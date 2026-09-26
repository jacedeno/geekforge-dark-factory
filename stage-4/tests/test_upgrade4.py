"""WO 4.5: stage-4 round trip, and stage-3 exports under replans and series amendment."""

import copy
import unittest

from stage3 import FUTURE, MANAGED, Stage3Test, fixture
from test_replans import CLOSURE, PLAN_RESTAURANT


class Upgrade4Test(Stage3Test):
    def setUp(self):
        self.reset(fixture(restaurants=copy.deepcopy(MANAGED) + [copy.deepcopy(PLAN_RESTAURANT)]))
        self.ada = self.login()
        self.bob = self.login("bob@example.com", "battery staple")

    def test_round_trip_keeps_closures_plans_and_receipts(self):
        anchor = self.book(self.ada, key="b", restaurant_id="r_plan", table_id="t_2",
                           party_size=4)[1]
        series = self.client.post("/series", {"anchor_reference": anchor["reference"], "count": 2,
                                              "interval_weeks": 1}, token=self.ada, key="s")[1]
        plan = self.client.post("/restaurants/r_plan/replans", CLOSURE, token=self.bob,
                                key="r")[1]
        applied = self.client.post(f"/restaurants/r_plan/replans/{plan['plan_id']}/apply", {},
                                   token=self.bob, key="a")[1]
        pending = self.client.post("/restaurants/r_plan/replans",
                                   dict(CLOSURE, table_id="t_4"), token=self.bob, key="r2")[1]
        before = self.client.get("/_test/export")[1]
        self.assertEqual(before["state"]["schema"], 4)

        self.reset({})
        self.assertEqual(self.client.post("/_test/import", before)[0], 204)
        self.assertEqual(self.client.get("/_test/export")[1], before)
        self.assertEqual(self.client.post(f"/restaurants/r_plan/replans/{plan['plan_id']}/apply",
                                          {}, token=self.bob, key="a")[:2], (200, applied))
        self.assertError(self.client.post(f"/restaurants/r_plan/replans/{plan['plan_id']}/apply",
                                          {}, token=self.bob, key="a3"), 409,
                         "plan_already_applied")
        # The closure still holds, and the stored plan can still be applied.
        self.assertError(self.book(self.ada, key="t2", restaurant_id="r_plan", table_id="t_2",
                                   party_size=2, starts_at_local=f"{FUTURE}T21:00"), 409,
                         "table_unavailable")
        status, second, _ = self.client.post(
            f"/restaurants/r_plan/replans/{pending['plan_id']}/apply", {}, token=self.bob, key="p")
        self.assertEqual((status, second["restaurant_revision"]), (201, applied["restaurant_revision"] + 1))
        s = self.client.get(f"/series/{series['series_id']}", token=self.ada)[1]
        amended = self.client.post(f"/series/{series['series_id']}/amend",
                                   {"expected_revision": s["revision"], "from_index": 0,
                                    "local_time": "18:00"}, token=self.ada, key="am")
        self.assertEqual(amended[0], 201, amended[1])

    def test_stage_3_series_derives_its_schedule(self):
        anchor = self.book(self.ada, key="b")[1]
        series = self.client.post("/series", {"anchor_reference": anchor["reference"], "count": 3,
                                              "interval_weeks": 2}, token=self.ada, key="s")[1]
        refs = [o["reference"] for o in series["occurrences"]]
        # The anchor becomes an exception on another day; the schedule still comes from index 1.
        self.client.patch(f"/reservations/{refs[0]}", {"starts_at_local": "2030-09-27T19:00"},
                          token=self.ada)
        doc = self.client.get("/_test/export")[1]
        doc["state"]["schema"] = 3
        for r in doc["state"]["restaurants"]:
            del r["closures"]
        for s in doc["state"]["series"]:
            del s["anchor_date"]
        del doc["state"]["plans"]
        self.reset({})
        self.assertEqual(self.client.post("/_test/import", doc)[0], 204)
        s = self.client.get(f"/series/{series['series_id']}", token=self.ada)[1]
        body = self.client.post(f"/series/{series['series_id']}/amend",
                                {"expected_revision": s["revision"], "from_index": 0,
                                 "local_time": "20:00"}, token=self.ada, key="am")[1]
        self.assertEqual([o["reservation"]["starts_at_local"] for o in body["occurrences"]],
                         ["2030-09-27T19:00", "2030-10-10T20:00", "2030-10-24T20:00"])


if __name__ == "__main__":
    unittest.main()
