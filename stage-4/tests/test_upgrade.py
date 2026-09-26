"""WO 3.5: imports of stage-1 and stage-2 exports, and the stage-3 round trip."""

import copy
import json
import unittest

from stage3 import BASE_TERMS, FUTURE, POLICY, Stage3Test
from support import FIXTURE


def stage_2_export(password_hash):
    """An export in the stage-2 service's format (state schema 2)."""
    response = {"reservation_id": "res_1", "reference": "OLDREF01", "restaurant_id": "r_anker",
                "table_ids": ["t_2"], "table_id": "t_2", "party_size": 4, "status": "confirmed",
                "starts_at_local": f"{FUTURE}T19:00", "starts_at": f"{FUTURE}T19:00:00+02:00",
                "ends_at": f"{FUTURE}T20:30:00+02:00", "created_at": "2026-09-01T10:00:00+00:00"}
    request = {"restaurant_id": "r_anker", "table_id": "t_2",
               "starts_at_local": f"{FUTURE}T19:00", "party_size": 4}
    restaurant = dict(copy.deepcopy(FIXTURE["restaurants"][0]), combinable=[["t_1", "t_2"]])
    return {
        "track": "tablekeeper", "format_version": 1,
        "state": {
            "schema": 2,
            "users": [{"id": "u_1", "email": "ada@example.com", "display_name": "Ada",
                       "password_hash": password_hash}],
            "tokens": [{"token": "tok-old", "user_id": "u_1"}],
            "restaurants": [restaurant],
            "reservations": [
                {"id": "res_1", "reference": "OLDREF01", "user_id": "u_1",
                 "restaurant_id": "r_anker", "table_ids": ["t_2"], "party_size": 4,
                 "status": "confirmed", "starts_at_local": f"{FUTURE}T19:00",
                 "created_at": "2026-09-01T10:00:00+00:00"},
                {"id": "res_2", "reference": "OLDREF02", "user_id": "u_1",
                 "restaurant_id": "r_anker", "table_ids": ["t_1", "t_2"], "party_size": 5,
                 "status": "cancelled", "starts_at_local": f"{FUTURE}T21:00",
                 "created_at": "2026-09-02T10:00:00+00:00"}],
            "idempotency": [{"user_id": "u_1", "key": "lost", "method": "POST",
                             "path": "/reservations",
                             "request": json.dumps(request, sort_keys=True, separators=(",", ":")),
                             "status": 201, "response": response}],
        },
    }, request, response


class UpgradeTest(Stage3Test):
    def test_stage_2_export_migrates(self):
        from tablekeeper.passwords import hash_password
        doc, request, response = stage_2_export(hash_password("correct horse"))
        self.assertEqual(self.client.post("/_test/import", doc)[0], 204)
        token = "tok-old"
        self.assertEqual(self.client.post("/reservations", request, token=token, key="lost")[:2],
                         (200, response))
        found = self.client.get("/reservations/OLDREF01", token=token)[1]
        self.assertEqual((found["revision"], found["accepted_terms"]), (1, BASE_TERMS))
        entries = self.client.get("/reservations/OLDREF01/history", token=token)[1]["entries"]
        self.assertEqual([(e["event"], e["revision"]) for e in entries], [("created", 1)])
        self.assertEqual(entries[0]["at"], "2026-09-01T12:00:00+02:00")
        pair = self.client.get("/reservations/OLDREF02/history", token=token)[1]["entries"]
        self.assertEqual([(e["event"], e["revision"]) for e in pair],
                         [("created", 1), ("cancelled", 1)])
        self.assertEqual(pair[0]["changes"][0],
                         {"field": "table_ids", "from": None, "to": ["t_1", "t_2"]})
        self.assertEqual(self.client.get("/reservations/OLDREF01/decision", token=token)[1],
                         {"reference": "OLDREF01", "revision": 1, "accepted_terms": BASE_TERMS})
        status, series, _ = self.client.post("/series", {"anchor_reference": "OLDREF01",
                                                         "count": 2, "interval_weeks": 1},
                                             token=token, key="s")
        self.assertEqual(status, 201, series)
        self.assertEqual(series["occurrences"][0]["reservation"], found)
        self.login()

    def test_stage_3_round_trip(self):
        manager = self.bob
        self.client.post("/restaurants/r_anker/policies", POLICY, token=manager, key="p")
        ref = self.book(self.ada, key="b", table_id="t_3")[1]["reference"]
        self.client.patch(f"/reservations/{ref}", {"party_size": 3}, token=self.ada)
        series = self.client.post("/series", {"anchor_reference": ref, "count": 3,
                                              "interval_weeks": 1}, token=self.ada, key="s")[1]
        second = series["occurrences"][1]["reference"]
        self.client.patch(f"/reservations/{second}", {"party_size": 2}, token=self.ada)
        before = self.client.get("/_test/export")[1]
        self.assertEqual(before["state"]["schema"], 4)
        history = self.client.get(f"/reservations/{ref}/history", token=self.ada)[1]
        current = self.client.get(f"/series/{series['series_id']}", token=self.ada)[1]

        self.reset({})
        self.assertEqual(self.client.post("/_test/import", before)[0], 204)
        self.assertEqual(self.client.get("/_test/export")[1], before)
        self.assertEqual(self.client.get(f"/reservations/{ref}/history", token=self.ada)[1],
                         history)
        self.assertEqual(self.client.get(f"/series/{series['series_id']}", token=self.ada)[1],
                         current)
        self.assertEqual(self.client.get("/restaurants/r_anker/policies")[1]["policies"][0]
                         ["policy_version"], 1)
        # Receipts replay, the manager can still publish, versions continue.
        self.assertEqual(self.client.post("/series", {"anchor_reference": ref, "count": 3,
                                                      "interval_weeks": 1}, token=self.ada,
                                          key="s")[:2], (200, series))
        again = self.client.post("/restaurants/r_anker/policies", dict(POLICY, slot_minutes=15),
                                 token=manager, key="p2")
        self.assertEqual((again[0], again[1]["policy_version"]), (201, 2))
        self.assertError(self.client.post("/series", {"anchor_reference": second, "count": 2,
                                                      "interval_weeks": 1}, token=self.ada,
                                          key="s2"), 409, "already_in_series")

    def test_invalid_stage_3_import_changes_nothing(self):
        good = self.client.get("/_test/export")[1]
        bad = copy.deepcopy(good)
        bad["state"]["restaurants"][0]["policies"] = [dict(POLICY, policy_version=7)]
        self.assertError(self.client.post("/_test/import", bad), 422, "validation_failed")
        self.assertEqual(self.client.get("/_test/export")[1], good)


if __name__ == "__main__":
    unittest.main()
