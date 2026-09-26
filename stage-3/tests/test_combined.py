"""WO 2.2: combined tables in the model, availability, bookings, amendments and moves."""

import copy
import unittest

from support import FIXTURE, FUTURE, ServiceTest, fixture

COMBO = {
    "id": "r_combo", "name": "Tafelrunde", "timezone": "Europe/Berlin",
    "slot_minutes": 30, "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 60,
    "opening_hours": [{"weekday": "thu", "opens": "18:00", "closes": "23:00"}],
    "tables": [{"id": "t_1", "label": "1", "capacity": 2},
               {"id": "t_2", "label": "2", "capacity": 4},
               {"id": "t_3", "label": "3", "capacity": 4}],
    "combinable": [["t_2", "t_1"], ["t_2", "t_3"]],
}


def combo_fixture(**changes):
    return fixture(restaurants=FIXTURE["restaurants"] + [COMBO], **changes)


def seed(ref, **fields):
    base = {"id": f"res_{ref}", "reference": ref, "user_id": "u_bob", "restaurant_id": "r_combo",
            "starts_at_local": f"{FUTURE}T19:00", "party_size": 2}
    base.update(fields)
    return base


class CombinedTest(ServiceTest):
    def setUp(self):
        self.reset(combo_fixture())
        self.ada = self.login()

    def book_combo(self, key="c", **fields):
        body = {"restaurant_id": "r_combo", "table_ids": ["t_1", "t_2"],
                "starts_at_local": f"{FUTURE}T19:00", "party_size": 6}
        body.update(fields)
        return self.client.post("/reservations", body, token=self.ada, key=key)

    def options(self, party, time="19:00"):
        body = self.client.get(f"/availability?restaurant_id=r_combo&date={FUTURE}"
                               f"&party_size={party}")[1]
        slot = next(s for s in body["slots"] if s["starts_at_local"].endswith(time))
        return slot["available_table_ids"], slot["available_options"]

    def test_detail_carries_combinable(self):
        body = self.client.get("/restaurants/r_combo")[1]
        self.assertEqual(body["combinable"], [["t_2", "t_1"], ["t_2", "t_3"]])

    def test_availability_options(self):
        singles, options = self.options(2)
        self.assertEqual(singles, ["t_1", "t_2", "t_3"])
        self.assertEqual(options, [{"table_ids": ["t_1"], "capacity": 2},
                                   {"table_ids": ["t_2"], "capacity": 4},
                                   {"table_ids": ["t_3"], "capacity": 4},
                                   {"table_ids": ["t_2", "t_1"], "capacity": 6},
                                   {"table_ids": ["t_2", "t_3"], "capacity": 8}])
        singles, options = self.options(7)
        self.assertEqual(singles, [])
        self.assertEqual(options, [{"table_ids": ["t_2", "t_3"], "capacity": 8}])

    def test_book_pair(self):
        status, body, _ = self.book_combo()
        self.assertEqual(status, 201, body)
        self.assertEqual(body["table_ids"], ["t_2", "t_1"])
        self.assertNotIn("table_id", body)
        singles, options = self.options(1)
        self.assertEqual(singles, ["t_3"])
        self.assertEqual(options, [{"table_ids": ["t_3"], "capacity": 4}])
        _, options = self.options(1, "20:30")
        self.assertEqual(len(options), 5)
        # Any member being taken blocks the pair and the single.
        self.assertError(self.book(self.ada, key="s", restaurant_id="r_combo", table_id="t_1",
                                   party_size=2), 409, "table_unavailable")
        self.assertError(self.book_combo(key="p2", table_ids=["t_2", "t_3"]), 409,
                         "table_unavailable")
        # Cancelling frees every table.
        self.client.post(f"/reservations/{body['reference']}/cancel", token=self.ada)
        self.assertEqual(len(self.options(1)[1]), 5)

    def test_errors(self):
        cases = [
            ({"table_id": "t_1"}, 422, "validation_failed"),
            ({"table_ids": "t_1"}, 400, "malformed_request"),
            ({"table_ids": ["t_1", 2]}, 400, "malformed_request"),
            ({"table_ids": []}, 422, "validation_failed"),
            ({"table_ids": ["t_1", "t_1"]}, 422, "validation_failed"),
            ({"table_ids": ["t_1", "t_2", "t_3"]}, 422, "combination_not_allowed"),
            ({"table_ids": ["t_1", "t_9"]}, 404, "not_found"),
            ({"restaurant_id": "nope"}, 404, "not_found"),
            ({"table_ids": ["t_1", "t_3"]}, 422, "combination_not_allowed"),
            ({"table_ids": ["t_1", "t_2"], "party_size": 7}, 422, "party_exceeds_capacity"),
            ({"starts_at_local": f"{FUTURE}T19:10"}, 422, "not_on_slot_grid"),
        ]
        for i, (fields, status, code) in enumerate(cases):
            with self.subTest(fields=fields):
                self.assertError(self.book_combo(key=f"e{i}", **fields), status, code)
        body = {"restaurant_id": "r_combo", "starts_at_local": f"{FUTURE}T19:00", "party_size": 2}
        self.assertError(self.client.post("/reservations", body, token=self.ada, key="none"), 422,
                         "validation_failed")

    def test_single_in_table_ids_and_input_order(self):
        status, body, _ = self.book_combo(key="one", table_ids=["t_3"], party_size=4)
        self.assertEqual(status, 201)
        self.assertEqual((body["table_id"], body["table_ids"]), ("t_3", ["t_3"]))
        status, body, _ = self.book_combo(key="rev", table_ids=["t_1", "t_2"],
                                          starts_at_local=f"{FUTURE}T21:00")
        self.assertEqual(body["table_ids"], ["t_2", "t_1"])

    def test_patch_table_ids(self):
        ref = self.book(self.ada, key="s", restaurant_id="r_combo", table_id="t_1",
                        party_size=2)[1]["reference"]
        path = f"/reservations/{ref}"
        self.assertError(self.client.patch(path, {"table_id": "t_1", "table_ids": ["t_1"]},
                                           token=self.ada), 422, "validation_failed")
        self.assertError(self.client.patch(path, {"table_ids": ["t_1", "t_3"]}, token=self.ada),
                         422, "combination_not_allowed")
        self.assertError(self.client.patch(path, {"table_ids": 5}, token=self.ada), 400,
                         "malformed_request")
        status, body, _ = self.client.patch(path, {"table_ids": ["t_1", "t_2"], "party_size": 5},
                                            token=self.ada)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["table_ids"], ["t_2", "t_1"])
        self.assertNotIn("table_id", body)
        self.assertEqual(self.client.patch(path, {"table_ids": ["t_2", "t_1"]},
                                           token=self.ada)[:2], (200, body))
        status, body, _ = self.client.patch(path, {"table_id": "t_3", "party_size": 4},
                                            token=self.ada)
        self.assertEqual((status, body["table_id"], body["table_ids"]), (200, "t_3", ["t_3"]))

    def test_moves_with_table_ids(self):
        a = self.book(self.ada, key="a", restaurant_id="r_combo", table_id="t_1",
                      party_size=2)[1]["reference"]
        b = self.book(self.ada, key="b", restaurant_id="r_combo", table_id="t_3",
                      party_size=2)[1]["reference"]
        moves = {"moves": [{"reference": a, "table_ids": ["t_1", "t_2"]}]}
        status, body, _ = self.client.post("/reservation-moves", moves, token=self.ada, key="m")
        self.assertEqual(status, 201, body)
        self.assertEqual(body["reservations"][0]["table_ids"], ["t_2", "t_1"])
        clash = {"moves": [{"reference": b, "table_ids": ["t_2", "t_3"]}]}
        self.assertError(self.client.post("/reservation-moves", clash, token=self.ada, key="n"),
                         409, "table_unavailable")
        swap = {"moves": [{"reference": a, "table_id": "t_3"},
                          {"reference": b, "table_ids": ["t_2", "t_1"]}]}
        status, body, _ = self.client.post("/reservation-moves", swap, token=self.ada, key="o")
        self.assertEqual(status, 201, body)
        both = {"moves": [{"reference": a, "table_id": "t_3", "table_ids": ["t_3"]}]}
        self.assertError(self.client.post("/reservation-moves", both, token=self.ada, key="p"),
                         422, "validation_failed")
        bad = {"moves": [{"reference": a, "table_ids": "t_3"}]}
        self.assertError(self.client.post("/reservation-moves", bad, token=self.ada, key="q"),
                         422, "validation_failed")

    def test_seeded_sets_and_status(self):
        self.reset(combo_fixture(reservations=[
            seed("PAIR0001", table_ids=["t_1", "t_2"], party_size=5),
            seed("GONE0001", table_id="t_3", status="cancelled"),
        ]))
        _, options = self.options(1)
        self.assertEqual(options, [{"table_ids": ["t_3"], "capacity": 4}])
        bob = self.login("bob@example.com", "battery staple")
        pair = self.client.get("/reservations/PAIR0001", token=bob)[1]
        self.assertEqual(pair["table_ids"], ["t_2", "t_1"])
        self.assertEqual(self.client.get("/reservations/GONE0001", token=bob)[1]["status"],
                         "cancelled")
        for bad in (seed("BAD00001", table_id="t_3", status="pending"),
                    seed("BAD00001", table_id="t_3", table_ids=["t_3"]),
                    seed("BAD00001", table_ids=["t_3", "t_3"]),
                    seed("BAD00001", table_ids=["t_9"])):
            self.assertError(self.client.post("/_test/reset", combo_fixture(reservations=[bad])),
                             422, "validation_failed")
        for pairs in ([["t_1"]], [["t_1", "t_1"]], [["t_1", "t_9"]], [["t_1", "t_2", "t_3"]]):
            bad = dict(COMBO, combinable=pairs)
            self.assertError(self.client.post("/_test/reset", fixture(restaurants=[bad])), 422,
                             "validation_failed")


class UpgradeTest(ServiceTest):
    """E13: exports of the stage-1 service import and keep working."""

    STAGE_1_EXPORT = {
        "track": "tablekeeper", "format_version": 1,
        "state": {
            "schema": 1,
            "users": [{"id": "u_1", "email": "ada@example.com", "display_name": "Ada",
                       "password_hash": None}],
            "tokens": [{"token": "tok-stage-1", "user_id": "u_1"}],
            "restaurants": [dict(FIXTURE["restaurants"][0])],
            "reservations": [{"id": "res_1", "reference": "OLDREF01", "user_id": "u_1",
                              "restaurant_id": "r_anker", "table_id": "t_2", "party_size": 4,
                              "status": "confirmed", "starts_at_local": f"{FUTURE}T19:00",
                              "created_at": "2026-09-01T10:00:00+00:00"}],
            "idempotency": [],
        },
    }

    def test_stage_1_export_migrates(self):
        from tablekeeper.passwords import hash_password
        doc = copy.deepcopy(self.STAGE_1_EXPORT)
        doc["state"]["users"][0]["password_hash"] = hash_password("correct horse")
        original = {"reservation_id": "res_1", "reference": "OLDREF01", "restaurant_id": "r_anker",
                    "table_id": "t_2", "party_size": 4, "status": "confirmed",
                    "starts_at_local": f"{FUTURE}T19:00",
                    "starts_at": f"{FUTURE}T19:00:00+02:00", "ends_at": f"{FUTURE}T20:30:00+02:00",
                    "created_at": "2026-09-01T10:00:00+00:00"}
        request = {"restaurant_id": "r_anker", "table_id": "t_2",
                   "starts_at_local": f"{FUTURE}T19:00", "party_size": 4}
        doc["state"]["idempotency"] = [{
            "user_id": "u_1", "key": "lost", "method": "POST", "path": "/reservations",
            "request": '{"party_size":4,"restaurant_id":"r_anker","starts_at_local":"'
                       + FUTURE + 'T19:00","table_id":"t_2"}',
            "status": 201, "response": original}]
        self.assertEqual(self.client.post("/_test/import", doc)[0], 204)
        token = "tok-stage-1"
        self.assertEqual(self.client.post("/reservations", request, token=token, key="lost")[:2],
                         (200, original))
        found = self.client.get("/reservations/OLDREF01", token=token)[1]
        self.assertEqual(found, dict(original, table_ids=["t_2"]))
        self.assertEqual(self.client.get("/restaurants/r_anker")[1]["combinable"], [])
        self.login()
        exported = self.client.get("/_test/export")[1]
        self.assertEqual(exported["state"]["schema"], 2)
        self.assertEqual(self.client.post("/_test/import", exported)[0], 204)
        legacy = copy.deepcopy(doc)
        legacy["state"]["reservations"][0]["table_ids"] = ["t_2"]
        del legacy["state"]["reservations"][0]["table_id"]
        self.assertError(self.client.post("/_test/import", legacy), 422, "validation_failed")


if __name__ == "__main__":
    unittest.main()
