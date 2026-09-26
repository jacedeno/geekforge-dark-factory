"""WO 1.1: health, reset, conventions, errors and restaurants."""

import unittest

from support import FIXTURE, ServiceTest, fixture


class RuntimeTest(ServiceTest):
    def test_health(self):
        status, body, ctype = self.client.get("/health")
        self.assertEqual((status, body), (200, {"status": "ok"}))
        self.assertEqual(ctype, "application/json; charset=utf-8")

    def test_reset_replaces_state_and_repeats(self):
        self.reset()
        self.reset(fixture(restaurants=FIXTURE["restaurants"][:1]))
        status, body, _ = self.client.get("/restaurants")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"restaurants": [
            {"id": "r_anker", "name": "Zum Anker", "timezone": "Europe/Berlin"}]})
        self.reset({})
        self.assertEqual(self.client.get("/restaurants")[1], {"restaurants": []})

    def test_seeded_users_log_in(self):
        self.reset()
        self.login()
        self.login("bob@example.com", "battery staple")

    def test_reset_rejects_invalid_fixture_and_keeps_state(self):
        self.reset()
        bad = [
            [],
            fixture(restaurants=[dict(FIXTURE["restaurants"][0], id="x" * 65)]),
            fixture(restaurants=[dict(FIXTURE["restaurants"][0], opening_hours=[
                {"weekday": "xyz", "opens": "18:00", "closes": "23:00"}])]),
            fixture(restaurants=[dict(FIXTURE["restaurants"][0], timezone="Mars/Olympus")]),
            fixture(users=[{"id": "u", "email": "a@b", "password": 5, "display_name": "A"}]),
        ] + [
            fixture(reservations=[{"id": "res_1", "reference": ref, "user_id": "u_ada",
                                   "restaurant_id": "r_anker", "table_id": "t_1",
                                   "starts_at_local": "2030-09-26T19:00", "party_size": 2}])
            for ref in ("ABC12", "ABCDEFGHIJKLM", "abc123", "ABC-123", 123456)
        ]
        for f in bad:
            self.assertError(self.client.post("/_test/reset", f), 422, "validation_failed")
        self.assertError(self.client.post("/_test/reset", raw=b"{nope"), 400, "malformed_request")
        self.assertEqual(len(self.client.get("/restaurants")[1]["restaurants"]), 3)

    def test_restaurant_detail(self):
        self.reset()
        status, body, _ = self.client.get("/restaurants/r_anker")
        self.assertEqual(status, 200)
        expected = dict(FIXTURE["restaurants"][0])
        self.assertEqual(body, expected)
        self.assertError(self.client.get("/restaurants/nope"), 404, "not_found")

    def test_unknown_route_and_wrong_method(self):
        self.assertError(self.client.get("/nope"), 404, "not_found")
        self.assertError(self.client.call("DELETE", "/restaurants"), 405, "method_not_allowed")
        self.assertError(self.client.post("/health", {}), 405, "method_not_allowed")
        for method in ("TRACE", "CONNECT", "PURGE", "HEAD", "OPTIONS", "PUT"):
            with self.subTest(method=method):
                status, _, _ = self.client.call(method, "/restaurants")
                self.assertEqual(status, 405)
                if method != "HEAD":
                    self.assertError(self.client.call(method, "/restaurants"), 405,
                                     "method_not_allowed")

    def test_seeded_reservation_blocks_table(self):
        seeded = {"id": "res_seed", "reference": "SEED0001", "user_id": "u_bob",
                  "restaurant_id": "r_anker", "table_id": "t_2",
                  "starts_at_local": "2030-09-26T19:00", "party_size": 2,
                  "created_at": "2026-01-02T03:04:05Z"}
        self.reset(fixture(reservations=[seeded]))
        bob = self.login("bob@example.com", "battery staple")
        status, body, _ = self.client.get("/reservations/SEED0001", token=bob)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "confirmed")
        self.assertEqual(body["created_at"], "2026-01-02T03:04:05+00:00")
        ada = self.login()
        self.assertError(self.book(ada), 409, "table_unavailable")


if __name__ == "__main__":
    unittest.main()
