"""Verifier probes for stage-1, written from plans/stage-1/brief.md (spec + decisions).

Run against a live service:  PROBE_BASE_URL=http://127.0.0.1:PORT pytest -q test_probes.py
Optional: PROBE_BASE_URL_FRESH (a second, untouched container for import-into-fresh).
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import os
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

import httpx
import pytest

BASE = os.environ.get("PROBE_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
FRESH = os.environ.get("PROBE_BASE_URL_FRESH")
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
REF = re.compile(r"^[A-Z0-9]{6,12}$")
UTC_TS = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00$")
OFF_TS = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$")

ADA = {"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada"}
BOB = {"id": "u_bob", "email": "bob@example.com", "password": "correct horse", "display_name": "Bob"}


# ---------------------------------------------------------------- helpers

def key() -> str:
    return uuid.uuid4().hex


def all_week(opens="18:00", closes="23:00"):
    return [{"weekday": d, "opens": opens, "closes": closes} for d in WEEKDAYS]


def restaurant(rid="r_anker", *, tz="Europe/Berlin", slot=30, dur=90, cutoff=120,
               hours=None, tables=None, name="Zum Anker"):
    return {"id": rid, "name": name, "timezone": tz, "slot_minutes": slot,
            "reservation_duration_minutes": dur, "cancellation_cutoff_minutes": cutoff,
            "opening_hours": all_week() if hours is None else hours,
            "tables": tables if tables is not None else [
                {"id": "t_1", "label": "1", "capacity": 2},
                {"id": "t_2", "label": "2", "capacity": 4},
                {"id": "t_3", "label": "3", "capacity": 6}]}


def fixture(users=None, restaurants=None, reservations=None):
    return {"users": [ADA, BOB] if users is None else users,
            "restaurants": [restaurant()] if restaurants is None else restaurants,
            "reservations": reservations or []}


def day(lead=7, tz="Europe/Berlin"):
    return (dt.datetime.now(ZoneInfo(tz)).date() + dt.timedelta(days=lead)).isoformat()


class C:
    def __init__(self, base=BASE, token=None):
        self.base, self.token = base, token
        self.h = httpx.Client(base_url=base, timeout=10)

    def req(self, method, path, *, json_=None, raw=None, key_=None, token=..., headers=None, params=None):
        hd = dict(headers or {})
        tok = self.token if token is ... else token
        if tok is not None:
            hd["Authorization"] = f"Bearer {tok}"
        if key_ is not None:
            hd["Idempotency-Key"] = key_
        kw = {"headers": hd, "params": params}
        if json_ is not None:
            hd["Content-Type"] = "application/json"
            kw["content"] = json.dumps(json_)
        if raw is not None:
            hd.setdefault("Content-Type", "application/json")
            kw["content"] = raw
        r = self.h.request(method, path, **kw)
        assert r.status_code < 500, f"{method} {path} -> {r.status_code} {r.text[:300]}"
        return r

    def get(self, p, **k): return self.req("GET", p, **k)
    def post(self, p, **k): return self.req("POST", p, **k)
    def patch(self, p, **k): return self.req("PATCH", p, **k)


def ok(r, status):
    assert r.status_code == status, f"expected {status}, got {r.status_code}: {r.text[:400]}"
    return r.json() if r.content else None


def err(r, status, code):
    assert r.status_code == status, f"expected {status} {code}, got {r.status_code}: {r.text[:400]}"
    body = r.json()
    assert isinstance(body, dict) and isinstance(body.get("error"), dict), r.text
    assert body["error"].get("code") == code, f"expected code {code}, got {body}"
    assert isinstance(body["error"].get("message"), str), body


def reset(fx, base=BASE):
    r = httpx.post(f"{base}/_test/reset", content=json.dumps(fx),
                   headers={"Content-Type": "application/json"}, timeout=10)
    assert r.status_code == 204, f"reset -> {r.status_code} {r.text[:300]}"


def login(user=ADA, base=BASE):
    r = httpx.post(f"{base}/auth/login", json={"email": user["email"], "password": user["password"]}, timeout=10)
    return C(base, ok(r, 200)["token"])


def book(c, date=None, at="19:00", table="t_2", party=4, rid="r_anker", k=None, **extra):
    body = {"restaurant_id": rid, "table_id": table,
            "starts_at_local": f"{date or day()}T{at}", "party_size": party, **extra}
    return c.post("/reservations", json_=body, key_=k or key())


def avail(date, rid="r_anker", party=2, base=BASE):
    r = httpx.get(f"{base}/availability", params={"restaurant_id": rid, "date": date, "party_size": party}, timeout=10)
    return ok(r, 200)["slots"]


def slot_times(date, rid="r_anker", party=2):
    return [s["starts_at_local"].split("T")[1] for s in avail(date, rid, party)]


@pytest.fixture
def world():
    reset(fixture())
    return login(ADA), login(BOB)


# ---------------------------------------------------------------- conventions

def test_content_type_on_success_and_error(world):
    ada, _ = world
    for r in (httpx.get(f"{BASE}/restaurants"), httpx.get(f"{BASE}/restaurants/nope"),
              ada.get("/reservations"), httpx.get(f"{BASE}/reservations")):
        ct = r.headers.get("content-type", "").replace(" ", "").lower()
        assert ct == "application/json;charset=utf-8", (r.request.url, ct)


def test_unknown_route_404():
    err(httpx.get(f"{BASE}/nope"), 404, "not_found")
    err(httpx.post(f"{BASE}/restaurants/r_anker/extra"), 404, "not_found")


def test_wrong_method_405(world):
    err(httpx.delete(f"{BASE}/restaurants"), 405, "method_not_allowed")
    err(httpx.put(f"{BASE}/health"), 405, "method_not_allowed")
    err(httpx.get(f"{BASE}/_test/reset"), 405, "method_not_allowed")


def test_garbage_inputs_never_5xx(world):
    ada, _ = world
    bodies = [b"", b"null", b"[]", b"\"x\"", b"{", b"{\"moves\":null}", "{\"x\":\"\u00e9\u2603\"}".encode(),
              b"{\"party_size\": 1e400}", b"{\"restaurant_id\": null, \"table_id\": null}"]
    paths = [("POST", "/reservations"), ("POST", "/reservation-moves"), ("POST", "/auth/signup"),
             ("POST", "/auth/login"), ("PATCH", "/reservations/ABCDEF"), ("POST", "/_test/import")]
    for m, p in paths:
        for b in bodies:
            r = ada.req(m, p, raw=b, key_=key())
            assert 400 <= r.status_code < 500 or r.status_code in (200, 201, 204), (m, p, b, r.status_code)
            if r.status_code >= 400:
                assert "error" in r.json(), (m, p, b, r.text)
    for q in ({"restaurant_id": "r_anker", "date": "x" * 5000, "party_size": "9" * 400},
              {"restaurant_id": "", "date": "", "party_size": ""}):
        r = httpx.get(f"{BASE}/availability", params=q)
        assert r.status_code in (404, 422), r.status_code
    # the service is still consistent afterwards
    assert ada.get("/reservations").status_code == 200


# ---------------------------------------------------------------- reset

def test_reset_missing_arrays_default_empty():
    reset({"restaurants": [restaurant()]})
    assert httpx.get(f"{BASE}/restaurants").json()["restaurants"][0]["id"] == "r_anker"
    reset({})
    assert httpx.get(f"{BASE}/restaurants").json() == {"restaurants": []}


def test_reset_invalid_leaves_state():
    reset(fixture())
    bad_fixtures = [
        [], "x", 5,
        fixture(restaurants=[restaurant(hours=[{"weekday": "xyz", "opens": "18:00", "closes": "23:00"}])]),
        fixture(restaurants=[restaurant("r" * 65)]),
    ]
    for bad in bad_fixtures:
        r = httpx.post(f"{BASE}/_test/reset", content=json.dumps(bad), headers={"Content-Type": "application/json"})
        err(r, 422, "validation_failed")
    assert [x["id"] for x in httpx.get(f"{BASE}/restaurants").json()["restaurants"]] == ["r_anker"]
    login(ADA)


def test_reset_unparseable_400():
    reset(fixture())
    r = httpx.post(f"{BASE}/_test/reset", content=b"{not json", headers={"Content-Type": "application/json"})
    err(r, 400, "malformed_request")
    assert httpx.get(f"{BASE}/restaurants").json()["restaurants"][0]["id"] == "r_anker"


def test_seeded_created_at():
    d = day()
    seeded = [
        {"id": "res_a", "reference": "SEEDAA", "user_id": "u_ada", "restaurant_id": "r_anker",
         "table_id": "t_2", "starts_at_local": f"{d}T19:00", "party_size": 2,
         "created_at": "2026-01-02T03:04:05+00:00"},
        {"id": "res_b", "reference": "SEEDBB", "user_id": "u_ada", "restaurant_id": "r_anker",
         "table_id": "t_3", "starts_at_local": f"{d}T19:00", "party_size": 2},
    ]
    before = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    reset(fixture(reservations=seeded))
    ada = login(ADA)
    a = ok(ada.get("/reservations/SEEDAA"), 200)
    b = ok(ada.get("/reservations/SEEDBB"), 200)
    assert a["status"] == "confirmed" and a["reservation_id"] == "res_a"
    assert dt.datetime.fromisoformat(a["created_at"]) == dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.timezone.utc)
    assert UTC_TS.match(b["created_at"]), b["created_at"]
    assert abs((dt.datetime.fromisoformat(b["created_at"]) - before).total_seconds()) < 120


# ---------------------------------------------------------------- auth

def test_auth_shapes(world):
    r = ok(httpx.post(f"{BASE}/auth/signup", json={"email": "n@example.com", "password": "12345678",
                                                   "display_name": "N"}), 201)
    assert set(r) >= {"user_id", "display_name", "token"} and r["display_name"] == "N"
    assert isinstance(r["user_id"], str) and len(r["user_id"]) <= 64
    assert len(r["token"]) >= 16
    l = ok(httpx.post(f"{BASE}/auth/login", json={"email": "n@example.com", "password": "12345678"}), 200)
    assert l["user_id"] == r["user_id"] and l["display_name"] == "N" and l["token"] != r["token"]


def test_email_case_insensitive(world):
    err(httpx.post(f"{BASE}/auth/signup", json={"email": "ADA@Example.COM", "password": "12345678",
                                                "display_name": "A"}), 409, "email_taken")
    ok(httpx.post(f"{BASE}/auth/login", json={"email": "Ada@EXAMPLE.com", "password": "correct horse"}), 200)
    ok(httpx.post(f"{BASE}/auth/signup", json={"email": "Mixed@Case.io", "password": "12345678",
                                               "display_name": "M"}), 201)
    ok(httpx.post(f"{BASE}/auth/login", json={"email": "mixed@case.io", "password": "12345678"}), 200)


@pytest.mark.parametrize("body", [
    {"email": "a@b.c", "password": "1234567", "display_name": "X"},
    {"email": "ab.c", "password": "12345678", "display_name": "X"},
    {"email": "a@@b.c", "password": "12345678", "display_name": "X"},
    {"email": "a@", "password": "12345678", "display_name": "X"},
    {"email": "@b.c", "password": "12345678", "display_name": "X"},
    {"email": "a b@c.d", "password": "12345678", "display_name": "X"},
    {"email": "", "password": "12345678", "display_name": "X"},
    {"email": "a@b.c", "password": "12345678", "display_name": ""},
    {"password": "12345678", "display_name": "X"},
    {"email": "a@b.c", "display_name": "X"},
    {"email": "a@b.c", "password": "12345678"},
])
def test_signup_validation_matrix(world, body):
    err(httpx.post(f"{BASE}/auth/signup", json=body), 422, "validation_failed")


def test_signup_password_exactly_8_ok(world):
    ok(httpx.post(f"{BASE}/auth/signup", json={"email": "eight@x.io", "password": "12345678",
                                               "display_name": "E"}), 201)


def test_signup_validation_before_taken(world):
    err(httpx.post(f"{BASE}/auth/signup", json={"email": ADA["email"], "password": "short",
                                                "display_name": "X"}), 422, "validation_failed")


@pytest.mark.parametrize("body", [
    {"email": "a@b.c", "password": 12345678, "display_name": "X"},
    {"email": "a@b.c", "password": "12345678", "display_name": 5},
    {"email": ["a@b.c"], "password": "12345678", "display_name": "X"},
])
def test_signup_wrong_type_400(world, body):
    err(httpx.post(f"{BASE}/auth/signup", json=body), 400, "malformed_request")


def test_login_wrong_type_400(world):
    err(httpx.post(f"{BASE}/auth/login", json={"email": ADA["email"], "password": 5}), 400, "malformed_request")
    err(httpx.post(f"{BASE}/auth/login", content=b"[1]", headers={"Content-Type": "application/json"}),
        400, "malformed_request")
    err(httpx.post(f"{BASE}/auth/login", json={"email": ADA["email"]}), 422, "validation_failed")


def test_multiple_tokens_valid(world):
    a1, a2, a3 = login(ADA), login(ADA), login(ADA)
    assert len({a1.token, a2.token, a3.token}) == 3
    for c in (a1, a2, a3):
        ok(c.get("/reservations"), 200)


@pytest.mark.parametrize("hdr", [None, "", "Bearer", "Bearer ", "bearer {t}", "Basic {t}", "Bearer  {t}",
                                 "Bearer {t}x", "Token {t}", "{t}"])
def test_protected_endpoints_401_matrix(world, hdr):
    ada, _ = world
    t = ada.token
    headers = {} if hdr is None else {"Authorization": hdr.format(t=t)}
    ref = ok(book(ada), 201)["reference"]
    calls = [("GET", "/reservations", None), ("GET", f"/reservations/{ref}", None),
             ("POST", f"/reservations/{ref}/cancel", None), ("PATCH", f"/reservations/{ref}", {"party_size": 2}),
             ("POST", "/reservations", {"x": 1}), ("POST", "/reservation-moves", {"moves": []})]
    for m, p, b in calls:
        r = httpx.request(m, f"{BASE}{p}", headers={**headers, "Idempotency-Key": key()}, json=b)
        err(r, 401, "unauthenticated")
    assert ok(ada.get(f"/reservations/{ref}"), 200)["status"] == "confirmed"


def test_fifty_concurrent_logins():
    users = [{"id": f"u_{i}", "email": f"u{i}@x.io", "password": "correct horse", "display_name": f"U{i}"}
             for i in range(50)]
    reset(fixture(users=users))

    def go(i):
        t0 = time.monotonic()
        r = httpx.post(f"{BASE}/auth/login", json={"email": f"u{i}@x.io", "password": "correct horse"}, timeout=6)
        return r.status_code, time.monotonic() - t0
    with ThreadPoolExecutor(50) as ex:
        out = list(ex.map(go, range(50)))
    assert all(s == 200 for s, _ in out), out
    assert max(t for _, t in out) < 5.0, max(t for _, t in out)


def test_export_has_no_plaintext_password(world):
    ok(httpx.post(f"{BASE}/auth/signup", json={"email": "p@x.io", "password": "Zq9-unique-pw",
                                               "display_name": "P"}), 201)
    text = httpx.get(f"{BASE}/_test/export").text
    assert "Zq9-unique-pw" not in text and "correct horse" not in text


# ---------------------------------------------------------------- availability

def test_availability_param_matrix(world):
    d = day()
    cases = [({"date": d, "party_size": "2"}, 422, "validation_failed"),
             ({"restaurant_id": "r_anker", "party_size": "2"}, 422, "validation_failed"),
             ({"restaurant_id": "r_anker", "date": d}, 422, "validation_failed"),
             ({"restaurant_id": "r_anker", "date": "2026-13-01", "party_size": "2"}, 422, "validation_failed"),
             ({"restaurant_id": "r_anker", "date": "2026-9-01", "party_size": "2"}, 422, "validation_failed"),
             ({"restaurant_id": "r_anker", "date": "2026-09-01T00:00", "party_size": "2"}, 422, "validation_failed"),
             ({"restaurant_id": "r_anker", "date": "2025-02-29", "party_size": "2"}, 422, "validation_failed"),
             ({"restaurant_id": "r_anker", "date": d, "party_size": "0"}, 422, "validation_failed"),
             ({"restaurant_id": "r_anker", "date": d, "party_size": "-1"}, 422, "validation_failed"),
             ({"restaurant_id": "r_anker", "date": d, "party_size": "1e1"}, 422, "validation_failed"),
             ({"restaurant_id": "r_anker", "date": d, "party_size": "abc"}, 422, "validation_failed"),
             ({"restaurant_id": "r_anker", "date": d, "party_size": ""}, 422, "validation_failed"),
             ({"restaurant_id": "r_nope", "date": d, "party_size": "2"}, 404, "not_found")]
    for q, s, c in cases:
        err(httpx.get(f"{BASE}/availability", params=q), s, c)
    ok(httpx.get(f"{BASE}/availability", params={"restaurant_id": "r_anker", "date": "2028-02-29",
                                                  "party_size": "2"}), 200)
    # party larger than every table: slots listed, all empty
    slots = avail(d, party=7)
    assert slots and all(s["available_table_ids"] == [] for s in slots)


def test_slot_boundary_and_odd_grid():
    d = day()
    r = restaurant(slot=25, dur=60, hours=all_week("17:10", "20:00"))
    reset(fixture(restaurants=[r]))
    assert slot_times(d) == ["17:10", "17:35", "18:00", "18:25", "18:50"]  # 18:50+60 = 19:50; 19:15+60 > 20:00
    ada = login(ADA)
    ok(book(ada, d, at="18:50"), 201)
    err(book(ada, d, at="19:15"), 422, "outside_opening_hours")
    err(book(ada, d, at="17:00"), 422, "outside_opening_hours")
    err(book(ada, d, at="17:30"), 422, "not_on_slot_grid")
    r = restaurant(slot=30, dur=90, hours=all_week("18:00", "20:30"))
    reset(fixture(restaurants=[r]))
    assert slot_times(d) == ["18:00", "18:30", "19:00"]  # 19:00+90 == 20:30 kept


def test_opening_hours_only_on_listed_weekday():
    d = day()
    wd = WEEKDAYS[dt.date.fromisoformat(d).weekday()]
    other = WEEKDAYS[(dt.date.fromisoformat(d).weekday() + 1) % 7]
    reset(fixture(restaurants=[restaurant(hours=[{"weekday": other, "opens": "18:00", "closes": "23:00"}])]))
    assert avail(d) == []
    err(book(login(ADA), d), 422, "outside_opening_hours")
    reset(fixture(restaurants=[restaurant(hours=[{"weekday": wd, "opens": "18:00", "closes": "23:00"}])]))
    assert avail(d)


# ---------------------------------------------------------------- DST

def zoned(slot=30, dur=90):
    reset(fixture(restaurants=[
        restaurant("r_berlin", tz="Europe/Berlin", slot=slot, dur=dur, hours=all_week("00:00", "23:30")),
        restaurant("r_ny", tz="America/New_York", slot=slot, dur=dur, hours=all_week("00:00", "23:30"))]))


def test_spring_forward_slots():
    zoned()
    b = slot_times("2026-03-29", "r_berlin")
    assert "02:00" not in b and "02:30" not in b and "01:30" in b and "03:00" in b
    n = slot_times("2026-03-08", "r_ny")
    assert "02:00" not in n and "02:30" not in n and "03:00" in n
    s = {x["starts_at_local"][-5:]: x["starts_at"] for x in avail("2026-03-29", "r_berlin")}
    assert s["01:30"].endswith("+01:00") and s["03:00"].endswith("+02:00")
    ada = login(ADA)
    err(book(ada, "2026-03-08", at="02:30", rid="r_ny"), 422, "invalid_local_time")
    err(book(ada, "2026-03-08", at="02:00", rid="r_ny"), 422, "invalid_local_time")


def test_ny_fall_back():
    zoned()
    n = slot_times("2026-11-01", "r_ny")
    assert n.count("01:00") == 1 and n.count("01:30") == 1
    ada = login(ADA)
    r = ok(book(ada, "2026-11-01", at="01:30", rid="r_ny"), 201)
    assert r["starts_at"] == "2026-11-01T01:30:00-04:00", r["starts_at"]
    assert r["ends_at"] == "2026-11-01T02:00:00-05:00", r["ends_at"]


def test_spring_forward_duration_absolute():
    zoned()
    ada = login(ADA)
    r = ok(book(ada, "2026-03-29", at="01:30", rid="r_berlin"), 201)
    assert r["starts_at"] == "2026-03-29T01:30:00+01:00"
    assert r["ends_at"] == "2026-03-29T04:00:00+02:00", r["ends_at"]


def test_fall_back_overlap_in_absolute_time():
    """Berlin 2026-10-25: 01:30 (+02) for 90 min ends at 02:00 (+01); a booking at 02:00
    (first occurrence, +02) overlaps it in absolute time."""
    zoned()
    ada = login(ADA)
    ok(book(ada, "2026-10-25", at="01:30", rid="r_berlin"), 201)
    err(book(ada, "2026-10-25", at="02:00", rid="r_berlin"), 409, "table_unavailable")
    err(book(ada, "2026-10-25", at="02:30", rid="r_berlin"), 409, "table_unavailable")


@pytest.mark.parametrize("rid,date", [("r_berlin", "2026-03-29"), ("r_berlin", "2026-10-25"),
                                      ("r_ny", "2026-03-08"), ("r_ny", "2026-11-01")])
def test_every_listed_slot_is_bookable_on_transition_days(rid, date):
    zoned(slot=15, dur=120)
    ada = login(ADA)
    listed = set(slot_times(date, rid))
    for h in range(24):
        for m in range(0, 60, 15):
            at = f"{h:02d}:{m:02d}"
            # a fresh restaurant state for each probe is too slow; use party 1 on rotating tables
            r = ada.post("/reservations", json_={"restaurant_id": rid, "table_id": "t_3",
                                                  "starts_at_local": f"{date}T{at}", "party_size": 1}, key_=key())
            if at in listed:
                assert r.status_code == 201, (at, r.status_code, r.text)
                ada.post(f"/reservations/{r.json()['reference']}/cancel")
            else:
                assert r.status_code == 422, (at, r.status_code, r.text)


# ---------------------------------------------------------------- create

def test_create_shape_strict(world):
    ada, _ = world
    d = day()
    r = ok(book(ada, d), 201)
    assert set(r) == {"reservation_id", "reference", "restaurant_id", "table_id", "party_size", "status",
                      "starts_at_local", "starts_at", "ends_at", "created_at"}, set(r)
    assert REF.match(r["reference"]) and 0 < len(r["reservation_id"]) <= 64
    assert UTC_TS.match(r["created_at"]), r["created_at"]
    assert OFF_TS.match(r["starts_at"]) and OFF_TS.match(r["ends_at"])
    assert isinstance(r["party_size"], int) and r["party_size"] == 4


def test_create_precedence_order(world):
    ada, _ = world
    good = {"restaurant_id": "r_anker", "table_id": "t_2", "starts_at_local": f"{day()}T19:00", "party_size": 4}
    # 401 beats missing key
    err(httpx.post(f"{BASE}/reservations", json=good), 401, "unauthenticated")
    # missing key beats bad body
    err(ada.post("/reservations", raw=b"{bad"), 400, "missing_idempotency_key")
    err(ada.post("/reservations", raw=b"{bad", key_=""), 400, "missing_idempotency_key")
    # long key beats bad body
    err(ada.post("/reservations", raw=b"{bad", key_="k" * 256), 422, "validation_failed")
    # bad body
    err(ada.post("/reservations", raw=b"{bad", key_=key()), 400, "malformed_request")


def test_idempotency_key_length(world):
    ada, _ = world
    ok(book(ada, k="k" * 255), 201)
    err(book(ada, at="20:30", k="k" * 256), 422, "validation_failed")
    ok(book(ada, at="20:30", k="x"), 201)


def test_create_body_not_object(world):
    ada, _ = world
    for raw in (b"[]", b"\"s\"", b"3", b"null"):
        err(ada.post("/reservations", raw=raw, key_=key()), 400, "malformed_request")


@pytest.mark.parametrize("drop", ["restaurant_id", "table_id", "starts_at_local", "party_size"])
def test_create_missing_field(world, drop):
    ada, _ = world
    body = {"restaurant_id": "r_anker", "table_id": "t_2", "starts_at_local": f"{day()}T19:00", "party_size": 4}
    del body[drop]
    err(ada.post("/reservations", json_=body, key_=key()), 422, "validation_failed")


@pytest.mark.parametrize("field,value,status,code", [
    ("restaurant_id", 5, 400, "malformed_request"),
    ("restaurant_id", ["r_anker"], 400, "malformed_request"),
    ("table_id", 2, 400, "malformed_request"),
    ("starts_at_local", 1900, 400, "malformed_request"),
    ("party_size", True, 422, "validation_failed"),
    ("party_size", False, 422, "validation_failed"),
    ("party_size", 4.0, 422, "validation_failed"),
    ("party_size", None, 422, "validation_failed"),
    ("party_size", [4], 422, "validation_failed"),
])
def test_create_wrong_types(world, field, value, status, code):
    ada, _ = world
    body = {"restaurant_id": "r_anker", "table_id": "t_2", "starts_at_local": f"{day()}T19:00",
            "party_size": 4, field: value}
    err(ada.post("/reservations", json_=body, key_=key()), status, code)


@pytest.mark.parametrize("v", ["2026-02-30T19:00", "2026-13-01T19:00", "2026-10-03T24:00", "2026-10-03T19:60",
                               "2026-10-03T19:0", "2026-10-03 19:00", "2026-10-3T19:00", "", "19:00",
                               "2026-10-03T19:00:00", "2026-10-03t19:00", " 2026-10-03T19:00"])
def test_starts_at_local_formats(world, v):
    ada, _ = world
    body = {"restaurant_id": "r_anker", "table_id": "t_2", "starts_at_local": v, "party_size": 2}
    err(ada.post("/reservations", json_=body, key_=key()), 422, "validation_failed")


def test_create_error_precedence(world):
    ada, _ = world
    d = day()
    # missing field (a) beats wrong type (b)
    err(ada.post("/reservations", json_={"restaurant_id": 5, "table_id": "t_2", "party_size": 2}, key_=key()),
        422, "validation_failed")
    # format (b) beats unknown restaurant (c)
    err(ada.post("/reservations", json_={"restaurant_id": "r_nope", "table_id": "t_2",
                                         "starts_at_local": "bad", "party_size": 2}, key_=key()),
        422, "validation_failed")
    # not found (c) beats capacity (g) and hours (e)
    err(book(ada, d, at="03:00", table="t_nope", party=99), 404, "not_found")
    # hours (e) beats grid (f)
    err(book(ada, d, at="17:15"), 422, "outside_opening_hours")
    # grid (f) beats capacity (g)
    err(book(ada, d, at="19:15", table="t_1", party=4), 422, "not_on_slot_grid")
    # capacity (g) beats overlap (h)
    ok(book(ada, d, at="19:00", table="t_1", party=2), 201)
    err(book(ada, d, at="19:00", table="t_1", party=3), 422, "party_exceeds_capacity")
    # DST (d) beats hours (e): Berlin spring gap on a restaurant closed at night
    reset(fixture(restaurants=[restaurant()]))
    ada = login(ADA)
    err(book(ada, "2026-03-29", at="02:30"), 422, "invalid_local_time")


def test_past_booking_allowed(world):
    ada, _ = world
    past = day(lead=-30)
    ok(book(ada, past), 201)


def test_cancelled_does_not_block(world):
    ada, bob = world
    ref = ok(book(ada), 201)["reference"]
    ok(ada.post(f"/reservations/{ref}/cancel"), 200)
    ok(book(bob), 201)


# ---------------------------------------------------------------- idempotency

def test_reuse_with_invalid_body_is_409(world):
    ada, _ = world
    k = key()
    ok(book(ada, k=k), 201)
    err(ada.post("/reservations", json_={"nonsense": True}, key_=k), 409, "idempotency_key_reuse")
    err(ada.post("/reservations", json_={}, key_=k), 409, "idempotency_key_reuse")
    # but an unparseable body is 400 before idempotency
    err(ada.post("/reservations", raw=b"{x", key_=k), 400, "malformed_request")


def test_key_reusable_after_4xx(world):
    ada, _ = world
    k = key()
    err(book(ada, table="t_1", party=4, k=k), 422, "party_exceeds_capacity")
    ok(book(ada, table="t_2", party=4, k=k), 201)
    k2 = key()
    ok(book(ada, at="21:00", table="t_3", k=key()), 201)
    err(book(ada, at="21:00", table="t_3", k=k2), 409, "table_unavailable")
    ok(book(ada, at="21:00", table="t_2", k=k2), 201)


def test_same_key_other_path(world):
    ada, _ = world
    k = key()
    ref = ok(book(ada, table="t_1", party=2, k=k), 201)["reference"]
    body = {"moves": [{"reference": ref, "table_id": "t_3"}]}
    ok(ada.post("/reservation-moves", json_=body, key_=k), 201)


def test_replay_key_order_whitespace(world):
    ada, _ = world
    k = key()
    d = day()
    first = ok(ada.post("/reservations", raw=(
        '{"restaurant_id":"r_anker","table_id":"t_2","starts_at_local":"%sT19:00","party_size":4}' % d).encode(),
        key_=k), 201)
    again = ok(ada.post("/reservations", raw=(
        '{ "party_size" : 4 ,\n "starts_at_local":"%sT19:00", "table_id":"t_2","restaurant_id":"r_anker"}' % d
    ).encode(), key_=k), 200)
    assert again == first
    # 4 vs 4.0 is a different JSON value for party_size? JSON numbers 4 and 4.0 are equal values;
    # we do not assert either way.


def test_replay_no_state_change(world):
    ada, _ = world
    k = key()
    ok(book(ada, k=k), 201)
    for _ in range(3):
        ok(book(ada, k=k), 200)
    assert len(ok(ada.get("/reservations"), 200)["reservations"]) == 1


def test_replay_after_patch_returns_original(world):
    ada, _ = world
    k = key()
    first = ok(book(ada, k=k), 201)
    ok(ada.patch(f"/reservations/{first['reference']}", json_={"table_id": "t_3"}), 200)
    assert ok(book(ada, k=k), 200) == first


def test_concurrent_identical_key():
    reset(fixture())
    ada = login(ADA)
    k = key()
    body = {"restaurant_id": "r_anker", "table_id": "t_2", "starts_at_local": f"{day()}T19:00", "party_size": 4}

    def go(_):
        return httpx.post(f"{BASE}/reservations", json=body, timeout=6,
                          headers={"Authorization": f"Bearer {ada.token}", "Idempotency-Key": k})
    with ThreadPoolExecutor(50) as ex:
        out = list(ex.map(go, range(50)))
    codes = sorted(r.status_code for r in out)
    assert codes.count(201) == 1 and codes.count(200) == 49, codes
    bodies = {json.dumps(r.json(), sort_keys=True) for r in out}
    assert len(bodies) == 1
    assert len(ok(ada.get("/reservations"), 200)["reservations"]) == 1


def test_fifty_compete_one_slot():
    users = [{"id": f"u_{i}", "email": f"c{i}@x.io", "password": "correct horse", "display_name": "C"}
             for i in range(50)]
    reset(fixture(users=users))
    toks = [login({"email": u["email"], "password": u["password"]}).token for u in users]
    body = {"restaurant_id": "r_anker", "table_id": "t_2", "starts_at_local": f"{day()}T19:00", "party_size": 4}

    def go(i):
        return httpx.post(f"{BASE}/reservations", json=body, timeout=6,
                          headers={"Authorization": f"Bearer {toks[i]}", "Idempotency-Key": key()}).status_code
    with ThreadPoolExecutor(50) as ex:
        codes = sorted(ex.map(go, range(50)))
    assert codes.count(201) == 1 and codes.count(409) == 49, codes


def test_concurrent_patch_onto_one_table():
    users = [{"id": f"u_{i}", "email": f"p{i}@x.io", "password": "correct horse", "display_name": "P"}
             for i in range(20)]
    tables = [{"id": f"t_{i}", "label": str(i), "capacity": 4} for i in range(21)]
    reset(fixture(users=users, restaurants=[restaurant(tables=tables)]))
    cs = [login({"email": u["email"], "password": u["password"]}) for u in users]
    refs = [ok(book(c, table=f"t_{i}"), 201)["reference"] for i, c in enumerate(cs)]

    def go(i):
        return cs[i].patch(f"/reservations/{refs[i]}", json_={"table_id": "t_20"}).status_code
    with ThreadPoolExecutor(20) as ex:
        codes = sorted(ex.map(go, range(20)))
    assert codes.count(200) == 1 and codes.count(409) == 19, codes


# ---------------------------------------------------------------- reads / cancel / patch

def test_empty_reservation_list(world):
    ada, _ = world
    assert ok(ada.get("/reservations"), 200) == {"reservations": []}


def cutoff_world(cutoff_min, start_in_min):
    """A restaurant open all day on a 1-minute grid with a booking `start_in_min` from now."""
    tz = ZoneInfo("Europe/Berlin")
    reset(fixture(restaurants=[restaurant(slot=1, dur=30, cutoff=cutoff_min, hours=all_week("00:00", "23:59"))]))
    ada = login(ADA)
    start = (dt.datetime.now(tz) + dt.timedelta(minutes=start_in_min)).replace(second=0, microsecond=0)
    if start.date() != (start + dt.timedelta(minutes=31)).date():
        pytest.skip("too close to local midnight")
    r = ok(ada.post("/reservations", json_={"restaurant_id": "r_anker", "table_id": "t_2",
                                            "starts_at_local": start.strftime("%Y-%m-%dT%H:%M"),
                                            "party_size": 2}, key_=key()), 201)
    return ada, r["reference"]


def test_cutoff_boundary():
    # start ~ now+10min (floored), cutoff 10 -> now >= start - cutoff -> refused
    ada, ref = cutoff_world(10, 10)
    err(ada.post(f"/reservations/{ref}/cancel"), 409, "cutoff_passed")
    err(ada.patch(f"/reservations/{ref}", json_={"party_size": 1}), 409, "cutoff_passed")
    # start ~ now+12min, cutoff 10 -> outside
    ada, ref = cutoff_world(10, 12)
    ok(ada.patch(f"/reservations/{ref}", json_={"party_size": 1}), 200)
    assert ok(ada.post(f"/reservations/{ref}/cancel"), 200)["status"] == "cancelled"


def test_cancel_already_cancelled_inside_cutoff():
    """D7/D8: an already-cancelled booking answers 200 (cancel) and 409 reservation_cancelled
    (PATCH) even once the cutoff has passed. Waits ~62 s for the cutoff to arrive."""
    ada, ref = cutoff_world(10, 11)
    ok(ada.post(f"/reservations/{ref}/cancel"), 200)
    time.sleep(62)
    assert ok(ada.post(f"/reservations/{ref}/cancel"), 200)["status"] == "cancelled"
    err(ada.patch(f"/reservations/{ref}", json_={"party_size": 1}), 409, "reservation_cancelled")


def test_patch_matrix(world):
    ada, bob = world
    d = day()
    ref = ok(book(ada, d, table="t_2", party=4), 201)["reference"]
    err(bob.patch(f"/reservations/{ref}", json_={"party_size": 2}), 404, "not_found")
    err(ada.patch("/reservations/NOPE99", json_={"party_size": 2}), 404, "not_found")
    r = ok(ada.patch(f"/reservations/{ref}", json_={"starts_at_local": f"{d}T20:00"}), 200)
    assert r["starts_at_local"] == f"{d}T20:00" and r["ends_at"].startswith(f"{d}T21:30")
    err(ada.patch(f"/reservations/{ref}", json_={"party_size": 5}), 422, "party_exceeds_capacity")
    err(ada.patch(f"/reservations/{ref}", json_={"party_size": "2"}), 422, "validation_failed")
    err(ada.patch(f"/reservations/{ref}", json_={"party_size": 0}), 422, "validation_failed")
    err(ada.patch(f"/reservations/{ref}", json_={"table_id": 3}), 400, "malformed_request")
    err(ada.patch(f"/reservations/{ref}", json_={"table_id": "t_nope"}), 404, "not_found")
    err(ada.patch(f"/reservations/{ref}", json_={"starts_at_local": f"{d}T20:15"}), 422, "not_on_slot_grid")
    err(ada.patch(f"/reservations/{ref}", json_={"starts_at_local": f"{d}T22:00"}), 422, "outside_opening_hours")
    err(ada.patch(f"/reservations/{ref}", json_={"starts_at_local": "2026-03-29T02:30"}), 422, "invalid_local_time")
    err(ada.patch(f"/reservations/{ref}", json_={"starts_at_local": f"{d}T20:00Z"}), 422, "validation_failed")
    r2 = ok(ada.patch(f"/reservations/{ref}", json_={"table_id": "t_3", "party_size": 6}), 200)
    assert (r2["table_id"], r2["party_size"], r2["reservation_id"], r2["reference"], r2["created_at"]) == \
        ("t_3", 6, r["reservation_id"], ref, r["created_at"])
    ok(ada.post(f"/reservations/{ref}/cancel"), 200)
    err(ada.patch(f"/reservations/{ref}", json_={"party_size": 2}), 409, "reservation_cancelled")


def test_patch_self_overlap_and_failure_unchanged(world):
    ada, bob = world
    d = day()
    ref = ok(book(ada, d, at="19:00", table="t_2"), 201)["reference"]
    # shift by one slot overlaps only itself -> allowed
    ok(ada.patch(f"/reservations/{ref}", json_={"starts_at_local": f"{d}T19:30"}), 200)
    before = ok(ada.get(f"/reservations/{ref}"), 200)
    ok(book(bob, d, at="21:00", table="t_3"), 201)
    # table+time change that collides -> 409 and nothing changed
    err(ada.patch(f"/reservations/{ref}", json_={"table_id": "t_3", "starts_at_local": f"{d}T21:00"}),
        409, "table_unavailable")
    assert ok(ada.get(f"/reservations/{ref}"), 200) == before
    # its old occupancy still held
    err(book(bob, d, at="19:30", table="t_2"), 409, "table_unavailable")


def test_patch_noop_and_bad_body(world):
    ada, _ = world
    d = day()
    r = ok(book(ada, d), 201)
    ref = r["reference"]
    assert ok(ada.patch(f"/reservations/{ref}", json_={}), 200) == r
    assert ok(ada.patch(f"/reservations/{ref}", json_={"table_id": "t_2", "party_size": 4}), 200) == r
    assert ok(ada.patch(f"/reservations/{ref}", json_={"restaurant_id": "r_other", "x": 1}), 200) == r
    err(ada.patch(f"/reservations/{ref}", raw=b"{x"), 400, "malformed_request")
    err(ada.patch(f"/reservations/{ref}", raw=b"[]"), 400, "malformed_request")
    # D8: 400 body parse comes before 404
    err(ada.patch("/reservations/NOPE99", raw=b"{x"), 400, "malformed_request")


# ---------------------------------------------------------------- export / import

def imp(obj, base=BASE, raw=None):
    return httpx.post(f"{base}/_test/import", content=raw if raw is not None else json.dumps(obj),
                      headers={"Content-Type": "application/json"}, timeout=10)


def test_export_shape(world):
    e = ok(httpx.get(f"{BASE}/_test/export"), 200)
    assert e["track"] == "tablekeeper" and e["format_version"] == 1 and isinstance(e["state"], dict)


def build_history():
    reset(fixture())
    ada, bob = login(ADA), login(BOB)
    d = day()
    k1, k2, kf, km = key(), key(), key(), key()
    b1 = ok(book(ada, d, at="19:00", table="t_2", k=k1), 201)
    b2 = ok(book(ada, d, at="19:00", table="t_1", party=2, k=k2), 201)
    ok(ada.post(f"/reservations/{b2['reference']}/cancel"), 200)
    err(book(ada, d, at="19:00", table="t_1", party=5, k=kf), 422, "party_exceeds_capacity")
    b3 = ok(book(bob, d, at="20:00", table="t_3", party=2), 201)
    mv = ok(bob.post("/reservation-moves", json_={"moves": [{"reference": b3["reference"], "table_id": "t_1"}]},
                     key_=km), 201)
    return ada, bob, d, dict(k1=k1, k2=k2, kf=kf, km=km, b1=b1, b2=b2, b3=b3, mv=mv)


def check_restored(base, ada_tok, bob_tok, d, h):
    ada, bob = C(base, ada_tok), C(base, bob_tok)
    login(ADA, base)
    login(BOB, base)
    lst = ok(ada.get("/reservations"), 200)["reservations"]
    assert {x["reference"] for x in lst} == {h["b1"]["reference"], h["b2"]["reference"]}
    assert ok(ada.get(f"/reservations/{h['b1']['reference']}"), 200) == h["b1"]
    assert ok(ada.get(f"/reservations/{h['b2']['reference']}"), 200)["status"] == "cancelled"
    assert ok(book(ada, d, at="19:00", table="t_2", k=h["k1"]), 200) == h["b1"]
    assert ok(book(ada, d, at="19:00", table="t_1", party=2, k=h["k2"]), 200) == h["b2"]
    err(book(ada, d, at="19:00", table="t_2", party=3, k=h["k1"]), 409, "idempotency_key_reuse")
    assert ok(bob.post("/reservation-moves", json_={"moves": [{"reference": h["b3"]["reference"],
                                                               "table_id": "t_1"}]}, key_=h["km"]), 200) == h["mv"]
    # the failed key is reusable: first use now succeeds
    ok(book(ada, d, at="21:00", table="t_1", party=2, k=h["kf"]), 201)
    # occupancy restored: b1 blocks t_2 at 19:00
    err(book(bob, d, at="19:00", table="t_2"), 409, "table_unavailable")
    # new references do not collide with imported ones and ids stay unique
    new = ok(book(bob, d, at="21:00", table="t_2", party=2), 201)
    assert new["reference"] not in {h["b1"]["reference"], h["b2"]["reference"], h["b3"]["reference"]}
    assert new["reservation_id"] not in {h["b1"]["reservation_id"], h["b2"]["reservation_id"],
                                         h["b3"]["reservation_id"]}


def test_export_import_roundtrip_full():
    ada, bob, d, h = build_history()
    snap = ok(httpx.get(f"{BASE}/_test/export"), 200)
    reset(fixture(users=[], restaurants=[]))
    assert ok(imp(snap), 204) is None
    check_restored(BASE, ada.token, bob.token, d, h)


def test_import_into_fresh_container():
    if not FRESH:
        pytest.skip("PROBE_BASE_URL_FRESH not set")
    ada, bob, d, h = build_history()
    snap = ok(httpx.get(f"{BASE}/_test/export"), 200)
    ok(imp(snap, FRESH), 204)
    check_restored(FRESH, ada.token, bob.token, d, h)


def test_import_twice_no_duplicates():
    ada, bob, d, h = build_history()
    snap = ok(httpx.get(f"{BASE}/_test/export"), 200)
    ok(imp(snap), 204)
    ok(imp(snap), 204)
    assert len(ok(ada.get("/reservations"), 200)["reservations"]) == 2
    assert len(httpx.get(f"{BASE}/restaurants").json()["restaurants"]) == 1
    assert ok(httpx.get(f"{BASE}/_test/export"), 200) == snap


def test_invalid_imports():
    ada, bob, d, h = build_history()
    snap = ok(httpx.get(f"{BASE}/_test/export"), 200)
    bad = [[], {}, {**snap, "track": "pocketful"}, {**snap, "format_version": 2},
           {**snap, "format_version": "1"}, {k: v for k, v in snap.items() if k != "state"},
           {**snap, "state": []}, {**snap, "state": "x"}, {**snap, "state": {}}]
    for b in bad:
        err(imp(b), 422, "validation_failed")
    err(imp(None, raw=b"{nope"), 400, "malformed_request")
    assert ok(httpx.get(f"{BASE}/_test/export"), 200) == snap


def test_import_replaces_and_reset_clears():
    ada, bob, d, h = build_history()
    snap = ok(httpx.get(f"{BASE}/_test/export"), 200)
    reset(fixture(users=[{"id": "u_z", "email": "z@x.io", "password": "zzzzzzzz", "display_name": "Z"}],
                  restaurants=[restaurant("r_z")]))
    z = login({"email": "z@x.io", "password": "zzzzzzzz"})
    ok(imp(snap), 204)
    err(z.get("/reservations"), 401, "unauthenticated")
    err(httpx.post(f"{BASE}/auth/login", json={"email": "z@x.io", "password": "zzzzzzzz"}), 401, "unauthenticated")
    assert [r["id"] for r in httpx.get(f"{BASE}/restaurants").json()["restaurants"]] == ["r_anker"]
    reset(fixture(users=[], restaurants=[]))
    err(ada.get("/reservations"), 401, "unauthenticated")
    assert httpx.get(f"{BASE}/restaurants").json() == {"restaurants": []}


def test_export_is_snapshot():
    ada, bob, d, h = build_history()
    snap = ok(httpx.get(f"{BASE}/_test/export"), 200)
    frozen = copy.deepcopy(snap)
    ok(book(ada, d, at="21:00", table="t_3"), 201)
    assert snap == frozen
    ok(imp(snap), 204)
    assert len(ok(ada.get("/reservations"), 200)["reservations"]) == 2


# ---------------------------------------------------------------- moves

def mv(c, moves, k=None, raw=None):
    if raw is not None:
        return c.post("/reservation-moves", raw=raw, key_=k or key())
    return c.post("/reservation-moves", json_={"moves": moves}, key_=k or key())


def test_moves_basic(world):
    ada, _ = world
    d = day()
    a = ok(book(ada, d, at="19:00", table="t_1", party=2), 201)
    b = ok(book(ada, d, at="19:00", table="t_2", party=2), 201)
    err(httpx.post(f"{BASE}/reservation-moves", json={"moves": []}, headers={"Idempotency-Key": key()}),
        401, "unauthenticated")
    err(ada.post("/reservation-moves", json_={"moves": [{"reference": a["reference"]}]}), 400,
        "missing_idempotency_key")
    err(ada.post("/reservation-moves", json_={"moves": [{"reference": a["reference"]}]}, key_="k" * 256), 422,
        "validation_failed")
    res = ok(mv(ada, [{"reference": b["reference"]},
                      {"reference": a["reference"], "table_id": "t_3", "unknown": 1}]), 201)["reservations"]
    assert [r["reference"] for r in res] == [b["reference"], a["reference"]]
    assert res[0] == b
    assert res[1]["table_id"] == "t_3" and res[1]["reservation_id"] == a["reservation_id"] \
        and res[1]["created_at"] == a["created_at"]


@pytest.mark.parametrize("body", [
    {}, {"moves": None}, {"moves": {}}, {"moves": "x"}, {"moves": []},
    {"moves": [{"reference": f"R{i}"} for i in range(9)]},
    {"moves": [5]}, {"moves": [{}]}, {"moves": [{"reference": 5}]},
    {"moves": [{"reference": "DUP"}, {"reference": "DUP"}]},
    {"moves": [{"reference": "A", "party_size": "2"}]},
    {"moves": [{"reference": "A", "table_id": 2}]},
    {"moves": [{"reference": "A", "starts_at_local": 5}]},
])
def test_moves_shape_errors(world, body):
    ada, _ = world
    err(ada.post("/reservation-moves", json_=body, key_=key()), 422, "validation_failed")


def test_moves_body_not_object(world):
    ada, _ = world
    err(mv(ada, None, raw=b"[]"), 400, "malformed_request")
    err(mv(ada, None, raw=b"{x"), 400, "malformed_request")


def test_moves_error_precedence():
    d = day()
    reset(fixture(restaurants=[restaurant(), restaurant("r_two", name="Two")]))
    ada, bob = login(ADA), login(BOB)
    a1 = ok(book(ada, d, at="19:00", table="t_1", party=2), 201)
    a2 = ok(book(ada, d, at="20:30", table="t_1", party=2), 201)
    other = ok(book(ada, d, at="19:00", table="t_1", party=2, rid="r_two"), 201)
    b1 = ok(book(bob, d, at="19:00", table="t_3", party=2), 201)
    ac = ok(book(ada, d, at="21:00", table="t_2", party=2), 201)
    ok(ada.post(f"/reservations/{ac['reference']}/cancel"), 200)
    err(mv(ada, [{"reference": a1["reference"]}, {"reference": b1["reference"]}]), 404, "not_found")
    err(mv(ada, [{"reference": a1["reference"]}, {"reference": "NOPE99"}]), 404, "not_found")
    err(mv(ada, [{"reference": a1["reference"]}, {"reference": other["reference"]}]), 422, "validation_failed")
    err(mv(ada, [{"reference": a1["reference"]}, {"reference": ac["reference"]}]), 409, "reservation_cancelled")
    # input order: first item's field error wins over second item's 404
    err(mv(ada, [{"reference": a1["reference"], "party_size": 99}, {"reference": "NOPE99"}]), 422,
        "party_exceeds_capacity")
    err(mv(ada, [{"reference": a1["reference"], "table_id": "t_nope"}]), 404, "not_found")
    err(mv(ada, [{"reference": a1["reference"], "starts_at_local": f"{d}T19:10"}]), 422, "not_on_slot_grid")
    err(mv(ada, [{"reference": a1["reference"], "starts_at_local": "2026-03-29T02:30"}]), 422, "invalid_local_time")
    # nothing changed
    assert ok(ada.get(f"/reservations/{a1['reference']}"), 200) == a1
    assert ok(ada.get(f"/reservations/{a2['reference']}"), 200) == a2


def test_moves_cutoff_even_unchanged():
    seeded = [{"id": "res_p", "reference": "PAST01", "user_id": "u_ada", "restaurant_id": "r_anker",
               "table_id": "t_2", "starts_at_local": f"{day(lead=-2)}T19:00", "party_size": 2}]
    reset(fixture(reservations=seeded))
    ada = login(ADA)
    a = ok(book(ada, day(), at="19:00", table="t_1", party=2), 201)
    err(mv(ada, [{"reference": a["reference"], "table_id": "t_3"}, {"reference": "PAST01"}]), 409, "cutoff_passed")
    assert ok(ada.get(f"/reservations/{a['reference']}"), 200) == a


def test_moves_swap():
    reset(fixture())
    ada = login(ADA)
    d = day()
    a = ok(book(ada, d, at="19:00", table="t_2", party=2), 201)
    b = ok(book(ada, d, at="19:00", table="t_3", party=2), 201)
    res = ok(mv(ada, [{"reference": a["reference"], "table_id": "t_3"},
                      {"reference": b["reference"], "table_id": "t_2"}]), 201)["reservations"]
    assert [r["table_id"] for r in res] == ["t_3", "t_2"]
    # time swap on the same table
    c = ok(book(ada, d, at="21:00", table="t_1", party=2), 201)
    e = ok(book(ada, d, at="19:30", table="t_1", party=2), 201)
    res = ok(mv(ada, [{"reference": c["reference"], "starts_at_local": f"{d}T19:30"},
                      {"reference": e["reference"], "starts_at_local": f"{d}T21:00"}]), 201)["reservations"]
    assert [r["starts_at_local"][-5:] for r in res] == ["19:30", "21:00"]


def test_moves_all_or_nothing():
    reset(fixture())
    ada, bob = login(ADA), login(BOB)
    d = day()
    a = ok(book(ada, d, at="19:00", table="t_1", party=2), 201)
    b = ok(book(ada, d, at="19:00", table="t_2", party=2), 201)
    ok(book(bob, d, at="19:00", table="t_3", party=2), 201)
    k = key()
    # second move collides with an unlisted booking -> nothing changes
    err(mv(ada, [{"reference": a["reference"], "starts_at_local": f"{d}T21:00"},
                 {"reference": b["reference"], "table_id": "t_3"}], k=k), 409, "table_unavailable")
    assert ok(ada.get(f"/reservations/{a['reference']}"), 200) == a
    assert ok(ada.get(f"/reservations/{b['reference']}"), 200) == b
    # the two results collide with each other
    err(mv(ada, [{"reference": a["reference"], "table_id": "t_3", "starts_at_local": f"{d}T21:00"},
                 {"reference": b["reference"], "table_id": "t_3", "starts_at_local": f"{d}T21:30"}]),
        409, "table_unavailable")
    # the key is still unused after the 409
    ok(mv(ada, [{"reference": a["reference"], "starts_at_local": f"{d}T21:00"}], k=k), 201)


def test_moves_replay_after_changes():
    reset(fixture())
    ada = login(ADA)
    d = day()
    a = ok(book(ada, d, at="19:00", table="t_1", party=2), 201)
    k = key()
    first = ok(mv(ada, [{"reference": a["reference"], "table_id": "t_2"}], k=k), 201)
    ok(ada.post(f"/reservations/{a['reference']}/cancel"), 200)
    assert ok(mv(ada, [{"reference": a["reference"], "table_id": "t_2"}], k=k), 200) == first
    err(mv(ada, [{"reference": a["reference"], "table_id": "t_3"}], k=k), 409, "idempotency_key_reuse")
    err(mv(ada, [{"reference": a["reference"]}, {"reference": a["reference"]}], k=k), 409,
        "idempotency_key_reuse")


def test_concurrent_identical_moves():
    reset(fixture())
    ada = login(ADA)
    d = day()
    a = ok(book(ada, d, at="19:00", table="t_1", party=2), 201)
    k = key()

    def go(_):
        return httpx.post(f"{BASE}/reservation-moves", json={"moves": [{"reference": a["reference"],
                                                                         "table_id": "t_2"}]}, timeout=6,
                          headers={"Authorization": f"Bearer {ada.token}", "Idempotency-Key": k})
    with ThreadPoolExecutor(30) as ex:
        out = list(ex.map(go, range(30)))
    codes = sorted(r.status_code for r in out)
    assert codes.count(201) == 1 and codes.count(200) == 29, codes
