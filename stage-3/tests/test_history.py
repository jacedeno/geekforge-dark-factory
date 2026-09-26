"""WO 3.3: reservation history, decision, combined-table history and moves under policies."""

import copy
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from stage3 import BASE_TERMS, FUTURE, MANAGED, POLICY, Stage3Test, fixture
from test_combined import COMBO

SEEDS = [
    {"id": "res_s1", "reference": "SEED0001", "user_id": "u_ada", "restaurant_id": "r_anker",
     "table_id": "t_3", "starts_at_local": f"{FUTURE}T21:00", "party_size": 5,
     "created_at": "2026-01-02T03:04:05Z"},
    {"id": "res_s2", "reference": "GONE0001", "user_id": "u_ada", "restaurant_id": "r_anker",
     "table_id": "t_3", "starts_at_local": f"{FUTURE}T18:00", "party_size": 2,
     "status": "cancelled", "created_at": "2026-01-02T03:04:05Z"},
]


class HistoryTest(Stage3Test):
    seeds = SEEDS

    def entries(self, ref):
        status, body, _ = self.history(ref)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["reference"], ref)
        return body["entries"]

    def test_created_changed_cancelled(self):
        ref = self.book(self.ada)[1]["reference"]
        self.assertEqual(self.book(self.ada)[0], 200)  # a replay records nothing
        self.patch(ref, {"table_id": "t_3"})
        self.patch(ref, {"table_id": "t_3", "party_size": 4})  # no-op: no entry
        self.patch(ref, {"starts_at_local": f"{FUTURE}T19:30", "party_size": 5})
        self.client.post(f"/reservations/{ref}/cancel", token=self.ada)
        self.client.post(f"/reservations/{ref}/cancel", token=self.ada)
        entries = self.entries(ref)
        self.assertEqual([e["seq"] for e in entries], [1, 2, 3, 4])
        self.assertEqual([e["event"] for e in entries], ["created", "changed", "changed",
                                                         "cancelled"])
        self.assertEqual([e["revision"] for e in entries], [1, 2, 3, 4])
        self.assertEqual(entries[0]["changes"], [
            {"field": "table_id", "from": None, "to": "t_2"},
            {"field": "starts_at_local", "from": None, "to": f"{FUTURE}T19:00"},
            {"field": "party_size", "from": None, "to": 4}])
        self.assertEqual(entries[1]["changes"], [{"field": "table_id", "from": "t_2", "to": "t_3"}])
        self.assertEqual(entries[2]["changes"], [
            {"field": "starts_at_local", "from": f"{FUTURE}T19:00", "to": f"{FUTURE}T19:30"},
            {"field": "party_size", "from": 4, "to": 5}])
        self.assertEqual(entries[3]["changes"], [])
        self.assertEqual(entries[0]["accepted_terms"], BASE_TERMS)
        ats = [e["at"] for e in entries]
        self.assertEqual(ats, sorted(ats))
        offset = datetime.now(ZoneInfo("Europe/Berlin")).isoformat()[-6:]
        self.assertTrue(all(a.endswith(offset) for a in ats), ats)

    def test_old_entries_keep_their_terms(self):
        ref = self.book(self.ada)[1]["reference"]
        self.publish(POLICY)
        self.patch(ref, {"party_size": 3})
        entries = self.entries(ref)
        self.assertEqual(entries[0]["accepted_terms"]["policy_version"], 0)
        self.assertEqual(entries[1]["accepted_terms"]["policy_version"], 1)

    def test_owner_only(self):
        ref = self.book(self.ada)[1]["reference"]
        for path in (f"/reservations/{ref}/history", f"/reservations/{ref}/decision"):
            self.assertError(self.client.get(path, token=self.bob), 404, "not_found")
            self.assertError(self.client.get(path), 404, "not_found")
            self.assertError(self.client.get(path, headers={"Authorization": "Bearer nope"}), 404,
                             "not_found")
            self.assertError(self.client.get(path.replace(ref, "NOPE0000"), token=self.ada), 404,
                             "not_found")
            self.assertEqual(self.client.get(path, token=self.ada)[0], 200)

    def test_seeded_bookings(self):
        entries = self.entries("SEED0001")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["at"], "2026-01-02T04:04:05+01:00")
        self.assertEqual((entries[0]["revision"], entries[0]["accepted_terms"]), (1, BASE_TERMS))
        gone = self.entries("GONE0001")
        self.assertEqual([(e["event"], e["revision"]) for e in gone],
                         [("created", 1), ("cancelled", 1)])
        self.assertEqual(self.get("GONE0001")["status"], "cancelled")
        self.assertEqual(self.get("SEED0001")["revision"], 1)


class CombinedHistoryTest(Stage3Test):
    def setUp(self):
        combo = dict(copy.deepcopy(COMBO), manager_user_ids=["u_bob"])
        self.reset(fixture(restaurants=copy.deepcopy(MANAGED) + [combo]))
        self.ada = self.login()
        self.bob = self.login("bob@example.com", "battery staple")

    def test_pair_history(self):
        body = {"restaurant_id": "r_combo", "table_ids": ["t_1", "t_2"],
                "starts_at_local": f"{FUTURE}T19:00", "party_size": 5}
        ref = self.client.post("/reservations", body, token=self.ada, key="c")[1]["reference"]
        self.assertEqual(self.patch(ref, {"table_ids": ["t_1", "t_2"]})[1]["revision"], 1)
        self.patch(ref, {"table_ids": ["t_2", "t_3"], "party_size": 6})
        self.patch(ref, {"table_id": "t_2", "party_size": 4})
        self.patch(ref, {"table_id": "t_3"})
        entries = self.history(ref)[1]["entries"]
        self.assertEqual([e["changes"][0] for e in entries], [
            {"field": "table_ids", "from": None, "to": ["t_2", "t_1"]},
            {"field": "table_ids", "from": ["t_2", "t_1"], "to": ["t_2", "t_3"]},
            {"field": "table_ids", "from": ["t_2", "t_3"], "to": ["t_2"]},
            {"field": "table_id", "from": "t_2", "to": "t_3"}])
        self.assertEqual(entries[1]["changes"][1], {"field": "party_size", "from": 5, "to": 6})


class MovesUnderPoliciesTest(Stage3Test):
    def test_moves_revisions_history_and_stale(self):
        a = self.book(self.ada, key="a", table_id="t_1", party_size=2)[1]["reference"]
        b = self.book(self.ada, key="b", table_id="t_2", party_size=2)[1]["reference"]
        move = lambda moves, key: self.client.post("/reservation-moves", {"moves": moves},
                                                   token=self.ada, key=key)
        self.assertError(move([{"reference": a, "table_id": "t_2", "expected_revision": 2},
                               {"reference": b, "table_id": "t_1"}], "m0"), 409, "stale_revision")
        self.assertError(move([{"reference": a, "expected_revision": "1"}], "m1"), 422,
                         "validation_failed")
        self.publish(POLICY)
        status, body, _ = move([{"reference": a, "table_id": "t_2", "expected_revision": 1},
                                {"reference": b, "table_id": "t_1"},
                                ], "m2")
        self.assertEqual(status, 201, body)
        self.assertEqual([r["revision"] for r in body["reservations"]], [2, 2])
        self.assertEqual([r["accepted_terms"]["policy_version"] for r in body["reservations"]],
                         [1, 1])
        self.assertEqual(body["reservations"][0]["ends_at"], f"{FUTURE}T21:00:00+02:00")
        entries = self.history(a)[1]["entries"]
        self.assertEqual(entries[-1]["changes"], [{"field": "table_id", "from": "t_1", "to": "t_2"}])
        # A no-op move keeps revision, terms and history.
        status, body, _ = move([{"reference": a}], "m3")
        self.assertEqual((status, body["reservations"][0]["revision"]), (201, 2))
        self.assertEqual(len(self.history(a)[1]["entries"]), 2)
        # A failed batch changes nothing.
        self.assertError(move([{"reference": a, "party_size": 3},
                               {"reference": b, "party_size": 9}], "m4"), 422,
                         "party_exceeds_capacity")
        self.assertEqual(self.get(a)["revision"], 2)


if __name__ == "__main__":
    unittest.main()
