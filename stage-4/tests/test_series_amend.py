"""WO 4.4: amending recurring reservations."""

import copy
import threading
import unittest

from stage3 import FUTURE, MANAGED, Stage3Test, fixture, policy
from test_replans import CLOSURE, PLAN_RESTAURANT


class SeriesAmendTest(Stage3Test):
    def setUp(self):
        self.reset(fixture(restaurants=copy.deepcopy(MANAGED) + [copy.deepcopy(PLAN_RESTAURANT)]))
        self.ada = self.login()
        self.bob = self.login("bob@example.com", "battery staple")
        anchor = self.book(self.ada, key="anchor")[1]
        self.series = self.client.post("/series", {"anchor_reference": anchor["reference"],
                                                   "count": 3, "interval_weeks": 1},
                                       token=self.ada, key="series")[1]
        self.sid = self.series["series_id"]
        self.refs = [o["reference"] for o in self.series["occurrences"]]

    def amend(self, body, key="am", token=None, sid=None):
        return self.client.post(f"/series/{sid or self.sid}/amend", body, token=token or self.ada,
                                key=key)

    def current(self):
        return self.client.get(f"/series/{self.sid}", token=self.ada)[1]

    def test_request_order_and_validation(self):
        body = {"expected_revision": 1, "from_index": 1, "local_time": "20:00"}
        self.assertError(self.client.post(f"/series/{self.sid}/amend", body, key="k"), 401,
                         "unauthenticated")
        self.assertError(self.client.post(f"/series/{self.sid}/amend", body, token=self.ada), 400,
                         "missing_idempotency_key")
        self.assertError(self.client.post(f"/series/{self.sid}/amend", raw=b"{", token=self.ada,
                                          key="k"), 400, "malformed_request")
        self.assertError(self.amend(body, sid="nope"), 404, "not_found")
        self.assertError(self.amend(body, token=self.bob), 404, "not_found")
        bad = [dict(body, expected_revision=v) for v in ("1", 0, True, None)] + \
              [dict(body, from_index=v) for v in (-1, 3, True, "1", 1.0)] + \
              [dict(body, local_time=v) for v in ("8:00", "24:00", "20:00:00", "20:60", 2000)] + \
              [{"from_index": 1, "local_time": "20:00"}]
        for i, b in enumerate(bad):
            with self.subTest(body=b):
                self.assertError(self.amend(b, key=f"v{i}"), 422, "validation_failed")
        self.assertError(self.amend(dict(body, expected_revision=2)), 409, "stale_revision")

    def test_amend_from_index(self):
        before = self.current()
        status, body, _ = self.amend({"expected_revision": 1, "from_index": 1,
                                      "local_time": "20:00", "note": "ignored"})
        self.assertEqual(status, 201, body)
        self.assertEqual(body, self.current())
        self.assertEqual(body["revision"], 2)
        occ = body["occurrences"]
        self.assertEqual(occ[0]["reservation"], before["occurrences"][0]["reservation"])
        self.assertEqual([o["reservation"]["starts_at_local"] for o in occ],
                         [f"{FUTURE}T19:00", "2030-10-03T20:00", "2030-10-10T20:00"])
        self.assertEqual([o["reservation"]["revision"] for o in occ], [1, 2, 2])
        self.assertEqual([o["exception"] for o in occ], [False, False, False])
        self.assertEqual([o["reference"] for o in occ], self.refs)
        entry = self.history(self.refs[1])[1]["entries"][-1]
        self.assertEqual((entry["event"], entry["changes"]), ("changed", [
            {"field": "starts_at_local", "from": "2030-10-03T19:00", "to": "2030-10-03T20:00"}]))
        # Replays return the original even after later edits.
        self.client.post(f"/reservations/{self.refs[2]}/cancel", token=self.ada)
        self.assertEqual(self.amend({"expected_revision": 1, "from_index": 1,
                                     "local_time": "20:00", "note": "ignored"})[:2], (200, body))

    def test_skips_exceptions_and_cancelled_and_uses_scheduled_dates(self):
        # Occurrence 1 becomes an exception on another day; occurrence 2 is cancelled.
        self.patch(self.refs[1], {"starts_at_local": "2030-10-04T19:00"})
        self.client.post(f"/reservations/{self.refs[2]}/cancel", token=self.ada)
        s = self.current()
        status, body, _ = self.amend({"expected_revision": s["revision"], "from_index": 0,
                                      "local_time": "18:00"})
        self.assertEqual(status, 201, body)
        occ = body["occurrences"]
        self.assertEqual([o["reservation"]["starts_at_local"] for o in occ],
                         [f"{FUTURE}T18:00", "2030-10-04T19:00", "2030-10-10T19:00"])
        self.assertEqual(body["revision"], s["revision"] + 1)

    def test_no_op_changes_nothing(self):
        status, body, _ = self.amend({"expected_revision": 1, "from_index": 0,
                                      "local_time": "19:00"})
        self.assertEqual((status, body["revision"]), (201, 1))
        self.assertEqual(body, self.series)

    def test_failures_change_nothing(self):
        self.book(self.bob, key="block", starts_at_local="2030-10-10T20:30", table_id="t_2")
        before = self.current()
        self.assertError(self.amend({"expected_revision": 1, "from_index": 0,
                                     "local_time": "20:00"}), 409, "table_unavailable")
        self.assertEqual(self.current(), before)
        # Non-occupancy errors win, in index order.
        self.assertError(self.amend({"expected_revision": 1, "from_index": 0,
                                     "local_time": "22:00"}, key="late"), 422,
                         "outside_opening_hours")
        self.assertError(self.amend({"expected_revision": 1, "from_index": 0,
                                     "local_time": "19:10"}, key="grid"), 422, "not_on_slot_grid")
        self.assertEqual(self.current()["revision"], 1)
        # Failed keys stay unused.
        self.assertEqual(self.amend({"expected_revision": 1, "from_index": 1,
                                     "local_time": "18:00"}, key="late")[0], 201)
        # Each occurrence validates against the policy of its own date.
        self.publish(policy(effective_from="2030-10-10", capacities={"t_1": 2, "t_2": 3, "t_3": 6}))
        self.assertError(self.amend({"expected_revision": 2, "from_index": 2,
                                     "local_time": "19:00"}, key="cap"), 422,
                         "party_exceeds_capacity")
        self.assertEqual(self.current()["revision"], 2)

    def test_concurrent_amendments(self):
        results = [None] * 6

        def run(i):
            results[i] = self.amend({"expected_revision": 1, "from_index": 1,
                                     "local_time": f"{17 + i % 3}:30" if i % 3 else "20:30"},
                                    key=f"c{i}")[0]
        threads = [threading.Thread(target=run, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results.count(201), 1, results)
        self.assertEqual(self.current()["revision"], 2)

    def test_closure_blocks_and_moved_occurrences_keep_tables(self):
        # Seat a series at the plan restaurant, move occurrence 0 by a plan, then amend.
        anchor = self.book(self.ada, key="pa", restaurant_id="r_plan", table_id="t_2",
                           party_size=4)[1]
        series = self.client.post("/series", {"anchor_reference": anchor["reference"], "count": 2,
                                              "interval_weeks": 1}, token=self.ada, key="ps")[1]
        plan = self.client.post("/restaurants/r_plan/replans", CLOSURE, token=self.bob,
                                key="r")[1]
        self.client.post(f"/restaurants/r_plan/replans/{plan['plan_id']}/apply", {},
                         token=self.bob, key="a")
        s = self.client.get(f"/series/{series['series_id']}", token=self.ada)[1]
        self.assertEqual(s["occurrences"][0]["reservation"]["table_ids"], ["t_3"])
        status, body, _ = self.client.post(f"/series/{series['series_id']}/amend",
                                           {"expected_revision": s["revision"], "from_index": 0,
                                            "local_time": "20:00"}, token=self.ada, key="pam")
        self.assertEqual(status, 201, body)
        self.assertEqual([o["reservation"]["table_ids"] for o in body["occurrences"]],
                         [["t_3"], ["t_2"]])
        # Moving the second occurrence into its own closure is refused.
        closure = dict(CLOSURE, **{"from": "2030-10-03T18:00:00+02:00",
                                   "to": "2030-10-03T19:00:00+02:00"})
        plan = self.client.post("/restaurants/r_plan/replans", closure, token=self.bob,
                                key="r2")[1]
        self.client.post(f"/restaurants/r_plan/replans/{plan['plan_id']}/apply", {},
                         token=self.bob, key="a2")
        s = self.client.get(f"/series/{series['series_id']}", token=self.ada)[1]
        self.assertError(self.client.post(f"/series/{series['series_id']}/amend",
                                          {"expected_revision": s["revision"], "from_index": 1,
                                           "local_time": "18:00"}, token=self.ada, key="pam2"),
                         409, "table_unavailable")


if __name__ == "__main__":
    unittest.main()
