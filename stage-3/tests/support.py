"""Test support: an in-process service on a free port and a small JSON client."""

import copy
import http.client
import json
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

from tablekeeper.server import serve  # noqa: E402

FIXTURE = {
    "users": [
        {"id": "u_ada", "email": "ada@example.com", "password": "correct horse",
         "display_name": "Ada"},
        {"id": "u_bob", "email": "Bob@Example.com", "password": "battery staple",
         "display_name": "Bob"},
    ],
    "restaurants": [
        {
            "id": "r_anker", "name": "Zum Anker", "timezone": "Europe/Berlin",
            "slot_minutes": 30, "reservation_duration_minutes": 90,
            "cancellation_cutoff_minutes": 120,
            "opening_hours": [{"weekday": d, "opens": "18:00", "closes": "23:00"}
                              for d in ("mon", "tue", "wed", "thu", "fri", "sat")],
            "tables": [{"id": "t_1", "label": "1", "capacity": 2},
                       {"id": "t_2", "label": "2", "capacity": 4},
                       {"id": "t_3", "label": "3", "capacity": 6}],
        },
        {
            "id": "r_night", "name": "Nachtcafe", "timezone": "Europe/Berlin",
            "slot_minutes": 30, "reservation_duration_minutes": 90,
            "cancellation_cutoff_minutes": 60,
            "opening_hours": [{"weekday": "sun", "opens": "00:00", "closes": "06:00"}],
            "tables": [{"id": "t_1", "label": "A", "capacity": 4}],
        },
        {
            "id": "r_ny", "name": "Night Owl", "timezone": "America/New_York",
            "slot_minutes": 30, "reservation_duration_minutes": 90,
            "cancellation_cutoff_minutes": 60,
            "opening_hours": [{"weekday": "sun", "opens": "00:00", "closes": "06:00"}],
            "tables": [{"id": "t_9", "label": "9", "capacity": 4}],
        },
    ],
    "reservations": [],
}

# 2030-09-26 is a Thursday: far enough ahead that no cutoff has passed.
FUTURE = "2030-09-26"


def fixture(**changes):
    f = copy.deepcopy(FIXTURE)
    f.update(changes)
    return f


class Client:
    def __init__(self, port):
        self.port = port

    def call(self, method, path, body=None, token=None, key=None, raw=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        h = dict(headers or {})
        data = raw
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        if data is not None:
            h["Content-Type"] = "application/json"
        if token:
            h["Authorization"] = f"Bearer {token}"
        if key is not None:
            h["Idempotency-Key"] = key
        conn.request(method, path, body=data, headers=h)
        resp = conn.getresponse()
        payload = resp.read()
        conn.close()
        parsed = json.loads(payload) if payload else None
        return resp.status, parsed, resp.getheader("Content-Type")

    def get(self, path, **kw):
        return self.call("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.call("POST", path, body=body, **kw)

    def patch(self, path, body=None, **kw):
        return self.call("PATCH", path, body=body, **kw)


class ServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = serve(0, host="127.0.0.1")
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.client = Client(cls.httpd.server_address[1])

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def reset(self, f=None):
        status, _, _ = self.client.post("/_test/reset", f if f is not None else fixture())
        self.assertEqual(status, 204)

    def login(self, email="ada@example.com", password="correct horse"):
        status, body, _ = self.client.post("/auth/login", {"email": email, "password": password})
        self.assertEqual(status, 200, body)
        return body["token"]

    def book(self, token, key="k1", **fields):
        body = {"restaurant_id": "r_anker", "table_id": "t_2",
                "starts_at_local": f"{FUTURE}T19:00", "party_size": 4}
        body.update(fields)
        return self.client.post("/reservations", body, token=token, key=key)

    def assertError(self, result, status, code):
        got_status, body, ctype = result
        self.assertEqual(got_status, status, body)
        self.assertEqual(body["error"]["code"], code, body)
        self.assertIsInstance(body["error"]["message"], str)
        self.assertEqual(ctype, "application/json; charset=utf-8")
