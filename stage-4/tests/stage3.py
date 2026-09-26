"""Shared fixture and helpers for the stage-3 tests."""

import copy

from support import FIXTURE, FUTURE, ServiceTest, fixture

MANAGED = [dict(FIXTURE["restaurants"][0], manager_user_ids=["u_bob"])] + FIXTURE["restaurants"][1:]

POLICY = {
    "effective_from": "2030-09-01",
    "slot_minutes": 30,
    "reservation_duration_minutes": 120,
    "cancellation_cutoff_minutes": 60,
    "opening_hours": [{"weekday": "thu", "opens": "17:00", "closes": "23:00"}],
    "capacities": {"t_1": 2, "t_2": 3, "t_3": 6},
}

BASE_TERMS = {
    "policy_version": 0, "slot_minutes": 30, "reservation_duration_minutes": 90,
    "cancellation_cutoff_minutes": 120,
    "opening_hours": [{"weekday": d, "opens": "18:00", "closes": "23:00"}
                      for d in ("mon", "tue", "wed", "thu", "fri", "sat")],
    "capacities": {"t_1": 2, "t_2": 4, "t_3": 6},
}


def policy(**changes):
    p = copy.deepcopy(POLICY)
    p.update(changes)
    return p


class Stage3Test(ServiceTest):
    seeds = []

    def setUp(self):
        self.reset(fixture(restaurants=copy.deepcopy(MANAGED), reservations=list(self.seeds)))
        self.ada = self.login()
        self.bob = self.login("bob@example.com", "battery staple")

    def publish(self, body, token=None, key="p1", restaurant="r_anker"):
        return self.client.post(f"/restaurants/{restaurant}/policies", body,
                                token=token or self.bob, key=key)

    def history(self, ref, token=None):
        return self.client.get(f"/reservations/{ref}/history", token=token or self.ada)

    def get(self, ref, token=None):
        return self.client.get(f"/reservations/{ref}", token=token or self.ada)[1]

    def patch(self, ref, body, token=None):
        return self.client.patch(f"/reservations/{ref}", body, token=token or self.ada)


__all__ = ["BASE_TERMS", "FUTURE", "MANAGED", "POLICY", "Stage3Test", "fixture", "policy"]
