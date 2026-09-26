"""WO 1.6: atomic reservation moves."""

import unittest

from support import FUTURE, ServiceTest, fixture

PAST_SEED = {"id": "res_past", "reference": "PAST0001", "user_id": "u_ada",
             "restaurant_id": "r_anker", "table_id": "t_3",
             "starts_at_local": "2026-01-02T19:00", "party_size": 2}


class MovesTest(ServiceTest):
    def setUp(self):
        self.reset(fixture(reservations=[PAST_SEED]))
        self.ada = self.login()
        self.bob = self.login("bob@example.com", "battery staple")
        self.a = self.book(self.ada, key="a", table_id="t_1", party_size=2)[1]["reference"]
        self.b = self.book(self.ada, key="b", table_id="t_2", party_size=2)[1]["reference"]

    def move(self, moves, key="m", token=None):
        return self.client.post("/reservation-moves", {"moves": moves}, token=token or self.ada,
                                key=key)

    def get(self, ref):
        return self.client.get(f"/reservations/{ref}", token=self.ada)[1]

    def test_swap(self):
        status, body, _ = self.move([{"reference": self.a, "table_id": "t_2"},
                                     {"reference": self.b, "table_id": "t_1"}])
        self.assertEqual(status, 201, body)
        self.assertEqual([r["reference"] for r in body["reservations"]], [self.a, self.b])
        self.assertEqual([r["table_id"] for r in body["reservations"]], ["t_2", "t_1"])
        self.assertEqual(self.get(self.a)["table_id"], "t_2")
        # Replay returns the original response even after a later change.
        self.client.post(f"/reservations/{self.a}/cancel", token=self.ada)
        self.assertEqual(self.move([{"reference": self.a, "table_id": "t_2"},
                                    {"reference": self.b, "table_id": "t_1"}])[:2], (200, body))
        self.assertError(self.move([{"reference": self.a}]), 409, "idempotency_key_reuse")

    def test_unchanged_items_are_returned(self):
        status, body, _ = self.move([{"reference": self.a}, {"reference": self.b,
                                                             "starts_at_local": f"{FUTURE}T21:00"}])
        self.assertEqual(status, 201)
        self.assertEqual(body["reservations"][0], self.get(self.a))
        self.assertEqual(body["reservations"][1]["starts_at_local"], f"{FUTURE}T21:00")

    def test_all_or_nothing(self):
        before = (self.get(self.a), self.get(self.b))
        self.assertError(self.move([{"reference": self.a, "table_id": "t_3"},
                                    {"reference": self.b, "party_size": 9}]), 422,
                         "party_exceeds_capacity")
        self.assertError(self.move([{"reference": self.a, "table_id": "t_2"},
                                    {"reference": self.b}], key="n"), 409, "table_unavailable")
        self.book(self.bob, key="x", table_id="t_3", starts_at_local=f"{FUTURE}T20:00")
        self.assertError(self.move([{"reference": self.a, "table_id": "t_3"}], key="o"), 409,
                         "table_unavailable")
        self.assertEqual((self.get(self.a), self.get(self.b)), before)
        # Failed keys remain reusable.
        self.assertEqual(self.move([{"reference": self.a}], key="n")[0], 201)

    def test_shape_errors(self):
        for moves in ([], [{"reference": self.a}] * 2, "x", [1], [{"table_id": "t_1"}],
                      [{"reference": 5}], [{"reference": self.a, "table_id": 1}],
                      [{"reference": self.a, "party_size": "2"}],
                      [{"reference": f"R{i}"} for i in range(9)]):
            with self.subTest(moves=moves):
                self.assertError(self.move(moves, key=f"s{moves!r}"[:200]), 422,
                                 "validation_failed")
        self.assertError(self.client.post("/reservation-moves", {}, token=self.ada, key="z"), 422,
                         "validation_failed")

    def test_precedence(self):
        self.assertError(self.client.post("/reservation-moves", {"moves": []}), 401,
                         "unauthenticated")
        self.assertError(self.client.post("/reservation-moves", {"moves": []}, token=self.ada),
                         400, "missing_idempotency_key")
        bob_ref = self.book(self.bob, key="bb", table_id="t_3")[1]["reference"]
        self.assertError(self.move([{"reference": self.a}, {"reference": bob_ref}]), 404,
                         "not_found")
        night = self.book(self.ada, key="n", restaurant_id="r_night", table_id="t_1",
                          starts_at_local="2030-09-29T01:00")[1]["reference"]
        self.assertError(self.move([{"reference": self.a}, {"reference": night}], key="m2"), 422,
                         "validation_failed")
        self.client.post(f"/reservations/{self.b}/cancel", token=self.ada)
        self.assertError(self.move([{"reference": self.a, "party_size": 99},
                                    {"reference": self.b}], key="m3"), 422,
                         "party_exceeds_capacity")
        self.assertError(self.move([{"reference": self.b}, {"reference": self.a,
                                                            "party_size": 99}], key="m4"), 409,
                         "reservation_cancelled")
        self.assertError(self.move([{"reference": "PAST0001"}], key="m5"), 409, "cutoff_passed")


if __name__ == "__main__":
    unittest.main()
