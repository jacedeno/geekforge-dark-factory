"""WO 3.4: recurring reservations."""

import copy
import unittest

from stage3 import FUTURE, MANAGED, POLICY, Stage3Test, fixture, policy

WIDE = {"t_1": 2, "t_2": 4, "t_3": 6}
NIGHT_EVERY_DAY = [{"weekday": d, "opens": "00:00", "closes": "06:00"}
                   for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")]


class SeriesTest(Stage3Test):
    seeds = [{"id": "res_p", "reference": "PAST0001", "user_id": "u_ada",
              "restaurant_id": "r_anker", "table_id": "t_1",
              "starts_at_local": "2026-01-01T19:00", "party_size": 2}]

    def adopt(self, anchor, count=3, interval=1, key="s", token=None, **extra):
        body = {"anchor_reference": anchor, "count": count, "interval_weeks": interval}
        body.update(extra)
        return self.client.post("/series", body, token=token or self.ada, key=key)

    def reservations_count(self):
        return len(self.client.get("/reservations", token=self.ada)[1]["reservations"])

    def test_adopt_and_read(self):
        anchor = self.book(self.ada)[1]
        status, body, _ = self.adopt(anchor["reference"], count=3, interval=2, note="ignored")
        self.assertEqual(status, 201, body)
        self.assertEqual((body["revision"], body["interval_weeks"]), (1, 2))
        occ = body["occurrences"]
        self.assertEqual([o["index"] for o in occ], [0, 1, 2])
        self.assertEqual(occ[0]["reservation"], anchor)
        self.assertEqual(occ[0]["reference"], anchor["reference"])
        self.assertEqual([o["reservation"]["starts_at_local"] for o in occ],
                         [f"{FUTURE}T19:00", "2030-10-10T19:00", "2030-10-24T19:00"])
        self.assertEqual(occ[2]["reservation"]["starts_at"], "2030-10-24T19:00:00+02:00")
        self.assertEqual(len({o["reference"] for o in occ}), 3)
        self.assertFalse(any(o["exception"] for o in occ))
        for o in occ[1:]:
            r = o["reservation"]
            self.assertEqual((r["revision"], r["table_id"], r["party_size"]), (1, "t_2", 4))
            self.assertEqual(self.history(o["reference"])[1]["entries"][0]["event"], "created")
        self.assertEqual(self.reservations_count(), 4)  # plus the seeded past booking
        self.assertEqual(self.client.get(f"/series/{body['series_id']}", token=self.ada)[:2],
                         (200, body))
        for token in (self.bob, None):
            self.assertError(self.client.get(f"/series/{body['series_id']}", token=token), 404,
                             "not_found")
        self.assertError(self.client.get("/series/nope", token=self.ada), 404, "not_found")
        # Occupancy: the generated occurrence blocks its table.
        self.assertError(self.book(self.bob, key="x", starts_at_local="2030-10-10T19:30"), 409,
                         "table_unavailable")

    def test_validation_and_errors(self):
        anchor = self.book(self.ada)[1]["reference"]
        self.assertError(self.client.post("/series", {"anchor_reference": anchor, "count": 2,
                                                      "interval_weeks": 1}), 401, "unauthenticated")
        self.assertError(self.client.post("/series", {"anchor_reference": anchor, "count": 2,
                                                      "interval_weeks": 1}, token=self.ada), 400,
                         "missing_idempotency_key")
        for i, (count, interval) in enumerate([(1, 1), (13, 1), (True, 1), ("3", 1), (2, 0),
                                               (2, 5), (2, True), (2.0, 1), (None, 1)]):
            self.assertError(self.adopt(anchor, count, interval, key=f"v{i}"), 422,
                             "validation_failed")
        self.assertError(self.client.post("/series", {"count": 2, "interval_weeks": 1},
                                          token=self.ada, key="v9"), 422, "validation_failed")
        self.assertError(self.adopt("NOPE0000", key="e1"), 404, "not_found")
        bobs = self.book(self.bob, key="bb", table_id="t_3")[1]["reference"]
        self.assertError(self.adopt(bobs, key="e2"), 404, "not_found")
        self.assertError(self.adopt("PAST0001", key="e3"), 409, "cutoff_passed")
        cancelled = self.book(self.ada, key="cc", table_id="t_1", party_size=2)[1]["reference"]
        self.client.post(f"/reservations/{cancelled}/cancel", token=self.ada)
        self.assertError(self.adopt(cancelled, key="e4"), 409, "reservation_cancelled")
        body = self.adopt(anchor, key="ok")[1]
        self.assertError(self.adopt(anchor, key="e5"), 409, "already_in_series")
        self.assertError(self.adopt(body["occurrences"][1]["reference"], key="e6"), 409,
                         "already_in_series")

    def test_all_or_nothing(self):
        anchor = self.book(self.ada)[1]["reference"]
        self.book(self.bob, key="block", starts_at_local="2030-10-10T19:30")
        before = self.reservations_count()
        self.assertError(self.adopt(anchor, count=3, key="f"), 409, "table_unavailable")
        self.assertEqual(self.reservations_count(), before)
        self.assertEqual(self.get(anchor)["revision"], 1)
        # A policy closing the day of occurrence 1 wins in index order over the conflict later.
        self.publish(policy(effective_from="2030-10-03", opening_hours=[]))
        self.assertError(self.adopt(anchor, count=3, key="f"), 422, "outside_opening_hours")
        self.publish(policy(effective_from="2030-10-04", capacities=WIDE), key="p2")
        # The failed key is unused: the same key succeeds with a different body.
        self.assertEqual(self.adopt(anchor, count=2, interval=4, key="f")[0], 201)

    def test_per_occurrence_policy(self):
        anchor = self.book(self.ada)[1]["reference"]
        self.publish(policy(effective_from="2030-10-10", capacities={"t_1": 2, "t_2": 3, "t_3": 6}))
        self.assertError(self.adopt(anchor, count=3, key="a"), 422, "party_exceeds_capacity")
        self.publish(policy(effective_from="2030-10-10", reservation_duration_minutes=150,
                            capacities=WIDE), key="p2")
        body = self.adopt(anchor, count=3, key="b")[1]
        terms = [o["reservation"]["accepted_terms"]["policy_version"] for o in body["occurrences"]]
        self.assertEqual(terms, [0, 0, 2])
        self.assertEqual(body["occurrences"][2]["reservation"]["ends_at"],
                         "2030-10-10T21:30:00+02:00")

    def test_exceptions_revisions_and_replay(self):
        anchor = self.book(self.ada)[1]["reference"]
        original = self.adopt(anchor, count=4)[1]
        sid = original["series_id"]
        refs = [o["reference"] for o in original["occurrences"]]
        series = lambda: self.client.get(f"/series/{sid}", token=self.ada)[1]
        self.patch(refs[1], {"party_size": 4})  # no-op
        self.assertEqual(series()["revision"], 1)
        self.patch(refs[1], {"party_size": 3})
        self.assertEqual((series()["revision"], series()["occurrences"][1]["exception"]), (2, True))
        self.assertError(self.patch(refs[1], {"party_size": 9}), 422, "party_exceeds_capacity")
        self.assertEqual(series()["revision"], 2)
        self.client.post(f"/reservations/{refs[2]}/cancel", token=self.ada)
        self.client.post(f"/reservations/{refs[2]}/cancel", token=self.ada)
        s = series()
        self.assertEqual((s["revision"], s["occurrences"][2]["exception"]), (3, False))
        self.assertEqual(s["occurrences"][2]["reservation"]["status"], "cancelled")
        # Cancelling the anchor leaves the siblings confirmed.
        self.client.post(f"/reservations/{refs[0]}/cancel", token=self.ada)
        self.assertEqual(series()["occurrences"][3]["reservation"]["status"], "confirmed")
        moves = {"moves": [{"reference": refs[1], "table_id": "t_3"},
                           {"reference": refs[3], "table_id": "t_3"}]}
        self.assertEqual(self.client.post("/reservation-moves", moves, token=self.ada,
                                          key="m")[0], 201)
        s = series()
        self.assertEqual(s["revision"], 5)
        self.assertEqual([o["exception"] for o in s["occurrences"]], [False, True, False, True])
        # Replays return the original response and change nothing.
        self.assertEqual(self.adopt(anchor, count=4)[:2], (200, original))
        self.assertEqual(series()["revision"], 5)

    def test_dst_rules(self):
        night = dict(copy.deepcopy(MANAGED[1]), opening_hours=NIGHT_EVERY_DAY)
        self.reset(fixture(restaurants=[copy.deepcopy(MANAGED[0]), night]))
        ada = self.login()

        def book(local, key):
            return self.book(ada, key=key, restaurant_id="r_night", table_id="t_1",
                             starts_at_local=local, party_size=2)[1]["reference"]
        spring = book("2027-03-21T02:30", "a")
        self.assertError(self.adopt(spring, count=2, key="s1", token=ada), 422,
                         "invalid_local_time")
        autumn = book("2027-10-24T02:30", "b")
        body = self.adopt(autumn, count=2, key="s2", token=ada)[1]
        second = body["occurrences"][1]["reservation"]
        self.assertEqual(second["starts_at"], "2027-10-31T02:30:00+02:00")
        self.assertEqual(second["ends_at"], "2027-10-31T03:00:00+01:00")


if __name__ == "__main__":
    unittest.main()
