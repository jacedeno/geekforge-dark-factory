"""WO 1.3: availability, time zones and daylight-saving transitions."""

import unittest

from support import FUTURE, ServiceTest


def query(restaurant, date, party):
    return f"/availability?restaurant_id={restaurant}&date={date}&party_size={party}"


class AvailabilityTest(ServiceTest):
    def setUp(self):
        self.reset()

    def slots(self, restaurant, date, party=2):
        status, body, _ = self.client.get(query(restaurant, date, party))
        self.assertEqual(status, 200, body)
        return body

    def test_shape_and_slot_rule(self):
        body = self.slots("r_anker", "2026-09-24", 4)
        self.assertEqual(body["restaurant_id"], "r_anker")
        self.assertEqual(body["date"], "2026-09-24")
        self.assertEqual(body["timezone"], "Europe/Berlin")
        starts = [s["starts_at_local"] for s in body["slots"]]
        # 18:00 .. 21:30: a 90-minute booking must end by 23:00.
        self.assertEqual(starts, [f"2026-09-24T{h}" for h in (
            "18:00", "18:30", "19:00", "19:30", "20:00", "20:30", "21:00", "21:30")])
        self.assertEqual(body["slots"][0], {"starts_at_local": "2026-09-24T18:00",
                                            "starts_at": "2026-09-24T18:00:00+02:00",
                                            "available_table_ids": ["t_2", "t_3"]})

    def test_closed_day_and_empty_lists(self):
        self.assertEqual(self.slots("r_anker", "2026-09-27")["slots"], [])
        body = self.slots("r_anker", FUTURE, 7)
        self.assertTrue(body["slots"])
        self.assertTrue(all(s["available_table_ids"] == [] for s in body["slots"]))

    def test_booking_removes_table_for_overlapping_slots(self):
        token = self.login()
        self.assertEqual(self.book(token)[0], 201)
        body = self.slots("r_anker", FUTURE, 4)
        tables = {s["starts_at_local"][-5:]: s["available_table_ids"] for s in body["slots"]}
        self.assertEqual(tables["18:00"], ["t_3"])
        self.assertEqual(tables["18:30"], ["t_3"])
        self.assertEqual(tables["20:00"], ["t_3"])
        self.assertEqual(tables["20:30"], ["t_2", "t_3"])

    def test_parameter_validation(self):
        for path in ("/availability?restaurant_id=r_anker&date=2030-09-26",
                     query("r_anker", "2030-02-30", 2), query("r_anker", "2030-9-26", 2),
                     query("r_anker", FUTURE, 0), query("r_anker", FUTURE, "4.0"),
                     query("r_anker", FUTURE, "%2B4"), query("r_anker", FUTURE, "1e9"),
                     query("r_anker", FUTURE, "-1")):
            self.assertError(self.client.get(path), 422, "validation_failed")
        self.assertError(self.client.get(query("nope", FUTURE, 2)), 404, "not_found")

    def test_berlin_spring_forward(self):
        body = self.slots("r_night", "2026-03-29")
        got = [(s["starts_at_local"][-5:], s["starts_at"][-6:]) for s in body["slots"]]
        self.assertEqual(got, [("00:00", "+01:00"), ("00:30", "+01:00"), ("01:00", "+01:00"),
                               ("01:30", "+01:00"), ("03:00", "+02:00"), ("03:30", "+02:00"),
                               ("04:00", "+02:00"), ("04:30", "+02:00")])
        token = self.login()
        self.assertError(self.book(token, restaurant_id="r_night", table_id="t_1",
                                   starts_at_local="2026-03-29T02:30"), 422, "invalid_local_time")
        status, res, _ = self.book(token, restaurant_id="r_night", table_id="t_1",
                                   starts_at_local="2026-03-29T01:30", key="k2")
        self.assertEqual(status, 201)
        self.assertEqual(res["starts_at"], "2026-03-29T01:30:00+01:00")
        self.assertEqual(res["ends_at"], "2026-03-29T04:00:00+02:00")

    def test_berlin_fall_back(self):
        body = self.slots("r_night", "2026-10-25")
        got = [(s["starts_at_local"][-5:], s["starts_at"][-6:]) for s in body["slots"]]
        self.assertEqual(got[:7], [("00:00", "+02:00"), ("00:30", "+02:00"), ("01:00", "+02:00"),
                                   ("01:30", "+02:00"), ("02:00", "+02:00"), ("02:30", "+02:00"),
                                   ("03:00", "+01:00")])
        self.assertEqual(got[-1], ("04:30", "+01:00"))
        token = self.login()
        status, res, _ = self.book(token, restaurant_id="r_night", table_id="t_1",
                                   starts_at_local="2026-10-25T01:30")
        self.assertEqual(status, 201)
        self.assertEqual(res["ends_at"], "2026-10-25T02:00:00+01:00")

    def test_new_york_transitions(self):
        spring = self.slots("r_ny", "2026-03-08")
        locals_ = [s["starts_at_local"][-5:] for s in spring["slots"]]
        self.assertNotIn("02:00", locals_)
        self.assertNotIn("02:30", locals_)
        self.assertEqual(spring["slots"][0]["starts_at"], "2026-03-08T00:00:00-05:00")
        fall = self.slots("r_ny", "2026-11-01")
        got = [(s["starts_at_local"][-5:], s["starts_at"][-6:]) for s in fall["slots"]]
        self.assertEqual(got[:5], [("00:00", "-04:00"), ("00:30", "-04:00"), ("01:00", "-04:00"),
                                   ("01:30", "-04:00"), ("02:00", "-05:00")])
        self.assertEqual(len(got), len({g[0] for g in got}))


if __name__ == "__main__":
    unittest.main()
