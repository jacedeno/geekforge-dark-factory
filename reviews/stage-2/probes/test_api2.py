"""Verifier API probes for stage-2 (combined tables, upgrade import), from plans/stage-2/brief.md.

PROBE_BASE_URL (stage-2 service), PROBE_BASE_URL_PREV (a stage-1 service, for the upgrade probe).
Helpers come from the stage-1 probes.
"""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "stage-1", "probes"))
from test_probes import (ADA, BOB, BASE, C, all_week, day, err, fixture, imp, key, login,  # noqa: E402
                         ok, reset, restaurant)

PREV = os.environ.get("PROBE_BASE_URL_PREV")


def rest2(rid="r_anker", combinable=None, tables=None, **kw):
    r = restaurant(rid, tables=tables or [
        {"id": "t_1", "label": "1", "capacity": 2},
        {"id": "t_2", "label": "2", "capacity": 4},
        {"id": "t_3", "label": "3", "capacity": 6},
        {"id": "t_4", "label": "4", "capacity": 2}], **kw)
    r["combinable"] = [["t_1", "t_2"], ["t_3", "t_2"]] if combinable is None else combinable
    return r


def world2(**kw):
    reset(fixture(restaurants=[rest2(**kw)]))
    return login(ADA), login(BOB)


def bk(c, tables=None, at="19:00", party=4, d=None, rid="r_anker", k=None, table=None, **extra):
    body = {"restaurant_id": rid, "starts_at_local": f"{d or day()}T{at}", "party_size": party, **extra}
    if tables is not None:
        body["table_ids"] = tables
    if table is not None:
        body["table_id"] = table
    return c.post("/reservations", json_=body, key_=k or key())


def slots(date=None, party=2, rid="r_anker"):
    r = httpx.get(f"{BASE}/availability", params={"restaurant_id": rid, "date": date or day(),
                                                  "party_size": party})
    return {s["starts_at_local"][-5:]: s for s in ok(r, 200)["slots"]}


# ---------------------------------------------------------------- screens / conventions

@pytest.mark.parametrize("route", ["/", "/signup", "/login", "/lookup"])
def test_screen_routes_html(route):
    reset(fixture())
    r = httpx.get(f"{BASE}{route}")
    assert r.status_code == 200
    assert r.headers["content-type"].replace(" ", "").lower() == "text/html;charset=utf-8", r.headers
    assert "<html" in r.text.lower()


def test_api_still_json_and_unknown_routes_404():
    reset(fixture())
    err(httpx.get(f"{BASE}/nope"), 404, "not_found")
    ct = httpx.get(f"{BASE}/restaurants").headers["content-type"].replace(" ", "").lower()
    assert ct == "application/json;charset=utf-8"


# ---------------------------------------------------------------- model / reset

def test_detail_combinable():
    reset(fixture(restaurants=[rest2(), restaurant("r_plain")]))
    d = ok(httpx.get(f"{BASE}/restaurants/r_anker"), 200)
    assert d["combinable"] == [["t_1", "t_2"], ["t_3", "t_2"]]
    assert ok(httpx.get(f"{BASE}/restaurants/r_plain"), 200)["combinable"] == []


@pytest.mark.parametrize("comb", [[["t_1"]], [["t_1", "t_1"]], [["t_1", "t_9"]], [["t_1", "t_2", "t_3"]],
                                  "x", [["t_1", 2]], [[]]])
def test_reset_combinable_validation(comb):
    reset(fixture())
    r = httpx.post(f"{BASE}/_test/reset", json=fixture(restaurants=[rest2(combinable=comb)]))
    err(r, 422, "validation_failed")
    assert httpx.get(f"{BASE}/restaurants/r_anker").json().get("combinable") == []


def test_reset_pair_of_other_restaurant_422():
    other = rest2("r_two", combinable=[], tables=[{"id": "t_x", "label": "X", "capacity": 4}])
    r = httpx.post(f"{BASE}/_test/reset", json=fixture(restaurants=[rest2(combinable=[["t_1", "t_x"]]), other]))
    err(r, 422, "validation_failed")


def test_seeded_table_ids_and_status():
    d = day()
    seeded = [
        {"id": "res_p", "reference": "PAIR01", "user_id": "u_ada", "restaurant_id": "r_anker",
         "table_ids": ["t_2", "t_1"], "starts_at_local": f"{d}T19:00", "party_size": 5},
        {"id": "res_c", "reference": "CANC01", "user_id": "u_ada", "restaurant_id": "r_anker",
         "table_id": "t_3", "starts_at_local": f"{d}T19:00", "party_size": 2, "status": "cancelled"},
    ]
    reset(fixture(restaurants=[rest2()], reservations=seeded))
    ada = login(ADA)
    p = ok(ada.get("/reservations/PAIR01"), 200)
    assert p["table_ids"] == ["t_1", "t_2"] and "table_id" not in p and p["status"] == "confirmed"
    c = ok(ada.get("/reservations/CANC01"), 200)
    assert c["status"] == "cancelled" and c["table_ids"] == ["t_3"] and c["table_id"] == "t_3"
    s = slots(d, 2)["19:00"]
    assert s["available_table_ids"] == ["t_3", "t_4"], s
    bad = dict(seeded[1], status="pending")
    err(httpx.post(f"{BASE}/_test/reset", json=fixture(restaurants=[rest2()], reservations=[bad])),
        422, "validation_failed")
    both = dict(seeded[0], table_id="t_1")
    err(httpx.post(f"{BASE}/_test/reset", json=fixture(restaurants=[rest2()], reservations=[both])),
        422, "validation_failed")


# ---------------------------------------------------------------- availability

def test_available_options_rules():
    ada, _ = world2()
    d = day()
    s = slots(d, 5)["19:00"]
    assert s["available_table_ids"] == ["t_3"]
    assert s["available_options"] == [{"table_ids": ["t_3"], "capacity": 6},
                                      {"table_ids": ["t_1", "t_2"], "capacity": 6},
                                      {"table_ids": ["t_3", "t_2"], "capacity": 10}], s
    s = slots(d, 2)["19:00"]
    assert [o["table_ids"] for o in s["available_options"]] == [["t_1"], ["t_2"], ["t_3"], ["t_4"],
                                                              ["t_1", "t_2"], ["t_3", "t_2"]]
    # t_1 + t_4 is not declared: never offered even though 2+2 >= 3
    s = slots(d, 3)["19:00"]
    assert ["t_1", "t_4"] not in [o["table_ids"] for o in s["available_options"]]
    # booking t_2 removes both pairs containing it
    ok(bk(ada, table="t_2", party=2), 201)
    s = slots(d, 2)["19:00"]
    assert [o["table_ids"] for o in s["available_options"]] == [["t_1"], ["t_3"], ["t_4"]], s
    assert s["available_table_ids"] == ["t_1", "t_3", "t_4"]
    # overlapping (not equal) slot is also affected
    s = slots(d, 2)["20:00"]
    assert ["t_1", "t_2"] not in [o["table_ids"] for o in s["available_options"]]
    # capacity 7 only via pair 3+2 -> gone now; party 11 -> no options, slot kept
    assert slots(d, 11)["19:00"]["available_options"] == []


# ---------------------------------------------------------------- create

def test_create_pair_shape():
    ada, _ = world2()
    r = ok(bk(ada, tables=["t_2", "t_1"], party=6), 201)
    assert r["table_ids"] == ["t_1", "t_2"] and "table_id" not in r
    assert set(r) == {"reservation_id", "reference", "restaurant_id", "table_ids", "party_size", "status",
                      "starts_at_local", "starts_at", "ends_at", "created_at"}, set(r)
    r2 = ok(bk(ada, tables=["t_2", "t_3"], party=10, at="21:00"), 201)
    assert r2["table_ids"] == ["t_3", "t_2"]
    ada2 = ok(ada.get(f"/reservations/{r['reference']}"), 200)
    assert ada2 == r
    assert all("table_ids" in x for x in ok(ada.get("/reservations"), 200)["reservations"])


def test_create_single_shapes():
    ada, _ = world2()
    a = ok(bk(ada, table="t_2", party=2), 201)
    b = ok(bk(ada, tables=["t_3"], party=2), 201)
    for r, t in ((a, "t_2"), (b, "t_3")):
        assert r["table_id"] == t and r["table_ids"] == [t], r


@pytest.mark.parametrize("extra,status,code", [
    ({"table_id": "t_1", "table_ids": ["t_1", "t_2"]}, 422, "validation_failed"),
    ({"table_id": "t_1", "table_ids": ["t_1"]}, 422, "validation_failed"),
    ({}, 422, "validation_failed"),
    ({"table_ids": "t_1"}, 400, "malformed_request"),
    ({"table_ids": ["t_1", 2]}, 400, "malformed_request"),
    ({"table_ids": {"a": "t_1"}}, 400, "malformed_request"),
    ({"table_ids": []}, 422, "validation_failed"),
    ({"table_ids": ["t_1", "t_1"]}, 422, "validation_failed"),
    ({"table_ids": ["t_1", "t_2", "t_3"]}, 422, "combination_not_allowed"),
    ({"table_ids": ["t_1", "t_9"]}, 404, "not_found"),
    ({"table_ids": ["t_9"]}, 404, "not_found"),
    ({"table_ids": ["t_1", "t_4"]}, 422, "combination_not_allowed"),
    ({"table_ids": ["t_1", "t_3"]}, 422, "combination_not_allowed"),
])
def test_table_ids_error_matrix(extra, status, code):
    ada, _ = world2()
    body = {"restaurant_id": "r_anker", "starts_at_local": f"{day()}T19:00", "party_size": 2, **extra}
    err(ada.post("/reservations", json_=body, key_=key()), status, code)


def test_table_ids_precedence():
    reset(fixture(restaurants=[rest2(), rest2("r_two", combinable=[],
                                             tables=[{"id": "t_x", "label": "X", "capacity": 9}])]))
    ada = login(ADA)
    # >2 beats unknown table
    err(bk(ada, tables=["t_9", "t_8", "t_7"], party=2), 422, "combination_not_allowed")
    # unknown/foreign beats undeclared pair
    err(bk(ada, tables=["t_1", "t_x"], party=2), 404, "not_found")
    # undeclared pair beats DST/hours/grid/capacity
    err(bk(ada, tables=["t_1", "t_4"], party=99, at="03:17"), 422, "combination_not_allowed")
    # hours before capacity; capacity uses the sum
    err(bk(ada, tables=["t_1", "t_2"], party=7), 422, "party_exceeds_capacity")
    ok(bk(ada, tables=["t_1", "t_2"], party=6), 201)
    err(bk(ada, tables=["t_1", "t_2"], party=99, at="17:00"), 422, "outside_opening_hours")
    err(bk(ada, tables=["t_1", "t_2"], party=6, at="19:30"), 409, "table_unavailable")
    err(bk(ada, tables=["t_1", "t_2"], party=2, d="2026-03-29", at="02:30"), 422, "invalid_local_time")
    # missing required beats both-present
    err(ada.post("/reservations", json_={"restaurant_id": "r_anker", "table_id": "t_1", "table_ids": ["t_1"],
                                         "party_size": 2}, key_=key()), 422, "validation_failed")


def test_pair_occupancy():
    ada, bob = world2()
    d = day()
    p = ok(bk(ada, tables=["t_1", "t_2"], party=5), 201)
    err(bk(bob, table="t_1", party=2, at="20:00"), 409, "table_unavailable")
    err(bk(bob, table="t_2", party=2, at="18:00"), 409, "table_unavailable")
    ok(bk(bob, table="t_1", party=2, at="20:30"), 201)  # half-open: 19:00+90 = 20:30
    err(bk(bob, tables=["t_3", "t_2"], party=2, at="19:30"), 409, "table_unavailable")
    ok(ada.post(f"/reservations/{p['reference']}/cancel"), 200)
    s = slots(d, 2)["19:00"]
    assert "t_1" in s["available_table_ids"] and "t_2" in s["available_table_ids"]
    ok(bk(bob, tables=["t_3", "t_2"], party=2, at="19:30"), 201)


def test_pair_idempotency():
    ada, _ = world2()
    k = key()
    first = ok(bk(ada, tables=["t_2", "t_1"], party=5, k=k), 201)
    assert ok(bk(ada, tables=["t_2", "t_1"], party=5, k=k), 200) == first
    err(bk(ada, tables=["t_1", "t_2"], party=5, k=k), 409, "idempotency_key_reuse")  # different JSON value
    ok(ada.post(f"/reservations/{first['reference']}/cancel"), 200)
    assert ok(bk(ada, tables=["t_2", "t_1"], party=5, k=k), 200) == first


# ---------------------------------------------------------------- patch / moves

def test_patch_table_ids():
    ada, bob = world2()
    r = ok(bk(ada, table="t_1", party=2), 201)
    ref = r["reference"]
    p = ok(ada.patch(f"/reservations/{ref}", json_={"table_ids": ["t_2", "t_1"], "party_size": 6}), 200)
    assert p["table_ids"] == ["t_1", "t_2"] and "table_id" not in p and p["reference"] == ref
    err(ada.patch(f"/reservations/{ref}", json_={"table_ids": ["t_1", "t_4"]}), 422, "combination_not_allowed")
    err(ada.patch(f"/reservations/{ref}", json_={"table_ids": ["t_1", "t_2"], "table_id": "t_1"}), 422,
        "validation_failed")
    err(ada.patch(f"/reservations/{ref}", json_={"table_ids": "t_1"}), 400, "malformed_request")
    err(ada.patch(f"/reservations/{ref}", json_={"table_id": "t_1"}), 422, "party_exceeds_capacity")
    ok(bk(bob, table="t_3", party=2, at="20:00"), 201)
    err(ada.patch(f"/reservations/{ref}", json_={"table_ids": ["t_3", "t_2"]}), 409, "table_unavailable")
    assert ok(ada.get(f"/reservations/{ref}"), 200) == p
    # overlap with itself is fine: shift a pair by one slot
    ok(ada.patch(f"/reservations/{ref}", json_={"starts_at_local": f"{day()}T19:30"}), 200)
    s = ok(ada.patch(f"/reservations/{ref}", json_={"table_id": "t_2", "party_size": 4}), 200)
    assert s["table_id"] == "t_2" and s["table_ids"] == ["t_2"]
    assert "t_1" in slots(day(), 2)["19:30"]["available_table_ids"]


def test_moves_table_ids():
    ada, bob = world2()
    a = ok(bk(ada, table="t_1", party=2), 201)
    b = ok(bk(ada, table="t_3", party=2), 201)
    res = ok(ada.post("/reservation-moves", json_={"moves": [
        {"reference": a["reference"], "table_ids": ["t_2", "t_1"], "party_size": 5},
        {"reference": b["reference"]}]}, key_=key()), 201)["reservations"]
    assert res[0]["table_ids"] == ["t_1", "t_2"] and "table_id" not in res[0]
    assert res[1]["table_id"] == "t_3"
    err(ada.post("/reservation-moves", json_={"moves": [{"reference": a["reference"], "table_id": "t_1",
                                                         "table_ids": ["t_1"]}]}, key_=key()),
        422, "validation_failed")
    # resulting bookings share t_2 -> 409, nothing changes
    err(ada.post("/reservation-moves", json_={"moves": [
        {"reference": a["reference"], "table_id": "t_2", "party_size": 2},
        {"reference": b["reference"], "table_ids": ["t_3", "t_2"]}]}, key_=key()), 409, "table_unavailable")
    assert ok(ada.get(f"/reservations/{b['reference']}"), 200) == b
    # swap: pair t_1+t_2 -> t_3; single t_3 -> pair t_1+t_2
    res = ok(ada.post("/reservation-moves", json_={"moves": [
        {"reference": a["reference"], "table_id": "t_3"},
        {"reference": b["reference"], "table_ids": ["t_1", "t_2"]}]}, key_=key()), 201)["reservations"]
    assert res[0]["table_ids"] == ["t_3"] and res[1]["table_ids"] == ["t_1", "t_2"]
    # an undeclared pair in a move
    ok(bk(bob, table="t_4", party=2, at="20:00"), 201)
    err(ada.post("/reservation-moves", json_={"moves": [
        {"reference": a["reference"], "table_ids": ["t_1", "t_4"]}]}, key_=key()), 422, "combination_not_allowed")


def test_concurrent_pair_vs_singles():
    users = [{"id": f"u_{i}", "email": f"q{i}@x.io", "password": "correct horse", "display_name": "Q"}
             for i in range(30)]
    reset(fixture(users=users, restaurants=[rest2()]))
    cs = [login({"email": u["email"], "password": u["password"]}) for u in users]

    def go(i):
        body = {"restaurant_id": "r_anker", "starts_at_local": f"{day()}T19:00", "party_size": 2}
        if i % 3 == 0:
            body["table_ids"] = ["t_1", "t_2"]
        elif i % 3 == 1:
            body["table_id"] = "t_1"
        else:
            body["table_ids"] = ["t_3", "t_2"]
        return cs[i].post("/reservations", json_=body, key_=key()).status_code
    with ThreadPoolExecutor(30) as ex:
        codes = list(ex.map(go, range(30)))
    assert set(codes) <= {201, 409}, codes
    ada_view = [x for c in cs for x in ok(c.get("/reservations"), 200)["reservations"]]
    used = {}
    for r in ada_view:
        for t in r["table_ids"]:
            used[t] = used.get(t, 0) + 1
    assert all(v == 1 for v in used.values()), used
    assert codes.count(201) in (1, 2), codes  # either one pair containing t_2, or t_1 alone + t_3+t_2


# ---------------------------------------------------------------- export / upgrade

def test_stage2_roundtrip():
    ada, bob = world2()
    k = key()
    p = ok(bk(ada, tables=["t_2", "t_1"], party=5, k=k), 201)
    c = ok(bk(ada, table="t_3", party=2), 201)
    ok(ada.post(f"/reservations/{c['reference']}/cancel"), 200)
    snap = ok(httpx.get(f"{BASE}/_test/export"), 200)
    assert snap["format_version"] == 1 and snap["track"] == "tablekeeper"
    reset(fixture(users=[], restaurants=[]))
    ok(imp(snap), 204)
    assert ok(ada.get(f"/reservations/{p['reference']}"), 200) == p
    assert ok(bk(ada, tables=["t_2", "t_1"], party=5, k=k), 200) == p
    assert ok(httpx.get(f"{BASE}/restaurants/r_anker"), 200)["combinable"] == [["t_1", "t_2"], ["t_3", "t_2"]]
    assert ok(ada.get(f"/reservations/{c['reference']}"), 200)["status"] == "cancelled"


def test_upgrade_from_stage1():
    if not PREV:
        pytest.skip("PROBE_BASE_URL_PREV not set")
    reset(fixture(), base=PREV)
    ada, bob = login(ADA, PREV), login(BOB, PREV)
    d = day()
    k1, kf, km = key(), key(), key()
    body = {"restaurant_id": "r_anker", "table_id": "t_2", "starts_at_local": f"{d}T19:00", "party_size": 4}
    b1 = ok(ada.post("/reservations", json_=body, key_=k1), 201)
    err(ada.post("/reservations", json_={**body, "table_id": "t_1"}, key_=kf), 422, "party_exceeds_capacity")
    b2 = ok(bob.post("/reservations", json_={**body, "table_id": "t_3", "party_size": 2}, key_=key()), 201)
    mv = ok(bob.post("/reservation-moves", json_={"moves": [{"reference": b2["reference"], "table_id": "t_1"}]},
                     key_=km), 201)
    ok(bob.post(f"/reservations/{b2['reference']}/cancel"), 200)
    snap = ok(httpx.get(f"{PREV}/_test/export"), 200)

    reset(fixture(users=[], restaurants=[]))
    ok(imp(snap), 204)
    ada2, bob2 = C(BASE, ada.token), C(BASE, bob.token)
    login(ADA)
    got = ok(ada2.get(f"/reservations/{b1['reference']}"), 200)
    assert got == {**b1, "table_ids": ["t_2"]}, got
    # replay returns the ORIGINAL stage-1 response (no table_ids added), 200
    assert ok(ada2.post("/reservations", json_=body, key_=k1), 200) == b1
    err(ada2.post("/reservations", json_={**body, "party_size": 3}, key_=k1), 409, "idempotency_key_reuse")
    assert ok(bob2.post("/reservation-moves", json_={"moves": [{"reference": b2["reference"], "table_id": "t_1"}]},
                        key_=km), 200) == mv
    assert ok(bob2.get(f"/reservations/{b2['reference']}"), 200)["status"] == "cancelled"
    # the failed key is reusable
    ok(ada2.post("/reservations", json_={**body, "table_id": "t_3"}, key_=kf), 201)
    # combinable defaults to [] and pairs are refused
    assert ok(httpx.get(f"{BASE}/restaurants/r_anker"), 200)["combinable"] == []
    err(bk(ada2, tables=["t_1", "t_2"], party=2, at="21:00"), 422, "combination_not_allowed")
    # the upgraded state exports and re-imports
    snap2 = ok(httpx.get(f"{BASE}/_test/export"), 200)
    assert snap2["state"].get("schema") != snap["state"].get("schema"), "stage-2 exports carry a new schema marker"
    ok(imp(snap2), 204)
    assert ok(ada2.get(f"/reservations/{b1['reference']}"), 200)["table_ids"] == ["t_2"]
