"""WO 1.2: signup, login and bearer authentication."""

import threading
import time
import unittest

from support import ServiceTest


class AuthTest(ServiceTest):
    def setUp(self):
        self.reset()

    def signup(self, **fields):
        body = {"email": "cy@example.com", "password": "longenough", "display_name": "Cy"}
        body.update(fields)
        return self.client.post("/auth/signup", body)

    def test_signup_and_login(self):
        status, body, _ = self.signup()
        self.assertEqual(status, 201, body)
        self.assertEqual(set(body), {"user_id", "display_name", "token"})
        self.assertEqual(body["display_name"], "Cy")
        status, login, _ = self.client.post("/auth/login",
                                            {"email": "CY@example.com", "password": "longenough"})
        self.assertEqual(status, 200)
        self.assertEqual(login["user_id"], body["user_id"])
        self.assertNotEqual(login["token"], body["token"])
        for token in (body["token"], login["token"]):
            self.assertEqual(self.client.get("/reservations", token=token)[0], 200)

    def test_signup_errors(self):
        self.assertError(self.signup(email="ADA@example.com"), 409, "email_taken")
        self.assertError(self.signup(password="short"), 422, "validation_failed")
        self.assertError(self.signup(email="ADA@example.com", password="short"), 422,
                         "validation_failed")
        for email in ("no-at", "a@@b", "@b", "a@", "a b@c"):
            self.assertError(self.signup(email=email), 422, "validation_failed")
        self.assertError(self.client.post("/auth/signup", {"email": "x@y", "password": "12345678"}),
                         422, "validation_failed")
        self.assertError(self.signup(display_name=""), 422, "validation_failed")
        self.assertError(self.signup(password=12345678), 400, "malformed_request")
        self.assertError(self.client.post("/auth/signup", raw=b"[1]"), 400, "malformed_request")

    def test_login_errors(self):
        self.assertError(self.client.post("/auth/login", {"email": "ada@example.com",
                                                          "password": "wrong horse"}),
                         401, "unauthenticated")
        self.assertError(self.client.post("/auth/login", {"email": "who@example.com",
                                                          "password": "correct horse"}),
                         401, "unauthenticated")
        self.assertError(self.client.post("/auth/login", {"email": "ada@example.com"}),
                         422, "validation_failed")

    def test_bearer_required(self):
        token = self.login()
        for headers in ({}, {"Authorization": "Bearer"}, {"Authorization": f"bearer {token}"},
                        {"Authorization": f"Token {token}"}, {"Authorization": "Bearer nope"}):
            self.assertError(self.client.get("/reservations", headers=headers), 401,
                             "unauthenticated")
        self.assertEqual(self.client.get("/reservations", token=token)[0], 200)

    def test_public_endpoints_need_no_token(self):
        self.assertEqual(self.client.get("/restaurants")[0], 200)
        self.assertEqual(self.client.get("/restaurants/r_anker")[0], 200)
        self.assertEqual(self.client.get(
            "/availability?restaurant_id=r_anker&date=2030-09-26&party_size=2")[0], 200)

    def test_concurrent_logins_are_fast(self):
        results = []

        def worker():
            results.append(self.client.post("/auth/login", {"email": "ada@example.com",
                                                            "password": "correct horse"})[0])
        threads = [threading.Thread(target=worker) for _ in range(50)]
        started = time.monotonic()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results, [200] * 50)
        self.assertLess(time.monotonic() - started, 5)


if __name__ == "__main__":
    unittest.main()
