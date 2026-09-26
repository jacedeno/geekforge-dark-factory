"""WO 4.2/4.3: restaurant revision, seating-plan preview and application, closures."""

import copy
import threading
import unittest

from stage3 import FUTURE, MANAGED, Stage3Test, fixture

PLAN_RESTAURANT = {
    "id": "r_plan", "name": "Planhaus", "timezone": "Europe/Berlin",
    "slot_minutes": 30, "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 60,
    "opening_hours": [{"weekday": "thu", "opens": "17:00", "closes": "23:00"}],
    "tables": [{"id": "t_1", "label": "1", "capacity": 2},
               {"id": "t_2", "label": "2", "capacity": 4},
               {"id": "t_3", "label": "3", "capacity": 4},
               {"id": "t_4", "label": "4", "capacity": 6},
               {"id": "t_5", "label": "5", "capacity": 4}],
    "combinable": [["t_1", "t_2"], ["t_1", "t_3"]],
    "manager_user_ids": ["u_bob"],
}
CLOSURE = {"table_id": "t_2", "from": f"{FUTURE}T18:00:00+02:00", "to": f"{FUTURE}T23:00:00+02:00"}


class ReplanTest(Stage3Test):
    def setUp(self):
        self.reset(fixture(restaurants=copy.deepcopy(MANAGED) + [copy.deepcopy(PLAN_RESTAURANT)]))
        self.ada = self.login()
        self.bob = self.login("bob@example.com", "battery staple")

    def reserve(self, table, party, time="19:00", key=None, token=None):
        status, body, _ = self.book(token or self.ada, key=key or f"{table}-{time}-{party}",
                                    restaurant_id="r_plan", table_id=table, party_size=party,
                                    starts_at_local=f"{FUTURE}T{time}")
        self.assertEqual(status, 201, body)
        return body

    def replan(self, body=None, key="r", token=None, restaurant="r_plan"):
        return self.client.post(f"/restaurants/{restaurant}/replans",
                                CLOSURE if body is None else body, token=token or self.bob, key=key)

    def apply(self, plan_id, key="a", token=None, restaurant="r_plan", body=None):
        return self.client.post(f"/restaurants/{restaurant}/replans/{plan_id}/apply",
                                {} if body is None else body, token=token or self.bob, key=key)

    def test_request_order_and_validation(self):
        self.assertError(self.client.post("/restaurants/r_plan/replans", CLOSURE, key="k"), 401,
                         "unauthenticated")
        self.assertError(self.replan(restaurant="nope"), 404, "not_found")
        self.assertError(self.replan(token=self.ada), 403, "forbidden")
        self.assertError(self.client.post("/restaurants/r_plan/replans", CLOSURE, token=self.bob),
                         400, "missing_idempotency_key")
        self.assertError(self.client.post("/restaurants/r_plan/replans", raw=b"[]",
                                          token=self.bob, key="k"), 400, "malformed_request")
        bad = [dict(CLOSURE, table_id=None), {"from": CLOSURE["from"], "to": CLOSURE["to"]},
               dict(CLOSURE, table_id=2), dict(CLOSURE, **{"from": f"{FUTURE}T18:00:00"}),
               dict(CLOSURE, **{"from": f"{FUTURE} 18:00"}), dict(CLOSURE, to=5),
               dict(CLOSURE, to=CLOSURE["from"]),
               dict(CLOSURE, **{"from": CLOSURE["to"], "to": CLOSURE["from"]})]
        for i, body in enumerate(bad):
            self.assertError(self.replan(body, key=f"b{i}"), 422, "validation_failed")
        self.assertError(self.replan(dict(CLOSURE, table_id="t_9"), key="t9"), 404, "not_found")
        status, body, _ = self.replan(dict(CLOSURE, **{"from": f"{FUTURE}T16:00:00Z"}), key="z")
        self.assertEqual(status, 201, body)
        self.assertEqual(body["closure"]["from"], f"{FUTURE}T16:00:00Z")

    def test_preview_minimises_moves_then_seats_then_ranks(self):
        a = self.reserve("t_2", 4)  # on the closed table
        b = self.reserve("t_3", 2)
        status, plan, _ = self.replan()
        self.assertEqual(status, 201, plan)
        self.assertEqual(plan["restaurant_revision"], 2)
        self.assertEqual(plan["closure"], CLOSURE)
        by_ref = {x["reference"]: x for x in plan["assignments"]}
        self.assertEqual([x["reference"] for x in plan["assignments"]],
                         sorted([a["reference"], b["reference"]]))
        # One move beats two: A goes to t_5 (rank 4) or t_4; t_5 wastes no seats.
        self.assertEqual(by_ref[a["reference"]], {"reference": a["reference"],
                                                  "table_ids": ["t_5"], "changed": True})
        self.assertEqual(by_ref[b["reference"]]["changed"], False)
        self.assertEqual((plan["moved_count"], plan["unused_seats"]), (1, 2))
        # The preview changed nothing.
        self.assertEqual(self.get(a["reference"]), a)
        self.assertEqual(len(self.history(a["reference"])[1]["entries"]), 1)
        again = self.replan(key="r2")[1]
        self.assertEqual(again["restaurant_revision"], 2)
        self.assertNotEqual(again["plan_id"], plan["plan_id"])
        self.assertEqual(self.replan()[:2], (200, plan))

    def test_rank_breaks_ties(self):
        a = self.reserve("t_2", 4)
        plan = self.replan()[1]
        # t_3 and t_5 both seat 4 exactly; t_3 comes first in fixture order.
        self.assertEqual(plan["assignments"][0]["table_ids"], ["t_3"])

    def test_pairs_and_no_feasible_plan(self):
        self.reserve("t_4", 6, key="big")
        self.reserve("t_3", 4, key="three")
        self.reserve("t_5", 4, key="five")
        status, pair, _ = self.client.post("/reservations", {
            "restaurant_id": "r_plan", "table_ids": ["t_1", "t_2"], "party_size": 5,
            "starts_at_local": f"{FUTURE}T19:00"}, token=self.ada, key="pair")
        self.assertEqual(status, 201, pair)
        status, body, _ = self.replan()
        self.assertError((status, body, "application/json; charset=utf-8"), 409, "no_feasible_plan")
        self.assertEqual(self.get(pair["reference"])["table_ids"], ["t_1", "t_2"])
        self.assertEqual(self.client.get("/restaurants/r_plan")[0], 200)

    def test_empty_plan_and_planning_limit(self):
        status, plan, _ = self.replan()
        self.assertEqual(status, 201)
        self.assertEqual((plan["assignments"], plan["moved_count"], plan["unused_seats"]),
                         ([], 0, 0))
        big = copy.deepcopy(PLAN_RESTAURANT)
        big["id"] = "r_big"
        big["tables"] += [{"id": "t_6", "label": "6", "capacity": 2},
                          {"id": "t_7", "label": "7", "capacity": 2}]
        self.reset(fixture(restaurants=[big]))
        bob = self.login("bob@example.com", "battery staple")
        self.assertError(self.client.post("/restaurants/r_big/replans", CLOSURE, token=bob,
                                          key="l"), 422, "planning_limit")

    def test_too_many_bookings_is_planning_limit(self):
        for i, t in enumerate(["t_1", "t_2", "t_3", "t_4", "t_5"]):
            self.reserve(t, 2, key=f"a{i}")
        for i, t in enumerate(["t_1", "t_2"]):
            self.reserve(t, 2, time="21:00", key=f"b{i}")
        self.assertError(self.replan(), 422, "planning_limit")

    def test_apply(self):
        a = self.reserve("t_2", 4)
        b = self.reserve("t_3", 2)
        plan = self.replan()[1]
        status, applied, _ = self.apply(plan["plan_id"])
        self.assertEqual(status, 201, applied)
        self.assertEqual(applied["restaurant_revision"], 3)
        self.assertEqual([r["reference"] for r in applied["reservations"]],
                         [x["reference"] for x in plan["assignments"]])
        moved = self.get(a["reference"])
        self.assertEqual((moved["table_ids"], moved["revision"]), (["t_5"], 2))
        for field in ("starts_at", "ends_at", "party_size", "accepted_terms", "created_at"):
            self.assertEqual(moved[field], a[field])
        entry = self.history(a["reference"])[1]["entries"][-1]
        self.assertEqual((entry["event"], entry["plan_id"], entry["revision"]),
                         ("reassigned", plan["plan_id"], 2))
        self.assertEqual(entry["changes"], [{"field": "table_ids", "from": ["t_2"], "to": ["t_5"]}])
        self.assertEqual(self.get(b["reference"]), b)
        # Replay, second application, closure effects.
        self.assertEqual(self.apply(plan["plan_id"])[:2], (200, applied))
        self.assertError(self.apply(plan["plan_id"], key="a2"), 409, "plan_already_applied")
        self.assertError(self.reserve_raw("t_2", "20:00"), 409, "table_unavailable")
        # Outside the closure interval the table is bookable again.
        self.assertEqual(self.book(self.ada, key="later", restaurant_id="r_plan", table_id="t_2",
                                   party_size=2, starts_at_local="2030-10-03T19:00")[0], 201)
        body = self.client.get(f"/availability?restaurant_id=r_plan&date={FUTURE}&party_size=1"
                               "&explain=true")[1]
        slot = next(s for s in body["slots"] if s["starts_at_local"].endswith("20:00"))
        self.assertNotIn("t_2", slot["available_table_ids"])
        self.assertNotIn(["t_1", "t_2"], [o["table_ids"] for o in slot["available_options"]])
        t2 = next(e for e in slot["explain"] if e["table_id"] == "t_2")
        self.assertEqual(t2["rules"][1], {"rule": "no_overlap", "holds": False})
        self.assertError(self.client.patch(f"/reservations/{b['reference']}", {"table_id": "t_2"},
                                           token=self.ada), 409, "table_unavailable")

    def reserve_raw(self, table, time, party=4):
        return self.book(self.ada, key=f"raw-{table}-{time}", restaurant_id="r_plan",
                         table_id=table, party_size=party, starts_at_local=f"{FUTURE}T{time}")

    def test_apply_errors(self):
        self.reserve("t_2", 4)
        plan = self.replan()[1]
        self.assertError(self.client.post(f"/restaurants/r_plan/replans/{plan['plan_id']}/apply",
                                          {}, key="k"), 401, "unauthenticated")
        self.assertError(self.apply(plan["plan_id"], token=self.ada), 403, "forbidden")
        self.assertError(self.apply(plan["plan_id"], restaurant="nope"), 404, "not_found")
        self.assertError(self.client.post(f"/restaurants/r_plan/replans/{plan['plan_id']}/apply",
                                          {}, token=self.bob), 400, "missing_idempotency_key")
        self.assertError(self.apply(plan["plan_id"], body=[]), 400, "malformed_request")
        self.assertError(self.apply("plan_nope"), 404, "not_found")
        self.assertError(self.apply(plan["plan_id"], restaurant="r_anker"), 404, "not_found")
        # A booking elsewhere does not invalidate; one here does.
        self.book(self.ada, key="elsewhere", table_id="t_1", party_size=2)
        self.reserve("t_1", 2, time="17:00", key="here")
        self.assertError(self.apply(plan["plan_id"], key="s"), 409, "stale_plan")
        fresh = self.replan(key="r2")[1]
        self.assertEqual(self.apply(fresh["plan_id"], key="s")[0], 201)

    def test_concurrent_applications(self):
        a = self.reserve("t_2", 4)
        plan = self.replan()[1]
        results = [None] * 8

        def run(i):
            results[i] = self.apply(plan["plan_id"], key=f"c{i}")[0]
        threads = [threading.Thread(target=run, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results), [201] + [409] * 7)
        self.assertEqual(self.get(a["reference"])["revision"], 2)

    def test_series_members_move_without_exception(self):
        anchor = self.reserve("t_2", 4)
        series = self.client.post("/series", {"anchor_reference": anchor["reference"], "count": 2,
                                              "interval_weeks": 1}, token=self.ada, key="s")[1]
        plan = self.replan()[1]
        self.apply(plan["plan_id"])
        s = self.client.get(f"/series/{series['series_id']}", token=self.ada)[1]
        self.assertEqual(s["revision"], 2)
        self.assertEqual([o["exception"] for o in s["occurrences"]], [False, False])
        self.assertEqual(s["occurrences"][0]["reservation"]["table_ids"], ["t_3"])

    def test_restaurant_revision_counts(self):
        def revision():
            return self.replan(key=f"rev{self.n}")[1]["restaurant_revision"]
        self.n = 0
        self.assertEqual(revision(), 0)
        a = self.reserve("t_1", 2, time="17:00")
        self.n += 1
        self.assertEqual(revision(), 1)
        self.client.patch(f"/reservations/{a['reference']}", {"party_size": 2}, token=self.ada)
        self.book(self.ada, key=f"t_1-17:00-2", restaurant_id="r_plan", table_id="t_1",
                  party_size=2, starts_at_local=f"{FUTURE}T17:00")  # replay
        self.assertError(self.reserve_raw("t_1", "17:30", party=2), 409, "table_unavailable")
        self.n += 1
        self.assertEqual(revision(), 1)
        self.client.patch(f"/reservations/{a['reference']}", {"party_size": 1}, token=self.ada)
        self.client.post(f"/reservations/{a['reference']}/cancel", token=self.ada)
        self.n += 1
        self.assertEqual(revision(), 3)


if __name__ == "__main__":
    unittest.main()
