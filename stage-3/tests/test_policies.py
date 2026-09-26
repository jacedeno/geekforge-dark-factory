"""WO 3.2: policies, selection, explanations, accepted terms, revisions and amendments."""

import unittest

from stage3 import BASE_TERMS, FUTURE, POLICY, Stage3Test, policy


def avail(date=FUTURE, party=2, extra=""):
    return f"/availability?restaurant_id=r_anker&date={date}&party_size={party}{extra}"


class PolicyTest(Stage3Test):
    def test_publish_list_and_versions(self):
        status, body, _ = self.publish(dict(POLICY, note="ignored"))
        self.assertEqual(status, 201, body)
        self.assertEqual(body, dict(POLICY, policy_version=1))
        status, second, _ = self.publish(policy(effective_from="2030-01-01"), key="p2")
        self.assertEqual((status, second["policy_version"]), (201, 2))
        listed = self.client.get("/restaurants/r_anker/policies")
        self.assertEqual(listed[:2], (200, {"policies": [body, second]}))
        self.assertEqual(self.client.get("/restaurants/r_night/policies")[1], {"policies": []})
        self.assertError(self.client.get("/restaurants/nope/policies"), 404, "not_found")
        detail = self.client.get("/restaurants/r_anker")[1]
        self.assertEqual(detail["reservation_duration_minutes"], 90)
        self.assertNotIn("manager_user_ids", detail)

    def test_permissions_and_order(self):
        self.assertError(self.client.post("/restaurants/r_anker/policies", POLICY, key="k"), 401,
                         "unauthenticated")
        self.assertError(self.publish(POLICY, restaurant="nope"), 404, "not_found")
        self.assertError(self.publish(POLICY, token=self.ada), 403, "forbidden")
        self.assertError(self.client.post("/restaurants/r_anker/policies", POLICY,
                                          token=self.ada), 403, "forbidden")
        self.assertError(self.client.post("/restaurants/r_anker/policies", POLICY,
                                          token=self.bob), 400, "missing_idempotency_key")
        self.assertError(self.publish(POLICY, key="x" * 256), 422, "validation_failed")
        self.assertError(self.client.post("/restaurants/r_anker/policies", raw=b"[",
                                          token=self.bob, key="k"), 400, "malformed_request")

    def test_invalid_policies_allocate_nothing(self):
        bad = [
            {k: v for k, v in POLICY.items() if k != "capacities"},
            policy(effective_from="2030-02-30"), policy(effective_from=20300901),
            policy(slot_minutes=0), policy(slot_minutes=1441), policy(slot_minutes="30"),
            policy(slot_minutes=True), policy(reservation_duration_minutes=1441),
            policy(cancellation_cutoff_minutes=-1), policy(cancellation_cutoff_minutes=10081),
            policy(opening_hours=[{"weekday": "thu", "opens": "18:00", "closes": "23:00"},
                                  {"weekday": "thu", "opens": "10:00", "closes": "12:00"}]),
            policy(opening_hours=[{"weekday": "thu", "opens": "23:00", "closes": "18:00"}]),
            policy(opening_hours="thu"),
            policy(capacities={"t_1": 2, "t_2": 4}),
            policy(capacities={"t_1": 2, "t_2": 4, "t_3": 6, "t_9": 1}),
            policy(capacities={"t_1": 0, "t_2": 4, "t_3": 6}),
            policy(capacities={"t_1": 101, "t_2": 4, "t_3": 6}),
            policy(capacities={"t_1": True, "t_2": 4, "t_3": 6}),
        ]
        for i, body in enumerate(bad):
            with self.subTest(i=i):
                self.assertError(self.publish(body, key=f"bad{i}"), 422, "validation_failed")
        self.assertEqual(self.client.get("/restaurants/r_anker/policies")[1], {"policies": []})
        # A failed key stays unused; the next valid policy is version 1.
        self.assertEqual(self.publish(POLICY, key="bad0")[1]["policy_version"], 1)
        closed = self.publish(policy(opening_hours=[], effective_from="2031-01-01"), key="c")
        self.assertEqual(closed[0], 201)

    def test_replay_allocates_no_version(self):
        first = self.publish(POLICY)[1]
        self.assertEqual(self.publish(POLICY)[:2], (200, first))
        self.assertError(self.publish(policy(slot_minutes=15)), 409, "idempotency_key_reuse")
        self.assertEqual(self.publish(policy(slot_minutes=15), key="p2")[1]["policy_version"], 2)

    def test_selection_by_date_and_ties(self):
        self.publish(POLICY)
        # Later publication with an earlier date does not affect dates after the first policy.
        self.publish(policy(effective_from="2030-01-01", reservation_duration_minutes=60), key="p2")
        explained = self.client.get(avail(extra="&explain=true"))[1]
        self.assertEqual(explained["slots"][0]["starts_at_local"], f"{FUTURE}T17:00")
        self.assertEqual({e["policy_version"] for s in explained["slots"] for e in s["explain"]},
                         {1})
        before = self.client.get(avail(date="2029-09-27", extra="&explain=true"))[1]
        self.assertEqual(before["slots"][0]["explain"][0]["policy_version"], 0)
        # Same effective date: the greater version wins.
        self.publish(policy(slot_minutes=60), key="p3")
        starts = [s["starts_at_local"][-5:] for s in self.client.get(avail())[1]["slots"]]
        self.assertEqual(starts, ["17:00", "18:00", "19:00", "20:00", "21:00"])

    def test_explain(self):
        plain = self.client.get(avail(party=3))[1]
        self.assertNotIn("explain", plain["slots"][0])
        for value in ("false", "1", "", "True"):
            self.assertError(self.client.get(avail(extra=f"&explain={value}")), 422,
                             "validation_failed")
        self.book(self.ada, table_id="t_3", party_size=3)
        body = self.client.get(avail(party=3, extra="&explain=true"))[1]
        slot = next(s for s in body["slots"] if s["starts_at_local"].endswith("19:00"))
        self.assertEqual(slot["explain"], [
            {"table_id": "t_1", "policy_version": 0, "available": False,
             "rules": [{"rule": "capacity", "holds": False}, {"rule": "no_overlap", "holds": True}]},
            {"table_id": "t_2", "policy_version": 0, "available": True,
             "rules": [{"rule": "capacity", "holds": True}, {"rule": "no_overlap", "holds": True}]},
            {"table_id": "t_3", "policy_version": 0, "available": False,
             "rules": [{"rule": "capacity", "holds": True}, {"rule": "no_overlap", "holds": False}]},
        ])
        self.assertEqual(slot["available_table_ids"], ["t_2"])
        for s in body["slots"]:
            self.assertEqual([e["table_id"] for e in s["explain"] if e["available"]],
                             s["available_table_ids"])
        self.assertEqual(self.client.get(avail(date="2030-09-29", extra="&explain=true"))[1]["slots"],
                         [])
        full = self.client.get(avail(party=9, extra="&explain=true"))[1]["slots"][0]
        self.assertEqual(full["available_table_ids"], [])
        self.assertEqual(len(full["explain"]), 3)

    def test_bookings_accept_the_selected_policy(self):
        status, before, _ = self.book(self.ada, key="old")
        self.assertEqual((before["revision"], before["accepted_terms"]), (1, BASE_TERMS))
        self.publish(POLICY)
        # Publication does not change an accepted booking.
        self.assertEqual(self.get(before["reference"]), before)
        self.assertError(self.book(self.ada, key="big", starts_at_local=f"{FUTURE}T21:00",
                                   party_size=4), 422, "party_exceeds_capacity")
        status, after, _ = self.book(self.ada, key="new", table_id="t_3", party_size=6,
                                     starts_at_local=f"{FUTURE}T17:00")
        self.assertEqual(status, 201, after)
        self.assertEqual(after["accepted_terms"], dict(
            {k: v for k, v in POLICY.items() if k != "effective_from"}, policy_version=1))
        self.assertEqual(after["ends_at"], f"{FUTURE}T19:00:00+02:00")
        # The replay keeps the original terms.
        self.assertEqual(self.book(self.ada, key="old")[:2], (200, before))

    def test_amendments_revisions_and_terms(self):
        ref = self.book(self.ada)[1]["reference"]
        for value in ("1", 0, -1, True, 1.0):
            self.assertError(self.patch(ref, {"party_size": 3, "expected_revision": value}), 422,
                             "validation_failed")
        self.assertError(self.patch(ref, {"party_size": 3, "expected_revision": 2}), 409,
                         "stale_revision")
        # No-op: unchanged revision and no history entry.
        status, body, _ = self.patch(ref, {"party_size": 4, "table_id": "t_2",
                                           "expected_revision": 1})
        self.assertEqual((status, body["revision"]), (200, 1))
        self.assertEqual(len(self.history(ref)[1]["entries"]), 1)
        self.publish(POLICY)
        status, body, _ = self.patch(ref, {"party_size": 3, "expected_revision": 1})
        self.assertEqual((status, body["revision"]), (200, 2), body)
        self.assertEqual(body["accepted_terms"]["policy_version"], 1)
        self.assertEqual(body["ends_at"], f"{FUTURE}T21:00:00+02:00")
        self.assertError(self.patch(ref, {"party_size": 2, "expected_revision": 1}), 409,
                         "stale_revision")
        self.assertError(self.patch(ref, {"party_size": 4}), 422, "party_exceeds_capacity")
        self.assertEqual(self.get(ref)["revision"], 2)
        decision = self.client.get(f"/reservations/{ref}/decision", token=self.ada)[1]
        self.assertEqual(decision, {"reference": ref, "revision": 2,
                                    "accepted_terms": body["accepted_terms"]})
        status, cancelled, _ = self.client.post(f"/reservations/{ref}/cancel", token=self.ada)
        self.assertEqual((status, cancelled["revision"]), (200, 3))
        self.assertEqual(self.client.post(f"/reservations/{ref}/cancel",
                                          token=self.ada)[1]["revision"], 3)
        self.assertError(self.patch(ref, {"party_size": 2, "expected_revision": 1}), 409,
                         "stale_revision")
        self.assertError(self.patch(ref, {"party_size": 2}), 409, "reservation_cancelled")

    def test_noop_still_needs_an_editable_booking(self):
        self.reset(self.fixture_with_past())
        ada = self.login()
        self.assertError(self.client.patch("/reservations/PAST0001", {"party_size": 2}, token=ada),
                         409, "cutoff_passed")
        self.assertError(self.client.patch("/reservations/PAST0001", {}, token=ada), 409,
                         "cutoff_passed")

    @staticmethod
    def fixture_with_past():
        from stage3 import MANAGED, fixture
        return fixture(restaurants=MANAGED, reservations=[
            {"id": "res_p", "reference": "PAST0001", "user_id": "u_ada",
             "restaurant_id": "r_anker", "table_id": "t_1",
             "starts_at_local": "2026-01-02T19:00", "party_size": 2}])

    def test_cutoff_uses_accepted_terms(self):
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("Europe/Berlin"))
        soon = (now + timedelta(minutes=10)).replace(second=0, microsecond=0)
        if soon.date() != now.date() or soon.hour * 60 + soon.minute > 23 * 60:
            self.skipTest("too close to midnight in Berlin")
        every_day = [{"weekday": d, "opens": "00:00", "closes": "23:59"}
                     for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")]
        lenient = policy(effective_from=(now.date() - timedelta(days=1)).isoformat(),
                         slot_minutes=1, reservation_duration_minutes=30,
                         cancellation_cutoff_minutes=0, opening_hours=every_day)
        self.publish(lenient)
        local = soon.strftime("%Y-%m-%dT%H:%M")
        first = self.book(self.ada, key="a", starts_at_local=local, table_id="t_1", party_size=2)
        self.assertEqual(first[0], 201, first[1])
        # A stricter policy for the same date does not change the accepted cutoff.
        self.publish(dict(lenient, cancellation_cutoff_minutes=120), key="p2")
        second = self.book(self.ada, key="b", starts_at_local=local, table_id="t_2", party_size=2)
        self.assertEqual(second[1]["accepted_terms"]["cancellation_cutoff_minutes"], 120)
        self.assertError(self.client.post(f"/reservations/{second[1]['reference']}/cancel",
                                          token=self.ada), 409, "cutoff_passed")
        self.assertEqual(self.client.post(f"/reservations/{first[1]['reference']}/cancel",
                                          token=self.ada)[0], 200)

if __name__ == "__main__":
    unittest.main()
