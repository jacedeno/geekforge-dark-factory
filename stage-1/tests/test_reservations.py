"""WO 1.4: reservations, idempotency and concurrency."""

import re
import threading
import unittest

from support import FUTURE, ServiceTest, fixture

PAST_SEED = {"id": "res_past", "reference": "PAST0001", "user_id": "u_ada",
             "restaurant_id": "r_anker", "table_id": "t_1",
             "starts_at_local": "2026-01-02T19:00", "party_size": 2}


def parallel(n, fn):
    results = [None] * n

    def run(i):
        results[i] = fn(i)
    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


class ReservationTest(ServiceTest):
    def setUp(self):
        self.reset(fixture(reservations=[PAST_SEED]))
        self.ada = self.login()
        self.bob = self.login("bob@example.com", "battery staple")

    def test_create_shape(self):
        status, body, _ = self.book(self.ada)
        self.assertEqual(status, 201, body)
        self.assertEqual(set(body), {"reservation_id", "reference", "restaurant_id", "table_id",
                                     "party_size", "status", "starts_at_local", "starts_at",
                                     "ends_at", "created_at"})
        self.assertRegex(body["reference"], r"^[A-Z0-9]{6,12}$")
        self.assertLessEqual(len(body["reservation_id"]), 64)
        self.assertEqual(body["status"], "confirmed")
        self.assertEqual(body["starts_at"], f"{FUTURE}T19:00:00+02:00")
        self.assertEqual(body["ends_at"], f"{FUTURE}T20:30:00+02:00")
        self.assertTrue(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00", body["created_at"]))

    def test_half_open_intervals_and_past_starts(self):
        self.assertEqual(self.book(self.ada, key="a")[0], 201)
        self.assertEqual(self.book(self.ada, key="b", starts_at_local=f"{FUTURE}T20:30")[0], 201)
        self.assertEqual(self.book(self.ada, key="c", starts_at_local=f"{FUTURE}T17:30")[0], 422)
        self.assertError(self.book(self.bob, key="d", starts_at_local=f"{FUTURE}T20:00"), 409,
                         "table_unavailable")
        self.assertEqual(self.book(self.ada, key="e", starts_at_local="2026-01-02T20:00")[0], 201)

    def test_validation_order_and_codes(self):
        cases = [
            ({"party_size": None}, 422, "validation_failed"),
            ({"restaurant_id": 7}, 400, "malformed_request"),
            ({"starts_at_local": 7}, 400, "malformed_request"),
            ({"party_size": "4"}, 422, "validation_failed"),
            ({"party_size": True}, 422, "validation_failed"),
            ({"party_size": 2.5}, 422, "validation_failed"),
            ({"party_size": 0}, 422, "validation_failed"),
            ({"starts_at_local": f"{FUTURE}T19:00:00"}, 422, "validation_failed"),
            ({"starts_at_local": f"{FUTURE}T19:00+02:00"}, 422, "validation_failed"),
            ({"starts_at_local": "2030-02-30T19:00"}, 422, "validation_failed"),
            ({"starts_at_local": f"{FUTURE}T24:00"}, 422, "validation_failed"),
            ({"restaurant_id": "nope"}, 404, "not_found"),
            ({"table_id": "nope"}, 404, "not_found"),
            ({"restaurant_id": "r_night", "table_id": "t_9"}, 404, "not_found"),
            ({"starts_at_local": "2030-09-29T19:00"}, 422, "outside_opening_hours"),
            ({"starts_at_local": f"{FUTURE}T21:45"}, 422, "outside_opening_hours"),
            ({"starts_at_local": f"{FUTURE}T22:00"}, 422, "outside_opening_hours"),
            ({"starts_at_local": f"{FUTURE}T19:15"}, 422, "not_on_slot_grid"),
            ({"party_size": 5}, 422, "party_exceeds_capacity"),
        ]
        for i, (fields, status, code) in enumerate(cases):
            with self.subTest(fields=fields):
                if fields == {"party_size": None}:
                    body = {"restaurant_id": "r_anker", "table_id": "t_2",
                            "starts_at_local": f"{FUTURE}T19:00"}
                    result = self.client.post("/reservations", body, token=self.ada, key=f"v{i}")
                else:
                    result = self.book(self.ada, key=f"v{i}", **fields)
                self.assertError(result, status, code)

    def test_request_order_auth_key_body(self):
        self.assertError(self.client.post("/reservations", raw=b"{"), 401, "unauthenticated")
        self.assertError(self.client.post("/reservations", raw=b"{", token=self.ada), 400,
                         "missing_idempotency_key")
        self.assertError(self.client.post("/reservations", raw=b"{", token=self.ada, key=""), 400,
                         "missing_idempotency_key")
        self.assertError(self.client.post("/reservations", raw=b"{", token=self.ada,
                                          key="x" * 256), 422, "validation_failed")
        self.assertError(self.client.post("/reservations", raw=b"{", token=self.ada, key="x"),
                         400, "malformed_request")
        self.assertError(self.client.post("/reservations", raw=b"[]", token=self.ada, key="x"),
                         400, "malformed_request")
        self.assertEqual(self.book(self.ada, key="x" * 255)[0], 201)

    def test_idempotency(self):
        status, first, _ = self.book(self.ada, key="same")
        self.assertEqual(status, 201)
        # Replay with a different key order and whitespace is still the same JSON value.
        raw = (b'{ "party_size": 4, "starts_at_local": "' + FUTURE.encode() +
               b'T19:00", "table_id": "t_2",  "restaurant_id": "r_anker" }')
        status, replay, _ = self.client.post("/reservations", raw=raw, token=self.ada, key="same")
        self.assertEqual((status, replay), (200, first))
        self.assertError(self.book(self.ada, key="same", party_size=2), 409,
                         "idempotency_key_reuse")
        self.assertError(self.book(self.ada, key="same", party_size="bad"), 409,
                         "idempotency_key_reuse")
        # Replays survive cancellation and still return the original body.
        ref = first["reference"]
        self.assertEqual(self.client.post(f"/reservations/{ref}/cancel", token=self.ada)[0], 200)
        self.assertEqual(self.book(self.ada, key="same")[:2], (200, first))
        # Per-user scope and a failed key stays unused.
        self.assertEqual(self.book(self.bob, key="same")[0], 201)
        self.assertError(self.book(self.ada, key="fail", party_size=9), 422,
                         "party_exceeds_capacity")
        self.assertEqual(self.book(self.ada, key="fail", table_id="t_3", party_size=6)[0], 201)
        self.assertEqual(len(self.client.get("/reservations", token=self.ada)[1]["reservations"]),
                         3)

    def test_same_key_on_other_path_is_independent(self):
        status, first, _ = self.book(self.ada, key="shared")
        self.assertEqual(status, 201)
        status, body, _ = self.client.post("/reservation-moves",
                                           {"moves": [{"reference": first["reference"]}]},
                                           token=self.ada, key="shared")
        self.assertEqual(status, 201, body)

    def test_concurrent_identical_requests(self):
        results = parallel(30, lambda i: self.book(self.ada, key="race"))
        statuses = sorted(r[0] for r in results)
        self.assertEqual(statuses, [200] * 29 + [201])
        self.assertEqual(len({r[1]["reference"] for r in results}), 1)

    def test_concurrent_competing_bookings(self):
        results = parallel(30, lambda i: self.book(self.ada if i % 2 else self.bob, key=f"c{i}"))
        statuses = sorted(r[0] for r in results)
        self.assertEqual(statuses, [201] + [409] * 29)

    def test_list_get_and_privacy(self):
        a = self.book(self.ada, key="1")[1]
        b = self.book(self.ada, key="2", starts_at_local=f"{FUTURE}T21:00")[1]
        status, body, _ = self.client.get("/reservations", token=self.ada)
        self.assertEqual(status, 200)
        self.assertEqual([r["reference"] for r in body["reservations"]],
                         [b["reference"], a["reference"], "PAST0001"])
        self.assertEqual(self.client.get("/reservations", token=self.bob)[1],
                         {"reservations": []})
        self.assertEqual(self.client.get(f"/reservations/{a['reference']}", token=self.ada)[1], a)
        self.assertError(self.client.get(f"/reservations/{a['reference']}", token=self.bob), 404,
                         "not_found")
        self.assertError(self.client.get("/reservations/NOPE", token=self.ada), 404, "not_found")

    def test_cancel(self):
        a = self.book(self.ada)[1]
        path = f"/reservations/{a['reference']}/cancel"
        self.assertError(self.client.post(path), 401, "unauthenticated")
        self.assertError(self.client.post(path, token=self.bob), 404, "not_found")
        status, body, _ = self.client.post(path, token=self.ada)
        self.assertEqual(status, 200)
        self.assertEqual(body, dict(a, status="cancelled"))
        self.assertEqual(self.client.post(path, token=self.ada)[:2], (200, body))
        self.assertEqual(self.book(self.bob, key="again")[0], 201)
        self.assertError(self.client.post("/reservations/PAST0001/cancel", token=self.ada), 409,
                         "cutoff_passed")

    def test_patch(self):
        a = self.book(self.ada)[1]
        path = f"/reservations/{a['reference']}"
        self.assertError(self.client.patch(path, {"party_size": 2}), 401, "unauthenticated")
        self.assertError(self.client.patch(path, raw=b"{", token=self.ada), 400,
                         "malformed_request")
        self.assertError(self.client.patch(path, {}, token=self.bob), 404, "not_found")
        self.assertEqual(self.client.patch(path, {}, token=self.ada)[:2], (200, a))
        self.assertEqual(self.client.patch(path, {"party_size": 4, "restaurant_id": "x"},
                                           token=self.ada)[:2], (200, a))
        status, moved, _ = self.client.patch(path, {"table_id": "t_3",
                                                    "starts_at_local": f"{FUTURE}T20:00"},
                                             token=self.ada)
        self.assertEqual(status, 200)
        self.assertEqual((moved["reference"], moved["reservation_id"]),
                         (a["reference"], a["reservation_id"]))
        self.assertEqual((moved["table_id"], moved["ends_at"]),
                         ("t_3", f"{FUTURE}T21:30:00+02:00"))
        # The old slot is free again; a failing amendment changes nothing.
        other = self.book(self.bob, key="b")[1]
        self.assertError(self.client.patch(path, {"table_id": "t_2"}, token=self.ada), 409,
                         "table_unavailable")
        self.assertError(self.client.patch(path, {"party_size": 7}, token=self.ada), 422,
                         "party_exceeds_capacity")
        self.assertError(self.client.patch(path, {"table_id": 3}, token=self.ada), 400,
                         "malformed_request")
        self.assertError(self.client.patch(path, {"starts_at_local": f"{FUTURE}T19:10"},
                                           token=self.ada), 422, "not_on_slot_grid")
        self.assertEqual(self.client.get(path, token=self.ada)[1], moved)
        self.assertEqual(other["table_id"], "t_2")
        self.client.post(f"{path}/cancel", token=self.ada)
        self.assertError(self.client.patch(path, {"party_size": 2}, token=self.ada), 409,
                         "reservation_cancelled")
        self.assertError(self.client.patch("/reservations/PAST0001", {"party_size": 1},
                                           token=self.ada), 409, "cutoff_passed")


if __name__ == "__main__":
    unittest.main()
