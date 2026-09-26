"""Verifier probes for stage-3 (policies, terms, revisions, history, series, upgrade), from
plans/stage-3/brief.md.

PROBE_BASE_URL = stage-3 service; PROBE_BASE_URL_PREV1 / PROBE_BASE_URL_PREV2 = stage-1 / stage-2
services (upgrade probes). Helpers come from the stage-1 probes.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "stage-1", "probes"))
from test_probes import (ADA, BOB, BASE, C, all_week, day, err, fixture, imp, key, login,  # noqa: E402
                         ok, reset, restaurant)

PREV1 = os.environ.get("PROBE_BASE_URL_PREV1")
PREV2 = os.environ.get("PROBE_BASE_URL_PREV2")
TERM_KEYS = {"policy_version", "slot_minutes", "reservation_duration_minutes", "cancellation_cutoff_minutes",
             "opening_hours", "capacities"}
POLICY_KEYS = TERM_KEYS - {"policy_version"} | {"effective_from"}


def D(lead=7):
    return day(lead)


def mrest(rid="r_anker", combinable=None, **kw):
    r = restaurant(rid, **kw)
    r["manager_user_ids"] = ["u_ada"]
    if combinable is not None:
        r["combinable"] = combinable
    return r


def pol(date, **over):
    return {"effective_from": date, "slot_minutes": 30, "reservation_duration_minutes": 90,
            "cancellation_cutoff_minutes": 120, "opening_hours": all_week(),
            "capacities": {"t_1": 2, "t_2": 4, "t_3": 6}, **over}


def publish(c, body, rid="r_anker", k=None):
    return c.post(f"/restaurants/{rid}/policies", json_=body, key_=k or key())


def managed(**kw):
    reset(fixture(restaurants=[mrest(**kw)]))
    return login(ADA), login(BOB)


def bk(c, date=None, at="19:00", table="t_2", party=4, rid="r_anker", k=None, tables=None, **extra):
    body = {"restaurant_id": rid, "starts_at_local": f"{date or D()}T{at}", "party_size": party, **extra}
    if tables is not None:
        body["table_ids"] = tables
    else:
        body["table_id"] = table
    return c.post("/reservations", json_=body, key_=k or key())


def av(date, party=2, rid="r_anker", explain=True):
    params = {"restaurant_id": rid, "date": date, "party_size": party}
    if explain:
        params["explain"] = "true"
    return httpx.get(f"{BASE}/availability", params=params)


def hist(c, ref):
    return ok(c.get(f"/reservations/{ref}/history"), 200)


def at_minutes(ts):
    return dt.datetime.fromisoformat(ts)


def series(c, ref, count=2, interval=1, k=None, **extra):
    return c.post("/series", json_={"anchor_reference": ref, "count": count, "interval_weeks": interval, **extra},
                  key_=k or key())


# ---------------------------------------------------------------- explain

def test_explain_values():
    reset(fixture())
    for v in ("false", "1", "True", "", "TRUE", "yes"):
        err(httpx.get(f"{BASE}/availability", params={"restaurant_id": "r_anker", "date": D(), "party_size": 2,
                                                      "explain": v}), 422, "validation_failed")
    plain = ok(av(D(), explain=False), 200)
    assert all("explain" not in s for s in plain["slots"])
    assert all(set(s) >= {"starts_at_local", "starts_at", "available_table_ids"} for s in plain["slots"])
    full = ok(av(D()), 200)
    assert full["slots"] and all("explain" in s for s in full["slots"])


def test_explain_rules_all_reported():
    reset(fixture())
    bob = login(BOB)
    ok(bk(bob, table="t_1", party=2), 201)
    ok(bk(bob, table="t_3", party=2), 201)
    body = ok(av(D(), party=4), 200)
    for s in body["slots"]:
        ex = s["explain"]
        assert [e["table_id"] for e in ex] == ["t_1", "t_2", "t_3"]
        for e in ex:
            assert [r["rule"] for r in e["rules"]] == ["capacity", "no_overlap"], e
            assert e["available"] == all(r["holds"] for r in e["rules"])
        assert [e["table_id"] for e in ex if e["available"]] == s["available_table_ids"]
    s = next(x for x in body["slots"] if x["starts_at_local"].endswith("19:00"))
    by = {e["table_id"]: [r["holds"] for r in e["rules"]] for e in s["explain"]}
    assert by == {"t_1": [False, False], "t_2": [True, True], "t_3": [True, False]}, by
    s = next(x for x in body["slots"] if x["starts_at_local"].endswith("21:00"))
    by = {e["table_id"]: [r["holds"] for r in e["rules"]] for e in s["explain"]}
    assert by == {"t_1": [False, True], "t_2": [True, True], "t_3": [True, True]}, by


def test_explain_closed_and_full():
    d = D()
    wd = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[dt.date.fromisoformat(d).weekday()]
    other = [h for h in all_week() if h["weekday"] != wd]
    reset(fixture(restaurants=[restaurant(hours=other)]))
    assert ok(av(d), 200)["slots"] == []
    reset(fixture())
    bob = login(BOB)
    for t in ("t_1", "t_2", "t_3"):
        ok(bk(bob, table=t, party=2), 201)
    s = next(x for x in ok(av(d), 200)["slots"] if x["starts_at_local"].endswith("19:00"))
    assert s["available_table_ids"] == [] and len(s["explain"]) == 3
    assert all(not e["available"] and [r["holds"] for r in e["rules"]] == [True, False] for e in s["explain"])


def test_explain_policy_version():
    ada, _ = managed()
    ok(publish(ada, pol(D(), capacities={"t_1": 5, "t_2": 4, "t_3": 6})), 201)
    s = ok(av(D(), party=5), 200)["slots"][0]
    assert {e["policy_version"] for e in s["explain"]} == {1}
    assert s["available_table_ids"] == ["t_1", "t_3"]
    assert {e["policy_version"] for e in ok(av(D(6), party=5), 200)["slots"][0]["explain"]} == {0}
    ok(publish(ada, pol(D(), capacities={"t_1": 2, "t_2": 4, "t_3": 6})), 201)
    s = ok(av(D(), party=5), 200)["slots"][0]
    assert {e["policy_version"] for e in s["explain"]} == {2} and s["available_table_ids"] == ["t_3"]


# ---------------------------------------------------------------- policies

def test_policy_precedence():
    reset(fixture(restaurants=[mrest(), restaurant("r_free")]))
    ada, bob = login(ADA), login(BOB)
    body = pol(D())
    err(httpx.post(f"{BASE}/restaurants/r_anker/policies", json=body, headers={"Idempotency-Key": key()}),
        401, "unauthenticated")
    err(httpx.post(f"{BASE}/restaurants/r_nope/policies", json=body), 401, "unauthenticated")
    err(C(BASE, "nope").post("/restaurants/r_anker/policies", json_=body, key_=key()), 401, "unauthenticated")
    err(bob.post("/restaurants/r_nope/policies", json_=body), 404, "not_found")
    err(bob.post("/restaurants/r_anker/policies", json_=body), 403, "forbidden")  # 403 before key
    err(ada.post("/restaurants/r_free/policies", json_=body, key_=key()), 403, "forbidden")
    err(ada.post("/restaurants/r_anker/policies", raw=b"{x"), 400, "missing_idempotency_key")
    err(ada.post("/restaurants/r_anker/policies", raw=b"{x", key_="k" * 256), 422, "validation_failed")
    err(ada.post("/restaurants/r_anker/policies", raw=b"{x", key_=key()), 400, "malformed_request")
    err(ada.post("/restaurants/r_anker/policies", raw=b"[]", key_=key()), 400, "malformed_request")
    k = key()
    first = ok(publish(ada, body, k=k), 201)
    err(publish(ada, {"nonsense": 1}, k=k), 409, "idempotency_key_reuse")  # reuse beats validation
    assert ok(publish(ada, body, k=k), 200) == first


def _mut(**kw):
    return kw


BAD = [
    ("drop", "effective_from"), ("drop", "slot_minutes"), ("drop", "reservation_duration_minutes"),
    ("drop", "cancellation_cutoff_minutes"), ("drop", "opening_hours"), ("drop", "capacities"),
    ("set", _mut(effective_from="2026-02-30")), ("set", _mut(effective_from="2026-9-01")),
    ("set", _mut(effective_from=20261001)), ("set", _mut(effective_from=None)),
    ("set", _mut(slot_minutes=0)), ("set", _mut(slot_minutes=1441)), ("set", _mut(slot_minutes="30")),
    ("set", _mut(slot_minutes=True)), ("set", _mut(slot_minutes=30.5)),
    ("set", _mut(reservation_duration_minutes=0)), ("set", _mut(reservation_duration_minutes=1441)),
    ("set", _mut(reservation_duration_minutes=False)),
    ("set", _mut(cancellation_cutoff_minutes=-1)), ("set", _mut(cancellation_cutoff_minutes=10081)),
    ("set", _mut(cancellation_cutoff_minutes=True)), ("set", _mut(cancellation_cutoff_minutes="0")),
    ("set", _mut(opening_hours="all")), ("set", _mut(opening_hours=[5])),
    ("set", _mut(opening_hours=[{"weekday": "xyz", "opens": "18:00", "closes": "23:00"}])),
    ("set", _mut(opening_hours=[{"weekday": "mon", "opens": "18:00", "closes": "23:00"},
                                {"weekday": "mon", "opens": "10:00", "closes": "12:00"}])),
    ("set", _mut(opening_hours=[{"weekday": "mon", "opens": "23:00", "closes": "18:00"}])),
    ("set", _mut(opening_hours=[{"weekday": "mon", "opens": "18:00", "closes": "18:00"}])),
    ("set", _mut(opening_hours=[{"weekday": "mon", "opens": "7:00", "closes": "18:00"}])),
    ("set", _mut(opening_hours=[{"weekday": "mon", "opens": "18:00"}])),
    ("set", _mut(capacities={"t_1": 2, "t_2": 4})), ("set", _mut(capacities={"t_1": 2, "t_2": 4, "t_3": 6, "t_9": 1})),
    ("set", _mut(capacities={"t_1": 0, "t_2": 4, "t_3": 6})), ("set", _mut(capacities={"t_1": 101, "t_2": 4, "t_3": 6})),
    ("set", _mut(capacities={"t_1": "2", "t_2": 4, "t_3": 6})), ("set", _mut(capacities={"t_1": True, "t_2": 4, "t_3": 6})),
    ("set", _mut(capacities={"t_1": 2.5, "t_2": 4, "t_3": 6})), ("set", _mut(capacities=[2, 4, 6])),
]


def test_policy_validation_matrix():
    ada, _ = managed()
    for kind, what in BAD:
        body = pol(D())
        if kind == "drop":
            del body[what]
        else:
            body.update(what)
        err(publish(ada, body), 422, "validation_failed")
    good = [pol(D(), slot_minutes=1, reservation_duration_minutes=1440, cancellation_cutoff_minutes=0),
            pol(D(), slot_minutes=1440, cancellation_cutoff_minutes=10080, opening_hours=[]),
            pol("2020-01-01", capacities={"t_1": 100, "t_2": 1, "t_3": 1})]
    versions = [ok(publish(ada, b), 201)["policy_version"] for b in good]
    assert versions == [1, 2, 3], versions
    assert len(ok(httpx.get(f"{BASE}/restaurants/r_anker/policies"), 200)["policies"]) == 3


def test_policy_versions_and_replay():
    reset(fixture(restaurants=[mrest(), mrest("r_two", name="Two")]))
    ada = login(ADA)
    k = key()
    p1 = ok(publish(ada, pol(D()), k=k), 201)
    assert p1["policy_version"] == 1
    assert ok(publish(ada, pol(D()), k=k), 200) == p1
    err(publish(ada, pol(D(), slot_minutes=15), k=k), 409, "idempotency_key_reuse")
    err(publish(ada, pol(D(), slot_minutes=0)), 422, "validation_failed")
    assert ok(publish(ada, pol(D())), 201)["policy_version"] == 2
    assert ok(publish(ada, pol(D()), rid="r_two"), 201)["policy_version"] == 1
    # same key on another restaurant path is a different request
    assert ok(publish(ada, pol(D()), rid="r_two", k=k), 201)["policy_version"] == 2


def test_policy_listing():
    ada, bob = managed()
    assert ok(httpx.get(f"{BASE}/restaurants/r_anker/policies"), 200) == {"policies": []}
    err(httpx.get(f"{BASE}/restaurants/r_nope/policies"), 404, "not_found")
    p1 = ok(publish(ada, {**pol(D(14)), "foo": 1}), 201)
    assert set(p1) == POLICY_KEYS | {"policy_version"}, set(p1)
    assert {k: p1[k] for k in POLICY_KEYS} == pol(D(14))
    p2 = ok(publish(ada, pol(D(), slot_minutes=15)), 201)
    assert ok(httpx.get(f"{BASE}/restaurants/r_anker/policies"), 200) == {"policies": [p1, p2]}


def test_detail_unchanged():
    ada, _ = managed()
    before = ok(httpx.get(f"{BASE}/restaurants/r_anker"), 200)
    assert "manager_user_ids" not in before
    ok(publish(ada, pol(D(-3), slot_minutes=15, reservation_duration_minutes=60, opening_hours=[])), 201)
    assert ok(httpx.get(f"{BASE}/restaurants/r_anker"), 200) == before
    assert before["slot_minutes"] == 30 and before["reservation_duration_minutes"] == 90


def test_policy_selection():
    ada, _ = managed()
    ok(publish(ada, pol(D(14), reservation_duration_minutes=60)), 201)   # v1
    ok(publish(ada, pol(D(7), reservation_duration_minutes=120)), 201)   # v2
    ok(publish(ada, pol(D(14), reservation_duration_minutes=30)), 201)   # v3 same date as v1 -> wins
    cases = [(D(6), 0, 90), (D(7), 2, 120), (D(13), 2, 120), (D(14), 3, 30), (D(21), 3, 30)]
    for i, (date, ver, dur) in enumerate(cases):
        r = ok(bk(ada, date, table=["t_1", "t_2", "t_3"][i % 3], party=2), 201)
        assert r["accepted_terms"]["policy_version"] == ver, (date, r["accepted_terms"])
        assert r["accepted_terms"]["reservation_duration_minutes"] == dur
        assert at_minutes(r["ends_at"]) - at_minutes(r["starts_at"]) == dt.timedelta(minutes=dur)


def test_availability_uses_policy():
    ada, _ = managed(combinable=[["t_1", "t_2"]])
    ok(publish(ada, pol(D(), slot_minutes=60, reservation_duration_minutes=60, opening_hours=all_week("17:00", "20:00"),
                        capacities={"t_1": 5, "t_2": 1, "t_3": 6})), 201)
    ok(publish(ada, pol(D(14), opening_hours=[])), 201)
    s = ok(av(D(), party=5), 200)["slots"]
    assert [x["starts_at_local"][-5:] for x in s] == ["17:00", "18:00", "19:00"]
    assert s[0]["available_table_ids"] == ["t_1", "t_3"]
    assert s[0]["available_options"] == [{"table_ids": ["t_1"], "capacity": 5}, {"table_ids": ["t_3"], "capacity": 6},
                                         {"table_ids": ["t_1", "t_2"], "capacity": 6}], s[0]
    assert [x["starts_at_local"][-5:] for x in ok(av(D(6), party=2), 200)["slots"]][0] == "18:00"
    assert ok(av(D(14)), 200)["slots"] == []
    # a booking on D (60 min) at 17:00 blocks 17:00 only, not 18:00
    ok(bk(ada, D(), at="17:00", table="t_3", party=2), 201)
    s = ok(av(D(), party=2), 200)["slots"]
    assert "t_3" not in s[0]["available_table_ids"] and "t_3" in s[1]["available_table_ids"]


def test_booking_uses_policy():
    ada, _ = managed(combinable=[["t_1", "t_2"]])
    ok(publish(ada, pol(D(), slot_minutes=60, reservation_duration_minutes=60, opening_hours=all_week("17:00", "20:00"),
                        capacities={"t_1": 5, "t_2": 1, "t_3": 6})), 201)
    r = ok(bk(ada, D(), at="17:00", table="t_1", party=5), 201)
    assert r["ends_at"].startswith(f"{D()}T18:00")
    err(bk(ada, D(), at="18:30", table="t_3", party=2), 422, "not_on_slot_grid")
    err(bk(ada, D(), at="19:30", table="t_3", party=2), 422, "outside_opening_hours")
    err(bk(ada, D(), at="18:00", table="t_2", party=2), 422, "party_exceeds_capacity")
    err(bk(ada, D(), at="18:00", tables=["t_1", "t_2"], party=7), 422, "party_exceeds_capacity")
    ok(bk(ada, D(), at="18:00", tables=["t_2", "t_1"], party=6), 201)


def test_patch_adopts_new_policy():
    ada, _ = managed()
    r = ok(bk(ada, D(6), table="t_2", party=4), 201)
    ok(publish(ada, pol(D(), reservation_duration_minutes=60, capacities={"t_1": 2, "t_2": 3, "t_3": 6})), 201)
    ref = r["reference"]
    err(ada.patch(f"/reservations/{ref}", json_={"starts_at_local": f"{D()}T19:00"}), 422, "party_exceeds_capacity")
    assert ok(ada.get(f"/reservations/{ref}"), 200) == r
    p = ok(ada.patch(f"/reservations/{ref}", json_={"starts_at_local": f"{D()}T19:00", "party_size": 3}), 200)
    assert p["revision"] == 2 and p["accepted_terms"]["policy_version"] == 1
    assert p["ends_at"].startswith(f"{D()}T20:00")


# ---------------------------------------------------------------- terms / revisions

def test_terms_shape():
    reset(fixture())
    ada = login(ADA)
    r = ok(bk(ada), 201)
    assert r["revision"] == 1
    t = r["accepted_terms"]
    assert set(t) == TERM_KEYS, set(t)
    assert t == {"policy_version": 0, "slot_minutes": 30, "reservation_duration_minutes": 90,
                 "cancellation_cutoff_minutes": 120, "opening_hours": all_week(),
                 "capacities": {"t_1": 2, "t_2": 4, "t_3": 6}}, t
    assert ok(ada.get(f"/reservations/{r['reference']}"), 200) == r
    assert ok(ada.get("/reservations"), 200)["reservations"] == [r]


def test_publication_does_not_touch_bookings():
    ada, _ = managed()
    r = ok(bk(ada), 201)
    h = hist(ada, r["reference"])
    ok(publish(ada, pol(D(-30), reservation_duration_minutes=60, cancellation_cutoff_minutes=0,
                        capacities={"t_1": 1, "t_2": 1, "t_3": 1})), 201)
    assert ok(ada.get(f"/reservations/{r['reference']}"), 200) == r
    assert hist(ada, r["reference"]) == h
    # the booking still occupies its full accepted 90 minutes
    err(bk(login(BOB), at="20:00", table="t_2", party=1), 409, "table_unavailable")


def test_accepted_cutoff_rules():
    ada, _ = managed()
    a = ok(bk(ada, D(6), table="t_1", party=2), 201)
    b = ok(bk(ada, D(6), table="t_2", party=2), 201)
    ok(publish(ada, pol(D(6), cancellation_cutoff_minutes=10080)), 201)
    # accepted cutoff (120) still governs cancel
    ok(ada.post(f"/reservations/{a['reference']}/cancel"), 200)
    # a real amendment checks the OLD cutoff, then adopts the new terms (cutoff 10080)
    p = ok(ada.patch(f"/reservations/{b['reference']}", json_={"party_size": 3}), 200)
    assert p["accepted_terms"]["cancellation_cutoff_minutes"] == 10080
    err(ada.patch(f"/reservations/{b['reference']}", json_={"party_size": 2}), 409, "cutoff_passed")
    err(ada.post(f"/reservations/{b['reference']}/cancel"), 409, "cutoff_passed")
    # the reverse: a booking accepted under a huge cutoff stays locked after a lenient policy
    reset(fixture(restaurants=[mrest(cutoff=10080)]))
    ada = login(ADA)
    c = ok(bk(ada, D(6), table="t_1", party=2), 201)
    err(ada.post(f"/reservations/{c['reference']}/cancel"), 409, "cutoff_passed")
    ok(publish(ada, pol(D(6), cancellation_cutoff_minutes=0)), 201)
    err(ada.post(f"/reservations/{c['reference']}/cancel"), 409, "cutoff_passed")
    err(ada.patch(f"/reservations/{c['reference']}", json_={"party_size": 2}), 409, "cutoff_passed")  # no-op too


def test_revision_rules():
    reset(fixture())
    ada = login(ADA)
    r = ok(bk(ada), 201)
    ref = r["reference"]
    p = ok(ada.patch(f"/reservations/{ref}", json_={"party_size": 3}), 200)
    assert p["revision"] == 2
    for noop in ({"party_size": 3}, {}, {"table_ids": ["t_2"]}, {"table_id": "t_2", "x": 1},
                 {"starts_at_local": r["starts_at_local"]}):
        assert ok(ada.patch(f"/reservations/{ref}", json_=noop), 200) == p, noop
    assert len(hist(ada, ref)["entries"]) == 2
    err(ada.patch(f"/reservations/{ref}", json_={"party_size": 99}), 422, "party_exceeds_capacity")
    assert ok(ada.get(f"/reservations/{ref}"), 200) == p
    c = ok(ada.post(f"/reservations/{ref}/cancel"), 200)
    assert c["revision"] == 3 and c["accepted_terms"] == p["accepted_terms"]
    assert ok(ada.post(f"/reservations/{ref}/cancel"), 200)["revision"] == 3
    assert [e["event"] for e in hist(ada, ref)["entries"]] == ["created", "changed", "cancelled"]


def test_expected_revision_matrix():
    seeded = [{"id": "res_p", "reference": "PAST01", "user_id": "u_ada", "restaurant_id": "r_anker",
               "table_id": "t_2", "starts_at_local": f"{D(-2)}T19:00", "party_size": 2}]
    reset(fixture(reservations=seeded))
    ada = login(ADA)
    ref = ok(bk(ada), 201)["reference"]
    for v in (True, False, "1", 0, -1, 1.5, 1.0, None, [1]):
        err(ada.patch(f"/reservations/{ref}", json_={"party_size": 3, "expected_revision": v}), 422,
            "validation_failed")
    err(ada.patch("/reservations/NOPE0000", json_={"expected_revision": "x"}), 404, "not_found")
    err(ada.patch(f"/reservations/{ref}", json_={"party_size": 99, "expected_revision": 2}), 409, "stale_revision")
    p = ok(ada.patch(f"/reservations/{ref}", json_={"party_size": 3, "expected_revision": 1}), 200)
    assert p["revision"] == 2
    assert ok(ada.patch(f"/reservations/{ref}", json_={"party_size": 3, "expected_revision": 2}), 200) == p
    # stale before cutoff
    err(ada.patch("/reservations/PAST01", json_={"party_size": 1, "expected_revision": 5}), 409, "stale_revision")
    err(ada.patch("/reservations/PAST01", json_={"party_size": 1, "expected_revision": 1}), 409, "cutoff_passed")
    # stale before cancelled
    ok(ada.post(f"/reservations/{ref}/cancel"), 200)
    err(ada.patch(f"/reservations/{ref}", json_={"party_size": 2, "expected_revision": 2}), 409, "stale_revision")
    err(ada.patch(f"/reservations/{ref}", json_={"party_size": 2, "expected_revision": 3}), 409,
        "reservation_cancelled")


def test_concurrent_expected_revision():
    reset(fixture())
    ada = login(ADA)
    ref = ok(bk(ada), 201)["reference"]

    def go(i):
        return ada.patch(f"/reservations/{ref}", json_={"party_size": 1 + i % 3, "expected_revision": 1})
    with ThreadPoolExecutor(20) as ex:
        out = list(ex.map(go, range(20)))
    codes = sorted(r.status_code for r in out)
    assert codes.count(200) == 1 and codes.count(409) == 19, codes
    assert all(r.json()["error"]["code"] == "stale_revision" for r in out if r.status_code == 409)
    assert ok(ada.get(f"/reservations/{ref}"), 200)["revision"] == 2
    assert len(hist(ada, ref)["entries"]) == 2


def test_replay_keeps_original_terms():
    ada, _ = managed()
    k = key()
    r = ok(bk(ada, k=k), 201)
    ok(ada.patch(f"/reservations/{r['reference']}", json_={"party_size": 3}), 200)
    ok(publish(ada, pol(D(-1), reservation_duration_minutes=60)), 201)
    ok(ada.patch(f"/reservations/{r['reference']}", json_={"party_size": 2}), 200)
    assert ok(bk(ada, k=k), 200) == r
    assert len(hist(ada, r["reference"])["entries"]) == 3


# ---------------------------------------------------------------- history / decision

def test_history_decision_404s():
    reset(fixture())
    ada, bob = login(ADA), login(BOB)
    ref = ok(bk(ada), 201)["reference"]
    for suffix in ("history", "decision"):
        err(bob.get(f"/reservations/{ref}/{suffix}"), 404, "not_found")
        err(httpx.get(f"{BASE}/reservations/{ref}/{suffix}"), 404, "not_found")
        err(C(BASE, "bogus").get(f"/reservations/{ref}/{suffix}"), 404, "not_found")
        err(httpx.get(f"{BASE}/reservations/{ref}/{suffix}", headers={"Authorization": "Basic x"}), 404, "not_found")
        err(ada.get(f"/reservations/NOPE0000/{suffix}"), 404, "not_found")
        ok(ada.get(f"/reservations/{ref}/{suffix}"), 200)


def test_history_rules():
    reset(fixture())
    ada = login(ADA)
    k = key()
    r = ok(bk(ada, table="t_2", party=4, k=k), 201)
    ref = r["reference"]
    ok(bk(ada, table="t_2", party=4, k=k), 200)
    h = hist(ada, ref)
    assert h["reference"] == ref and len(h["entries"]) == 1
    e = h["entries"][0]
    assert e["seq"] == 1 and e["event"] == "created"
    assert e["changes"] == [{"field": "table_id", "from": None, "to": "t_2"},
                            {"field": "starts_at_local", "from": None, "to": f"{D()}T19:00"},
                            {"field": "party_size", "from": None, "to": 4}], e["changes"]
    ok(ada.patch(f"/reservations/{ref}", json_={"party_size": 3, "starts_at_local": f"{D()}T20:00", "table_id": "t_2"}), 200)
    ok(ada.patch(f"/reservations/{ref}", json_={"table_id": "t_3", "party_size": 3}), 200)
    ok(ada.patch(f"/reservations/{ref}", json_={"party_size": 3}), 200)
    ok(ada.post(f"/reservations/{ref}/cancel"), 200)
    ok(ada.post(f"/reservations/{ref}/cancel"), 200)
    err(ada.patch(f"/reservations/{ref}", json_={"party_size": 2}), 409, "reservation_cancelled")
    es = hist(ada, ref)["entries"]
    assert [x["seq"] for x in es] == [1, 2, 3, 4]
    assert [x["event"] for x in es] == ["created", "changed", "changed", "cancelled"]
    assert es[1]["changes"] == [{"field": "starts_at_local", "from": f"{D()}T19:00", "to": f"{D()}T20:00"},
                                {"field": "party_size", "from": 4, "to": 3}], es[1]["changes"]
    assert es[2]["changes"] == [{"field": "table_id", "from": "t_2", "to": "t_3"}]
    assert es[3]["changes"] == []
    ats = [at_minutes(x["at"]) for x in es]
    assert ats == sorted(ats)
    assert all(x["at"].endswith(("+02:00", "+01:00")) for x in es), [x["at"] for x in es]
    assert [x["revision"] for x in es] == [1, 2, 3, 4]


def test_history_terms_snapshots():
    ada, _ = managed()
    r = ok(bk(ada), 201)
    ref = r["reference"]
    ok(publish(ada, pol(D(), reservation_duration_minutes=60)), 201)
    p = ok(ada.patch(f"/reservations/{ref}", json_={"party_size": 3}), 200)
    ok(ada.post(f"/reservations/{ref}/cancel"), 200)
    es = hist(ada, ref)["entries"]
    assert es[0]["accepted_terms"] == r["accepted_terms"] and es[0]["accepted_terms"]["policy_version"] == 0
    assert es[1]["accepted_terms"] == p["accepted_terms"] and es[1]["accepted_terms"]["policy_version"] == 1
    assert es[2]["accepted_terms"] == p["accepted_terms"]
    assert [x["revision"] for x in es] == [1, 2, 3]
    d = ok(ada.get(f"/reservations/{ref}/decision"), 200)
    assert d == {"reference": ref, "revision": 3, "accepted_terms": p["accepted_terms"]}


def test_pair_history():
    r0 = mrest(combinable=[["t_1", "t_2"]])
    reset(fixture(restaurants=[r0]))
    ada = login(ADA)
    r = ok(bk(ada, tables=["t_2", "t_1"], party=5), 201)
    ref = r["reference"]
    es = hist(ada, ref)["entries"]
    assert es[0]["changes"][0] == {"field": "table_ids", "from": None, "to": ["t_1", "t_2"]}, es[0]
    assert [c["field"] for c in es[0]["changes"]] == ["table_ids", "starts_at_local", "party_size"]
    assert ok(ada.patch(f"/reservations/{ref}", json_={"table_ids": ["t_2", "t_1"]}), 200) == r
    assert len(hist(ada, ref)["entries"]) == 1
    ok(ada.patch(f"/reservations/{ref}", json_={"table_id": "t_3"}), 200)
    ok(ada.patch(f"/reservations/{ref}", json_={"table_id": "t_2", "party_size": 4}), 200)
    ok(ada.patch(f"/reservations/{ref}", json_={"table_ids": ["t_2", "t_1"]}), 200)
    es = hist(ada, ref)["entries"]
    assert es[1]["changes"] == [{"field": "table_ids", "from": ["t_1", "t_2"], "to": ["t_3"]}], es[1]
    assert es[2]["changes"] == [{"field": "table_id", "from": "t_3", "to": "t_2"},
                                {"field": "party_size", "from": 5, "to": 4}], es[2]
    assert es[3]["changes"] == [{"field": "table_ids", "from": ["t_2"], "to": ["t_1", "t_2"]}], es[3]


def test_seeded_history():
    seeded = [{"id": "res_a", "reference": "SEEDAA", "user_id": "u_ada", "restaurant_id": "r_anker",
               "table_id": "t_2", "starts_at_local": f"{D()}T19:00", "party_size": 2,
               "created_at": "2026-01-02T03:04:05+00:00"},
              {"id": "res_b", "reference": "SEEDBB", "user_id": "u_ada", "restaurant_id": "r_anker",
               "table_id": "t_3", "starts_at_local": f"{D()}T19:00", "party_size": 2, "status": "cancelled",
               "created_at": "2026-01-02T03:04:05+00:00"}]
    reset(fixture(reservations=seeded))
    ada = login(ADA)
    a = ok(ada.get("/reservations/SEEDAA"), 200)
    assert a["revision"] == 1 and a["accepted_terms"]["policy_version"] == 0
    es = hist(ada, "SEEDAA")["entries"]
    assert len(es) == 1 and es[0]["event"] == "created" and es[0]["revision"] == 1
    assert at_minutes(es[0]["at"]) == dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.timezone.utc)
    assert es[0]["at"].endswith("+01:00"), es[0]["at"]
    es = hist(ada, "SEEDBB")["entries"]
    assert [e["event"] for e in es] == ["created", "cancelled"] and [e["revision"] for e in es] == [1, 1]
    assert ok(ada.get("/reservations/SEEDBB/decision"), 200)["revision"] == 1


# ---------------------------------------------------------------- series

def test_series_precedence():
    seeded = [{"id": "res_p", "reference": "PAST01", "user_id": "u_ada", "restaurant_id": "r_anker",
               "table_id": "t_1", "starts_at_local": f"{D(-2)}T19:00", "party_size": 2},
              {"id": "res_q", "reference": "PASTCX", "user_id": "u_ada", "restaurant_id": "r_anker",
               "table_id": "t_3", "starts_at_local": f"{D(-2)}T19:00", "party_size": 2, "status": "cancelled"}]
    reset(fixture(reservations=seeded))
    ada, bob = login(ADA), login(BOB)
    a = ok(bk(ada), 201)["reference"]
    b = ok(bk(bob, table="t_3", party=2), 201)["reference"]
    body = {"anchor_reference": a, "count": 2, "interval_weeks": 1}
    err(httpx.post(f"{BASE}/series", json=body, headers={"Idempotency-Key": key()}), 401, "unauthenticated")
    err(ada.post("/series", json_=body), 400, "missing_idempotency_key")
    err(ada.post("/series", json_=body, key_="k" * 256), 422, "validation_failed")
    err(ada.post("/series", raw=b"{x", key_=key()), 400, "malformed_request")
    for bad in ({"anchor_reference": 5}, {"count": 1}, {"count": 13}, {"count": True}, {"count": "2"},
                {"count": 2.0}, {"interval_weeks": 0}, {"interval_weeks": 5}, {"interval_weeks": True},
                {"anchor_reference": None}):
        err(ada.post("/series", json_={**body, **bad}, key_=key()), 422, "validation_failed")
    for drop in ("anchor_reference", "count", "interval_weeks"):
        err(ada.post("/series", json_={k: v for k, v in body.items() if k != drop}, key_=key()), 422,
            "validation_failed")
    err(series(ada, "NOPE0000", count=1), 422, "validation_failed")   # validation before 404
    err(series(ada, "NOPE0000"), 404, "not_found")
    err(series(ada, b), 404, "not_found")
    err(series(ada, "PASTCX"), 409, "reservation_cancelled")          # cancelled before cutoff
    err(series(ada, "PAST01"), 409, "cutoff_passed")
    k = key()
    s = ok(series(ada, a, count=3, k=k), 201)
    assert ok(series(ada, a, count=3, k=k), 200) == s
    err(series(ada, a, count=4, k=k), 409, "idempotency_key_reuse")
    err(series(ada, a), 409, "already_in_series")
    err(series(ada, s["occurrences"][1]["reference"]), 409, "already_in_series")
    c = ok(bk(ada, at="21:00", table="t_1", party=2), 201)["reference"]
    ok(ada.post(f"/reservations/{c}/cancel"), 200)
    err(series(ada, c), 409, "reservation_cancelled")


def test_series_success():
    reset(fixture())
    ada, bob = login(ADA), login(BOB)
    anchor = ok(bk(ada), 201)
    h0 = hist(ada, anchor["reference"])
    s = ok(series(ada, anchor["reference"], count=3, interval=2, note="ignored"), 201)
    assert isinstance(s["series_id"], str) and s["revision"] == 1 and s["interval_weeks"] == 2
    occ = s["occurrences"]
    assert [o["index"] for o in occ] == [0, 1, 2] and all(o["exception"] is False for o in occ)
    assert len({o["reference"] for o in occ}) == 3
    assert occ[0]["reference"] == anchor["reference"] and occ[0]["reservation"] == anchor
    for i, o in enumerate(occ[1:], start=1):
        r = o["reservation"]
        assert r["reference"] == o["reference"]
        assert r["starts_at_local"] == f"{D(7 + 14 * i)}T19:00", r["starts_at_local"]
        assert r["table_id"] == "t_2" and r["party_size"] == 4 and r["revision"] == 1 and r["status"] == "confirmed"
        es = hist(ada, o["reference"])["entries"]
        assert [e["event"] for e in es] == ["created"]
    assert ok(ada.get(f"/reservations/{anchor['reference']}"), 200) == anchor
    assert hist(ada, anchor["reference"]) == h0
    assert len(ok(ada.get("/reservations"), 200)["reservations"]) == 3
    err(bk(bob, D(21), table="t_2", party=2), 409, "table_unavailable")
    assert ok(ada.get(f"/series/{s['series_id']}"), 200) == s


def test_series_per_occurrence_policy_and_atomicity():
    ada, bob = managed()
    ok(publish(ada, pol(D(14), reservation_duration_minutes=60)), 201)                               # v1
    ok(publish(ada, pol(D(21), capacities={"t_1": 2, "t_2": 4, "t_3": 4})), 201)                     # v2
    ok(publish(ada, pol(D(28), opening_hours=[])), 201)                                               # v3
    a1 = ok(bk(ada, D(7), table="t_2", party=4), 201)
    s = ok(series(ada, a1["reference"], count=2), 201)
    r1 = s["occurrences"][1]["reservation"]
    assert r1["accepted_terms"]["policy_version"] == 1 and r1["ends_at"].startswith(f"{D(14)}T20:00")
    before = len(ok(ada.get("/reservations"), 200)["reservations"])
    # capacity fails at index 2 (v2: t_3 = 4) before closed day at index 3
    a2 = ok(bk(ada, D(7), table="t_3", party=5), 201)
    k = key()
    err(series(ada, a2["reference"], count=4, k=k), 422, "party_exceeds_capacity")
    assert len(ok(ada.get("/reservations"), 200)["reservations"]) == before + 1
    # key unused after failure; nothing was adopted
    ok(series(ada, a2["reference"], count=2, k=k), 201)
    # closed day at index 3
    a3 = ok(bk(ada, D(7), at="21:00", table="t_1", party=2), 201)
    err(series(ada, a3["reference"], count=4), 422, "outside_opening_hours")
    # occupancy: an existing booking at index 1 -> table_unavailable, nothing created
    ok(bk(bob, D(14), at="21:00", table="t_1", party=2), 201)
    n = len(ok(ada.get("/reservations"), 200)["reservations"])
    err(series(ada, a3["reference"], count=2), 409, "table_unavailable")
    assert len(ok(ada.get("/reservations"), 200)["reservations"]) == n
    assert ok(ada.get(f"/reservations/{a3['reference']}"), 200) == a3


def test_series_dst():
    night = mrest(hours=all_week("00:00", "23:30"))
    ny = mrest("r_ny", tz="America/New_York", hours=all_week("00:00", "23:30"))
    reset(fixture(restaurants=[night, ny]))
    ada = login(ADA)
    a = ok(bk(ada, "2026-10-18", at="02:30", table="t_2", party=2), 201)
    s = ok(series(ada, a["reference"], count=2), 201)
    r1 = s["occurrences"][1]["reservation"]
    assert r1["starts_at"] == "2026-10-25T02:30:00+02:00", r1["starts_at"]
    assert r1["ends_at"] == "2026-10-25T03:00:00+01:00", r1["ends_at"]
    b = ok(bk(ada, "2027-03-21", at="02:30", table="t_2", party=2), 201)
    err(series(ada, b["reference"], count=3), 422, "invalid_local_time")
    c = ok(bk(ada, "2027-03-07", at="02:00", table="t_2", party=2, rid="r_ny"), 201)
    err(series(ada, c["reference"], count=2), 422, "invalid_local_time")
    assert len(ok(ada.get("/reservations"), 200)["reservations"]) == 4


def test_series_read_and_replay():
    reset(fixture())
    ada, bob = login(ADA), login(BOB)
    anchor = ok(bk(ada), 201)
    k = key()
    s = ok(series(ada, anchor["reference"], count=2, k=k), 201)
    sid = s["series_id"]
    err(bob.get(f"/series/{sid}"), 404, "not_found")
    err(httpx.get(f"{BASE}/series/{sid}"), 404, "not_found")
    err(ada.get("/series/nope"), 404, "not_found")
    ok(ada.patch(f"/reservations/{s['occurrences'][1]['reference']}", json_={"party_size": 3}), 200)
    cur = ok(ada.get(f"/series/{sid}"), 200)
    assert cur["revision"] == 2 and cur["occurrences"][1]["exception"] is True
    assert cur["occurrences"][1]["reservation"]["party_size"] == 3
    assert ok(series(ada, anchor["reference"], count=2, k=k), 200) == s
    assert ok(ada.get(f"/series/{sid}"), 200) == cur


def test_series_revision_rules():
    reset(fixture())
    ada = login(ADA)
    anchor = ok(bk(ada), 201)
    s = ok(series(ada, anchor["reference"], count=3), 201)
    sid = s["series_id"]
    r = [o["reference"] for o in s["occurrences"]]

    def cur():
        return ok(ada.get(f"/series/{sid}"), 200)
    ok(ada.patch(f"/reservations/{r[1]}", json_={"party_size": 4}), 200)          # no-op
    err(ada.patch(f"/reservations/{r[1]}", json_={"party_size": 99}), 422, "party_exceeds_capacity")
    assert cur()["revision"] == 1 and not cur()["occurrences"][1]["exception"]
    ok(ada.patch(f"/reservations/{r[1]}", json_={"party_size": 3}), 200)
    assert cur()["revision"] == 2 and cur()["occurrences"][1]["exception"] is True
    ok(ada.post(f"/reservations/{r[2]}/cancel"), 200)
    c = cur()
    assert c["revision"] == 3 and c["occurrences"][2]["exception"] is False
    assert c["occurrences"][2]["reservation"]["status"] == "cancelled"
    ok(ada.post(f"/reservations/{r[2]}/cancel"), 200)
    assert cur()["revision"] == 3
    ok(ada.post(f"/reservations/{r[0]}/cancel"), 200)
    c = cur()
    assert c["revision"] == 4 and c["occurrences"][1]["reservation"]["status"] == "confirmed"
    ok(ada.patch(f"/reservations/{r[1]}", json_={"party_size": 2}), 200)
    c = cur()
    assert c["revision"] == 5 and c["occurrences"][1]["exception"] is True
    assert [o["index"] for o in c["occurrences"]] == [0, 1, 2] and [o["reference"] for o in c["occurrences"]] == r


def test_series_pair_anchor():
    reset(fixture(restaurants=[mrest(combinable=[["t_1", "t_2"]])]))
    ada = login(ADA)
    a = ok(bk(ada, tables=["t_2", "t_1"], party=5), 201)
    s = ok(series(ada, a["reference"], count=2), 201)
    r1 = s["occurrences"][1]["reservation"]
    assert r1["table_ids"] == ["t_1", "t_2"] and "table_id" not in r1
    es = hist(ada, r1["reference"])["entries"]
    assert es[0]["changes"][0] == {"field": "table_ids", "from": None, "to": ["t_1", "t_2"]}


# ---------------------------------------------------------------- moves

def mv(c, moves, k=None):
    return c.post("/reservation-moves", json_={"moves": moves}, key_=k or key())


def test_moves_under_policies():
    ada, _ = managed()
    a = ok(bk(ada, table="t_1", party=2), 201)
    b = ok(bk(ada, table="t_3", party=2), 201)
    err(mv(ada, [{"reference": a["reference"], "expected_revision": "x"}]), 422, "validation_failed")
    err(mv(ada, [{"reference": a["reference"], "expected_revision": 0}]), 422, "validation_failed")
    err(mv(ada, [{"reference": b["reference"]}, {"reference": a["reference"], "table_id": "t_2",
                                                 "expected_revision": 2}]), 409, "stale_revision")
    ok(publish(ada, pol(D(14), reservation_duration_minutes=60)), 201)
    res = ok(mv(ada, [{"reference": a["reference"], "starts_at_local": f"{D(14)}T19:00", "expected_revision": 1},
                      {"reference": b["reference"], "party_size": 2}]), 201)["reservations"]
    assert res[0]["revision"] == 2 and res[0]["accepted_terms"]["policy_version"] == 1
    assert res[0]["ends_at"].startswith(f"{D(14)}T20:00")
    assert res[1] == b
    ea = hist(ada, a["reference"])["entries"]
    assert [e["event"] for e in ea] == ["created", "changed"]
    assert ea[1]["changes"] == [{"field": "starts_at_local", "from": f"{D()}T19:00", "to": f"{D(14)}T19:00"}]
    assert len(hist(ada, b["reference"])["entries"]) == 1
    err(mv(ada, [{"reference": a["reference"], "table_id": "t_2"}, {"reference": b["reference"], "party_size": 99}]),
        422, "party_exceeds_capacity")
    assert ok(ada.get(f"/reservations/{a['reference']}"), 200) == res[0]
    # series: one batch changing two occurrences -> series revision +1 once, both exceptions
    anchor = ok(bk(ada, at="21:00", table="t_2", party=2), 201)
    s = ok(series(ada, anchor["reference"], count=3), 201)
    refs = [o["reference"] for o in s["occurrences"]]
    ok(mv(ada, [{"reference": refs[1], "party_size": 2}, {"reference": refs[2], "party_size": 2}]), 201)  # no-ops
    assert ok(ada.get(f"/series/{s['series_id']}"), 200)["revision"] == 1
    k = key()
    ok(mv(ada, [{"reference": refs[1], "party_size": 3}, {"reference": refs[2], "party_size": 3}], k=k), 201)
    cur = ok(ada.get(f"/series/{s['series_id']}"), 200)
    assert cur["revision"] == 2 and [o["exception"] for o in cur["occurrences"]] == [False, True, True]
    ok(mv(ada, [{"reference": refs[1], "party_size": 3}, {"reference": refs[2], "party_size": 3}], k=k), 200)
    assert ok(ada.get(f"/series/{s['series_id']}"), 200) == cur


# ---------------------------------------------------------------- export / import / upgrade

def test_stage3_roundtrip():
    reset(fixture(restaurants=[mrest(combinable=[["t_1", "t_2"]])]))
    ada, bob = login(ADA), login(BOB)
    kp, kb, ks = key(), key(), key()
    p1 = ok(publish(ada, pol(D(14), reservation_duration_minutes=60), k=kp), 201)
    r = ok(bk(ada, k=kb), 201)
    ok(ada.patch(f"/reservations/{r['reference']}", json_={"party_size": 3}), 200)
    pair = ok(bk(ada, at="21:00", tables=["t_1", "t_2"], party=5), 201)
    s = ok(series(ada, r["reference"], count=3, k=ks), 201)
    ok(ada.patch(f"/reservations/{s['occurrences'][1]['reference']}", json_={"party_size": 2}), 200)
    ok(ada.post(f"/reservations/{s['occurrences'][2]['reference']}/cancel"), 200)
    refs = [r["reference"], pair["reference"]] + [o["reference"] for o in s["occurrences"][1:]]
    views = {x: (ok(ada.get(f"/reservations/{x}"), 200), hist(ada, x), ok(ada.get(f"/reservations/{x}/decision"), 200))
             for x in refs}
    cur_series = ok(ada.get(f"/series/{s['series_id']}"), 200)
    pols = ok(httpx.get(f"{BASE}/restaurants/r_anker/policies"), 200)
    snap = ok(httpx.get(f"{BASE}/_test/export"), 200)
    assert snap["state"].get("schema") not in (1, 2), "stage-3 exports need a new schema marker"
    reset(fixture(users=[], restaurants=[]))
    ok(imp(snap), 204)
    ok(imp(snap), 204)
    for x in refs:
        assert (ok(ada.get(f"/reservations/{x}"), 200), hist(ada, x),
                ok(ada.get(f"/reservations/{x}/decision"), 200)) == views[x], x
    assert ok(ada.get(f"/series/{s['series_id']}"), 200) == cur_series
    assert ok(httpx.get(f"{BASE}/restaurants/r_anker/policies"), 200) == pols
    assert ok(publish(ada, pol(D(14)), k=kp), 200) == p1
    assert ok(bk(ada, k=kb), 200) == r
    assert ok(series(ada, r["reference"], count=3, k=ks), 200) == s
    err(publish(bob, pol(D())), 403, "forbidden")
    assert ok(publish(ada, pol(D())), 201)["policy_version"] == 2
    err(series(ada, r["reference"]), 409, "already_in_series")
    ok(ada.patch(f"/reservations/{s['occurrences'][1]['reference']}", json_={"party_size": 3}), 200)
    assert ok(ada.get(f"/series/{s['series_id']}"), 200)["revision"] == cur_series["revision"] + 1


def _upgrade_common(prev, fx_restaurant, pair=False):
    reset(fixture(restaurants=[fx_restaurant]), base=prev)
    ada, bob = login(ADA, prev), login(BOB, prev)
    k1 = key()
    body = {"restaurant_id": "r_anker", "starts_at_local": f"{D()}T19:00", "party_size": 4}
    body.update({"table_ids": ["t_2", "t_1"]} if pair else {"table_id": "t_2"})
    b1 = ok(ada.post("/reservations", json_=body, key_=k1), 201)
    b2 = ok(bob.post("/reservations", json_={"restaurant_id": "r_anker", "table_id": "t_3",
                                             "starts_at_local": f"{D()}T19:00", "party_size": 2}, key_=key()), 201)
    ok(bob.post(f"/reservations/{b2['reference']}/cancel"), 200)
    snap = ok(httpx.get(f"{prev}/_test/export"), 200)
    reset(fixture(users=[], restaurants=[]))
    ok(imp(snap), 204)
    ada3, bob3 = C(BASE, ada.token), C(BASE, bob.token)
    login(ADA)
    g = ok(ada3.get(f"/reservations/{b1['reference']}"), 200)
    for f in ("reservation_id", "reference", "starts_at", "ends_at", "created_at", "status", "party_size"):
        assert g[f] == b1[f], f
    assert g["revision"] == 1 and g["accepted_terms"]["policy_version"] == 0
    assert g["accepted_terms"]["reservation_duration_minutes"] == 90
    assert ok(ada3.post("/reservations", json_=body, key_=k1), 200) == b1   # original body, verbatim
    es = hist(ada3, b1["reference"])["entries"]
    assert len(es) == 1 and es[0]["event"] == "created" and es[0]["revision"] == 1
    assert at_minutes(es[0]["at"]) == at_minutes(b1["created_at"])
    assert es[0]["changes"][0]["field"] == ("table_ids" if pair else "table_id")
    es2 = hist(bob3, b2["reference"])["entries"]
    assert [e["event"] for e in es2] == ["created", "cancelled"] and [e["revision"] for e in es2] == [1, 1]
    s = ok(series(ada3, b1["reference"], count=2), 201)
    assert s["occurrences"][0]["reservation"]["reference"] == b1["reference"]
    assert ok(ada3.get(f"/reservations/{b1['reference']}/decision"), 200)["revision"] == 1


def test_upgrade_from_stage1():
    if not PREV1:
        pytest.skip("PROBE_BASE_URL_PREV1 not set")
    _upgrade_common(PREV1, restaurant())


def test_upgrade_from_stage2():
    if not PREV2:
        pytest.skip("PROBE_BASE_URL_PREV2 not set")
    r = restaurant()
    r["combinable"] = [["t_1", "t_2"]]
    _upgrade_common(PREV2, r, pair=True)


def test_concurrent_policy_publication():
    ada, _ = managed()

    def go(i):
        return publish(ada, pol(D(), slot_minutes=10 + i))
    with ThreadPoolExecutor(20) as ex:
        out = list(ex.map(go, range(20)))
    assert all(r.status_code == 201 for r in out), [r.status_code for r in out]
    assert sorted(r.json()["policy_version"] for r in out) == list(range(1, 21))
    k = key()
    with ThreadPoolExecutor(10) as ex:
        out = list(ex.map(lambda _: publish(ada, pol(D(1)), k=k), range(10)))
    codes = sorted(r.status_code for r in out)
    assert codes.count(201) == 1 and codes.count(200) == 9, codes
    assert len(ok(httpx.get(f"{BASE}/restaurants/r_anker/policies"), 200)["policies"]) == 21
