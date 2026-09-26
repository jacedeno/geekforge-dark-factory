"""Verifier probes for stage-4 (restaurant revision, replans, closures, series amend, upgrade),
from plans/stage-4/brief.md. The seating planner is cross-checked against an exhaustive reference
implementation of G3/G4 on reproducible randomised scenarios.

PROBE_BASE_URL = stage-4 service; PROBE_BASE_URL_PREV1 / PROBE_BASE_URL_PREV3 = stage-1 / stage-3.
"""
from __future__ import annotations

import datetime as dt
import itertools
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "stage-1", "probes"))
from test_probes import (ADA, BOB, BASE, C, all_week, day, err, fixture, imp, key, login,  # noqa: E402
                         ok, reset, restaurant)

PREV1 = os.environ.get("PROBE_BASE_URL_PREV1")
PREV3 = os.environ.get("PROBE_BASE_URL_PREV3")
TZ = ZoneInfo("Europe/Berlin")


def D(lead=7):
    return day(lead)


def inst(date, hhmm, tz=TZ):
    h, m = map(int, hhmm.split(":"))
    return dt.datetime.combine(dt.date.fromisoformat(date), dt.time(h, m), tzinfo=tz).isoformat()


def mrest(rid="r_anker", combinable=(("t_1", "t_2"), ("t_2", "t_3")), tables=None, **kw):
    r = restaurant(rid, tables=tables, **kw)
    r["manager_user_ids"] = ["u_ada"]
    r["combinable"] = [list(p) for p in combinable]
    return r


def world(**kw):
    reset(fixture(restaurants=[mrest(**kw)]))
    return login(ADA), login(BOB)


def bk(c, date=None, at="19:00", table="t_2", party=2, rid="r_anker", k=None, tables=None):
    body = {"restaurant_id": rid, "starts_at_local": f"{date or D()}T{at}", "party_size": party}
    if tables is not None:
        body["table_ids"] = tables
    else:
        body["table_id"] = table
    return c.post("/reservations", json_=body, key_=k or key())


def replan(c, table="t_2", frm=None, to=None, rid="r_anker", k=None, date=None, **extra):
    body = {"table_id": table, "from": frm or inst(date or D(), "18:00"),
            "to": to or inst(date or D(), "23:00"), **extra}
    return c.post(f"/restaurants/{rid}/replans", json_=body, key_=k or key())


def apply(c, plan_id, rid="r_anker", k=None):
    return c.post(f"/restaurants/{rid}/replans/{plan_id}/apply", json_={}, key_=k or key())


def rev(c, rid="r_anker"):
    """Current restaurant revision, read through a preview on an empty window (previews never
    change it). Uses a window far in the future with no bookings."""
    p = ok(replan(c, "t_1", inst("2031-01-06", "10:00"), inst("2031-01-06", "11:00"), rid=rid), 201)
    return p["restaurant_revision"]


def hist(c, ref):
    return ok(c.get(f"/reservations/{ref}/history"), 200)["entries"]


def get(c, ref):
    return ok(c.get(f"/reservations/{ref}"), 200)


def series(c, ref, count=2, interval=1, k=None):
    return c.post("/series", json_={"anchor_reference": ref, "count": count, "interval_weeks": interval},
                  key_=k or key())


def amend(c, sid, expected, from_index=0, local_time="20:00", k=None, **extra):
    return c.post(f"/series/{sid}/amend", json_={"expected_revision": expected, "from_index": from_index,
                                                  "local_time": local_time, **extra}, key_=k or key())


def pol(date, **over):
    return {"effective_from": date, "slot_minutes": 30, "reservation_duration_minutes": 90,
            "cancellation_cutoff_minutes": 120, "opening_hours": all_week(),
            "capacities": {"t_1": 2, "t_2": 4, "t_3": 6}, **over}


# ---------------------------------------------------------------- restaurant revision

def test_restaurant_revision_counting():
    ada, bob = world()
    assert rev(ada) == 0
    k = key()
    a = ok(bk(ada, table="t_1", k=k), 201)
    assert rev(ada) == 1
    ok(bk(ada, table="t_1", k=k), 200)                                             # replay
    err(bk(ada, table="t_1"), 409, "table_unavailable")                             # failure
    ok(ada.patch(f"/reservations/{a['reference']}", json_={"party_size": 2}), 200)  # no-op
    assert rev(ada) == 1
    ok(ada.patch(f"/reservations/{a['reference']}", json_={"party_size": 1}), 200)
    assert rev(ada) == 2
    ok(ada.post(f"/reservations/{a['reference']}/cancel"), 200)
    ok(ada.post(f"/reservations/{a['reference']}/cancel"), 200)
    assert rev(ada) == 3
    ok(ada.post("/restaurants/r_anker/policies", json_=pol(D(60)), key_=key()), 201)
    assert rev(ada) == 4
    b = ok(bk(ada, table="t_3"), 201)
    s = ok(series(ada, b["reference"], count=3), 201)
    assert rev(ada) == 6
    c = ok(bk(ada, at="21:00", table="t_1"), 201)
    ok(ada.post("/reservation-moves", json_={"moves": [{"reference": c["reference"], "table_id": "t_2"},
                                                        {"reference": b["reference"]}]}, key_=key()), 201)
    assert rev(ada) == 8
    ok(ada.post("/reservation-moves", json_={"moves": [{"reference": c["reference"]}]}, key_=key()), 201)
    assert rev(ada) == 8  # all-no-op batch
    ok(amend(ada, s["series_id"], 1, 0, "19:00"), 201)   # all no-op
    assert rev(ada) == 8
    ok(amend(ada, s["series_id"], 1, 1, "20:30"), 201)
    assert rev(ada) == 9
    # another restaurant's counter is independent
    reset(fixture(restaurants=[mrest(), mrest("r_two", name="Two")]))
    ada = login(ADA)
    ok(bk(ada), 201)
    assert rev(ada, "r_two") == 0 and rev(ada) == 1


# ---------------------------------------------------------------- replans: precedence / limits

def test_replan_precedence():
    reset(fixture(restaurants=[mrest(), mrest("r_two", name="Two"), restaurant("r_free")]))
    ada, bob = login(ADA), login(BOB)
    body = {"table_id": "t_2", "from": inst(D(), "18:00"), "to": inst(D(), "23:00")}
    err(httpx.post(f"{BASE}/restaurants/r_anker/replans", json=body, headers={"Idempotency-Key": key()}),
        401, "unauthenticated")
    err(ada.post("/restaurants/r_nope/replans", json_=body), 404, "not_found")
    err(bob.post("/restaurants/r_anker/replans", json_=body), 403, "forbidden")
    err(ada.post("/restaurants/r_free/replans", json_=body, key_=key()), 403, "forbidden")
    err(ada.post("/restaurants/r_anker/replans", raw=b"{x"), 400, "missing_idempotency_key")
    err(ada.post("/restaurants/r_anker/replans", raw=b"{x", key_="k" * 256), 422, "validation_failed")
    err(ada.post("/restaurants/r_anker/replans", raw=b"[]", key_=key()), 400, "malformed_request")
    bad = [{"table_id": 2}, {"table_id": None}, {"from": "2026-10-04T18:00:00"}, {"to": "2026-10-04 23:00"},
           {"from": 5}, {"from": inst(D(), "23:00"), "to": inst(D(), "18:00")},
           {"from": inst(D(), "18:00"), "to": inst(D(), "18:00")}, {"from": "not a time"}, {"to": None}]
    for b in bad:
        err(ada.post("/restaurants/r_anker/replans", json_={**body, **b}, key_=key()), 422, "validation_failed")
    for drop in ("table_id", "from", "to"):
        err(ada.post("/restaurants/r_anker/replans", json_={k: v for k, v in body.items() if k != drop},
                     key_=key()), 422, "validation_failed")
    err(ada.post("/restaurants/r_anker/replans", json_={**body, "table_id": "t_9"}, key_=key()), 404, "not_found")
    # Z offset accepted
    z = {"table_id": "t_2", "from": "2031-01-06T10:00:00Z", "to": "2031-01-06T11:00:00Z"}
    ok(ada.post("/restaurants/r_anker/replans", json_=z, key_=key()), 201)
    k = key()
    p = ok(ada.post("/restaurants/r_anker/replans", json_=body, key_=k), 201)
    assert ok(ada.post("/restaurants/r_anker/replans", json_=body, key_=k), 200) == p
    err(ada.post("/restaurants/r_anker/replans", json_={**body, "table_id": "t_1"}, key_=k), 409,
        "idempotency_key_reuse")
    ok(ada.post("/restaurants/r_two/replans", json_=body, key_=k), 201)  # other path


def test_planning_limit():
    tables7 = [{"id": f"t_{i}", "label": str(i), "capacity": 4} for i in range(1, 8)]
    reset(fixture(restaurants=[mrest(tables=tables7, combinable=())]))
    err(replan(login(ADA), "t_1"), 422, "planning_limit")
    tables6 = tables7[:6]
    pairs5 = [("t_1", "t_2"), ("t_2", "t_3"), ("t_3", "t_4"), ("t_4", "t_5"), ("t_5", "t_6")]
    reset(fixture(restaurants=[mrest(tables=tables6, combinable=pairs5)]))
    err(replan(login(ADA), "t_1"), 422, "planning_limit")
    reset(fixture(restaurants=[mrest(tables=tables6, combinable=pairs5[:4])]))
    ada = login(ADA)
    for i, at in enumerate(["18:00", "18:00", "18:00", "19:30", "19:30", "21:00", "21:00"]):
        ok(bk(ada, at=at, table=f"t_{i % 6 + 1}"), 201)
    err(replan(ada, "t_1"), 422, "planning_limit")     # 7 considered bookings
    # a 404 table beats planning_limit
    err(replan(ada, "t_9"), 404, "not_found")
    # six considered bookings are within the limit
    ok(ada.post(f"/reservations/{ok(ada.get('/reservations'), 200)['reservations'][0]['reference']}/cancel"), 200)
    ok(replan(ada, "t_1"), 201)


# ---------------------------------------------------------------- planner vs exhaustive reference

def reference_plan(tables, pairs, bookings, closed, cfrom, cto):
    """Exhaustive G3/G4 optimum. bookings: dicts with ref, tables, party, start, end, status."""
    cap = dict(tables)
    opts = [[t] for t, _ in tables] + [list(p) for p in pairs]
    conf = [b for b in bookings if b["status"] == "confirmed"]
    cons = sorted([b for b in conf if b["start"] < cto and cfrom < b["end"]], key=lambda b: b["ref"])
    fixed = [b for b in conf if b not in cons]
    cands = []
    for b in cons:
        cs = []
        for rank, o in enumerate(opts):
            if sum(cap[t] for t in o) < b["party"] or closed in o:
                continue
            if any(set(o) & set(f["tables"]) and f["start"] < b["end"] and b["start"] < f["end"] for f in fixed):
                continue
            cs.append((rank, o))
        cands.append(cs)
    best = None
    for combo in itertools.product(*cands):
        okk = True
        for i, j in itertools.combinations(range(len(cons)), 2):
            if (set(combo[i][1]) & set(combo[j][1]) and cons[i]["start"] < cons[j]["end"]
                    and cons[j]["start"] < cons[i]["end"]):
                okk = False
                break
        if not okk:
            continue
        moved = sum(set(o) != set(b["tables"]) for (_, o), b in zip(combo, cons))
        unused = sum(sum(cap[t] for t in o) - b["party"] for (_, o), b in zip(combo, cons))
        score = (moved, unused, [r for r, _ in combo])
        if best is None or score < best[0]:
            best = (score, combo)
    if best is None:
        return None
    (moved, unused, _), combo = best
    return {"assignments": [{"reference": b["ref"], "table_ids": o, "changed": set(o) != set(b["tables"])}
                            for (_, o), b in zip(combo, cons)],
            "moved_count": moved, "unused_seats": unused}


TIMES = ["17:00", "17:30", "18:00", "18:30", "19:00", "19:30", "20:00", "20:30", "21:00", "21:30"]


def scenario(seed):
    rng = random.Random(seed)
    n = rng.randint(3, 6)
    tables = [(f"t_{i}", rng.randint(1, 6)) for i in range(1, n + 1)]
    allp = [(a, b) for (a, _), (b, _) in itertools.combinations(tables, 2)]
    pairs = rng.sample(allp, min(len(allp), rng.randint(0, 4)))
    opts = [[t] for t, _ in tables] + [list(p) for p in pairs]
    cap = dict(tables)
    d = D(10)
    placed = []
    refs = set()
    for _ in range(rng.randint(1, 8)):
        at = rng.choice(TIMES)
        start = dt.datetime.fromisoformat(inst(d, at))
        end = start + dt.timedelta(minutes=90)
        o = rng.choice(opts)
        if any(set(o) & set(p["tables"]) and p["start"] < end and start < p["end"] and p["status"] == "confirmed"
               for p in placed):
            continue
        party = rng.randint(1, sum(cap[t] for t in o))
        ref = "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789") for _ in range(rng.choice([6, 8, 10])))
        if ref in refs:
            continue
        refs.add(ref)
        placed.append({"ref": ref, "tables": o, "party": party, "start": start, "end": end, "at": at,
                       "status": "cancelled" if rng.random() < 0.15 else "confirmed"})
    closed = rng.choice(tables)[0]
    f = rng.randint(0, 6)
    t = rng.randint(f + 1, len(TIMES))
    cfrom = dt.datetime.fromisoformat(inst(d, TIMES[f]))
    cto = dt.datetime.fromisoformat(inst(d, (TIMES + ["22:00"])[t]))
    return tables, pairs, placed, closed, cfrom, cto, d


def seed_fixture(tables, pairs, placed, d):
    r = mrest(tables=[{"id": t, "label": t.upper(), "capacity": c} for t, c in tables], combinable=pairs,
              hours=all_week("17:00", "23:30"))
    res = []
    for i, p in enumerate(placed):
        e = {"id": f"res_s{i}", "reference": p["ref"], "user_id": "u_ada", "restaurant_id": "r_anker",
             "starts_at_local": f"{d}T{p['at']}", "party_size": p["party"], "status": p["status"]}
        if len(p["tables"]) == 1:
            e["table_id"] = p["tables"][0]
        else:
            e["table_ids"] = p["tables"]
        res.append(e)
    return fixture(restaurants=[r], reservations=res)


@pytest.mark.parametrize("seed", range(60))
def test_planner_matches_reference(seed):
    tables, pairs, placed, closed, cfrom, cto, d = scenario(seed)
    reset(seed_fixture(tables, pairs, placed, d))
    ada = login(ADA)
    want = reference_plan(tables, pairs, placed, closed, cfrom, cto)
    r = replan(ada, closed, cfrom.isoformat(), cto.isoformat())
    considered = [b for b in placed if b["status"] == "confirmed" and b["start"] < cto and cfrom < b["end"]]
    if len(considered) > 6:
        err(r, 422, "planning_limit")
        return
    if want is None:
        err(r, 409, "no_feasible_plan")
        return
    got = ok(r, 201)
    assert got["closure"] == {"table_id": closed, "from": cfrom.isoformat(), "to": cto.isoformat()}
    assert got["restaurant_revision"] == 0
    assert {k: got[k] for k in ("assignments", "moved_count", "unused_seats")} == want, (seed, got, want)
    # apply and read back the resulting tables
    if seed % 4 == 0:
        res = ok(apply(ada, got["plan_id"]), 201)["reservations"]
        assert [x["reference"] for x in res] == [a["reference"] for a in want["assignments"]]
        for a, x in zip(want["assignments"], res):
            assert x["table_ids"] == a["table_ids"]


def test_planner_own_terms_and_cutoff():
    """Capacity is judged under each booking's own accepted terms; cutoffs do not block repairs."""
    ada, _ = world()
    b = ok(bk(ada, table="t_2", party=4), 201)            # terms v0: t_1=2, t_2=4, t_3=6
    ok(ada.post("/restaurants/r_anker/policies", json_=pol(D(-1), capacities={"t_1": 9, "t_2": 4, "t_3": 1}),
                key_=key()), 201)
    p = ok(replan(ada, "t_2"), 201)
    # under b's own terms t_1 (2) cannot seat 4; t_3 (6) can; pairs contain t_2
    assert p["assignments"] == [{"reference": b["reference"], "table_ids": ["t_3"], "changed": True}], p
    # a booking already inside its cutoff can still be moved by the operator
    reset(fixture(restaurants=[mrest(cutoff=10080)]))
    ada = login(ADA)
    c = ok(bk(ada, D(3), table="t_2", party=2), 201)
    err(ada.patch(f"/reservations/{c['reference']}", json_={"table_id": "t_1"}), 409, "cutoff_passed")
    p = ok(replan(ada, "t_2", date=D(3)), 201)
    assert p["assignments"][0]["table_ids"] == ["t_1"]
    ok(apply(ada, p["plan_id"]), 201)
    assert get(ada, c["reference"])["table_ids"] == ["t_1"]


def test_planner_at_limits_is_fast():
    tables = [{"id": f"t_{i}", "label": str(i), "capacity": c} for i, c in zip(range(1, 7), [2, 2, 4, 4, 6, 6])]
    pairs = [("t_1", "t_2"), ("t_3", "t_4"), ("t_5", "t_6"), ("t_2", "t_3")]
    reset(fixture(restaurants=[mrest(tables=tables, combinable=pairs, hours=all_week("17:00", "23:30"))]))
    ada = login(ADA)
    for t, at in [("t_1", "19:00"), ("t_2", "19:00"), ("t_3", "19:00"), ("t_4", "19:00"), ("t_5", "19:00"),
                  ("t_6", "21:00")]:
        ok(bk(ada, at=at, table=t, party=1), 201)
    t0 = time.monotonic()
    p = ok(replan(ada, "t_6"), 201)
    assert time.monotonic() - t0 < 5.0
    assert len(p["assignments"]) == 6
    # six simultaneous two-seat bookings and t_6 closed: only five tables remain, infeasible
    reset(fixture(restaurants=[mrest(tables=tables, combinable=pairs, hours=all_week("17:00", "23:30"))]))
    ada = login(ADA)
    for t in ("t_1", "t_2", "t_3", "t_4", "t_5", "t_6"):
        ok(bk(ada, at="19:00", table=t, party=2), 201)
    t0 = time.monotonic()
    err(replan(ada, "t_6"), 409, "no_feasible_plan")
    assert time.monotonic() - t0 < 5.0


# ---------------------------------------------------------------- preview / apply

def test_preview_shape_and_no_side_effects():
    ada, bob = world()
    a = ok(bk(ada, table="t_2", party=4), 201)
    b = ok(bk(bob, at="20:00", table="t_1", party=2), 201)
    before = (get(ada, a["reference"]), hist(ada, a["reference"]), get(bob, b["reference"]))
    p = ok(replan(ada, "t_2", inst(D(), "19:30"), inst(D(), "20:30")), 201)
    assert set(p) == {"plan_id", "restaurant_revision", "closure", "assignments", "moved_count", "unused_seats"}
    assert p["restaurant_revision"] == 2
    assert p["closure"] == {"table_id": "t_2", "from": inst(D(), "19:30"), "to": inst(D(), "20:30")}
    refs = sorted([a["reference"], b["reference"]])
    assert [x["reference"] for x in p["assignments"]] == refs
    assert (get(ada, a["reference"]), hist(ada, a["reference"]), get(bob, b["reference"])) == before
    assert rev(ada) == 2
    # a previewed closure is not in force
    ok(replan(ada, "t_3", inst(D(), "20:00"), inst(D(), "21:00")), 201)
    ok(bk(bob, at="20:00", table="t_3"), 201)
    # empty window -> empty feasible plan
    e = ok(replan(ada, "t_2", inst(D(), "12:00"), inst(D(), "13:00")), 201)
    assert e["assignments"] == [] and e["moved_count"] == 0 and e["unused_seats"] == 0


def test_no_feasible_plan():
    ada, bob = world(combinable=())
    ok(bk(ada, table="t_1", party=2), 201)
    ok(bk(ada, table="t_2", party=4), 201)
    six = ok(bk(bob, table="t_3", party=6), 201)
    k = key()
    err(replan(ada, "t_3", k=k), 409, "no_feasible_plan")
    assert rev(ada) == 3
    # the key stays unused after a 4xx
    ok(bob.post(f"/reservations/{six['reference']}/cancel"), 200)
    p = ok(replan(ada, "t_2", k=k), 201)
    assert [a["table_ids"] for a in p["assignments"] if a["changed"]] == [["t_3"]]


def test_apply_precedence():
    reset(fixture(restaurants=[mrest(), mrest("r_two", name="Two")]))
    ada, bob = login(ADA), login(BOB)
    ok(bk(ada, table="t_2", party=2), 201)
    p = ok(replan(ada, "t_2"), 201)
    pid = p["plan_id"]
    err(httpx.post(f"{BASE}/restaurants/r_anker/replans/{pid}/apply", json={}, headers={"Idempotency-Key": key()}),
        401, "unauthenticated")
    err(ada.post(f"/restaurants/r_nope/replans/{pid}/apply", json_={}, key_=key()), 404, "not_found")
    err(bob.post(f"/restaurants/r_anker/replans/{pid}/apply", json_={}), 403, "forbidden")
    err(ada.post(f"/restaurants/r_anker/replans/{pid}/apply", json_={}), 400, "missing_idempotency_key")
    err(ada.post(f"/restaurants/r_anker/replans/{pid}/apply", json_={}, key_="k" * 256), 422, "validation_failed")
    err(ada.post(f"/restaurants/r_anker/replans/{pid}/apply", raw=b"[]", key_=key()), 400, "malformed_request")
    err(ada.post(f"/restaurants/r_anker/replans/{pid}/apply", raw=b"{x", key_=key()), 400, "malformed_request")
    err(apply(ada, "nope"), 404, "not_found")
    err(apply(ada, pid, rid="r_two"), 404, "not_found")
    k = key()
    first = ok(apply(ada, pid, k=k), 201)
    assert set(first) == {"plan_id", "restaurant_revision", "reservations"} and first["plan_id"] == pid
    assert first["restaurant_revision"] == 2
    err(apply(ada, pid), 409, "plan_already_applied")
    ok(bk(ada, at="21:00", table="t_1"), 201)
    assert ok(apply(ada, pid, k=k), 200) == first
    err(apply(ada, pid), 409, "plan_already_applied")   # before stale_plan
    # stale: any intervening restaurant revision
    p2 = ok(replan(ada, "t_1", inst(D(), "20:30"), inst(D(), "22:00")), 201)
    ok(bk(ada, D(8), table="t_3"), 201)
    err(apply(ada, p2["plan_id"]), 409, "stale_plan")
    p3 = ok(replan(ada, "t_1", inst(D(), "20:30"), inst(D(), "22:00")), 201)
    ok(ada.post("/restaurants/r_anker/policies", json_=pol(D(90)), key_=key()), 201)
    err(apply(ada, p3["plan_id"]), 409, "stale_plan")


def test_apply_effects():
    ada, bob = world()
    a = ok(bk(ada, table="t_2", party=2), 201)                   # will move
    b = ok(bk(bob, at="20:00", table="t_3", party=3), 201)       # considered, stays
    c = ok(bk(ada, at="21:00", table="t_1", party=2), 201)       # outside window: fixed
    s = ok(series(ada, a["reference"], count=2), 201)
    ok(ada.patch(f"/reservations/{s['occurrences'][1]['reference']}", json_={"party_size": 1}), 200)  # exception
    srev = ok(ada.get(f"/series/{s['series_id']}"), 200)["revision"]
    p = ok(replan(ada, "t_2", inst(D(), "18:00"), inst(D(), "20:30")), 201)
    assert [x["reference"] for x in p["assignments"]] == sorted([a["reference"], b["reference"]])
    moved = {x["reference"]: x for x in p["assignments"]}
    assert moved[a["reference"]]["changed"] and not moved[b["reference"]]["changed"]
    ha, hb, hc = hist(ada, a["reference"]), hist(bob, b["reference"]), hist(ada, c["reference"])
    ga, gb, gc = get(ada, a["reference"]), get(bob, b["reference"]), get(ada, c["reference"])
    r0 = rev(ada)
    out = ok(apply(ada, p["plan_id"]), 201)
    assert out["restaurant_revision"] == r0 + 1 and rev(ada) == r0 + 1
    assert [x["reference"] for x in out["reservations"]] == sorted([a["reference"], b["reference"]])
    na = get(ada, a["reference"])
    assert na["table_ids"] == moved[a["reference"]]["table_ids"] and na["revision"] == ga["revision"] + 1
    for f in ("starts_at", "ends_at", "starts_at_local", "party_size", "accepted_terms", "created_at", "status",
              "reservation_id"):
        assert na[f] == ga[f], f
    e = hist(ada, a["reference"])
    assert len(e) == len(ha) + 1
    last = e[-1]
    assert last["event"] == "reassigned" and last["plan_id"] == p["plan_id"]
    assert last["changes"] == [{"field": "table_ids", "from": ["t_2"], "to": na["table_ids"]}], last
    assert last["revision"] == na["revision"] and last["accepted_terms"] == ga["accepted_terms"]
    assert last["seq"] == e[-2]["seq"] + 1
    assert get(bob, b["reference"]) == gb and hist(bob, b["reference"]) == hb
    assert get(ada, c["reference"]) == gc and hist(ada, c["reference"]) == hc
    cur = ok(ada.get(f"/series/{s['series_id']}"), 200)
    assert cur["revision"] == srev + 1
    assert [o["exception"] for o in cur["occurrences"]] == [False, True]
    assert {x["reference"]: x for x in out["reservations"]}[a["reference"]] == na


def test_closure_everywhere():
    ada, bob = world()
    a = ok(bk(ada, table="t_2", party=2), 201)
    p = ok(replan(ada, "t_2", inst(D(), "19:00"), inst(D(), "21:00")), 201)
    ok(apply(ada, p["plan_id"]), 201)
    q = {"restaurant_id": "r_anker", "date": D(), "party_size": 2, "explain": "true"}
    slots = {s["starts_at_local"][-5:]: s for s in ok(httpx.get(f"{BASE}/availability", params=q), 200)["slots"]}
    for hhmm in ("18:00", "19:00", "20:30"):
        s = slots[hhmm]
        assert "t_2" not in s["available_table_ids"], hhmm
        assert all("t_2" not in o["table_ids"] for o in s["available_options"]), hhmm
        e = {x["table_id"]: x for x in s["explain"]}["t_2"]
        assert e["available"] is False and e["rules"][1] == {"rule": "no_overlap", "holds": False}
    assert "t_2" in slots["21:00"]["available_table_ids"]  # `to` is exclusive
    err(bk(bob, at="20:30", table="t_2"), 409, "table_unavailable")
    err(bk(bob, at="18:00", tables=["t_2", "t_3"], party=3), 409, "table_unavailable")
    ok(bk(bob, at="21:00", table="t_2"), 201)                       # starts exactly at `to`
    x = ok(bk(bob, D(8), table="t_3"), 201)
    err(bob.patch(f"/reservations/{x['reference']}", json_={"starts_at_local": f"{D()}T19:00", "table_id": "t_2"}),
        409, "table_unavailable")
    err(bob.post("/reservation-moves", json_={"moves": [{"reference": x["reference"], "table_id": "t_2",
                                                         "starts_at_local": f"{D()}T18:30"}]}, key_=key()),
        409, "table_unavailable")
    # series adoption whose occurrence lands in a closure
    p2 = ok(replan(ada, "t_3", inst(D(14), "18:00"), inst(D(14), "23:00")), 201)
    ok(apply(ada, p2["plan_id"]), 201)
    anc = ok(bk(bob, D(7), at="19:00", table="t_3"), 201)
    err(series(bob, anc["reference"], count=2), 409, "table_unavailable")
    # series amend into a closure
    anc2 = ok(bk(bob, D(7), at="21:00", table="t_1"), 201)
    s = ok(series(bob, anc2["reference"], count=2), 201)
    p3 = ok(replan(ada, "t_1", inst(D(14), "20:00"), inst(D(14), "21:00")), 201)
    ok(apply(ada, p3["plan_id"]), 201)
    err(amend(bob, s["series_id"], 1, 0, "20:00"), 409, "table_unavailable")
    # later plans respect earlier closures: on D, t_2 is closed 19:00-21:00 and t_1 holds `a`;
    # closing t_3 leaves the anchor booked on t_3 at 19:00 (D(7) == D()) nowhere to go, since
    # every alternative uses t_2 or t_1 -> no feasible plan
    err(replan(ada, "t_3", inst(D(), "19:00"), inst(D(), "20:00")), 409, "no_feasible_plan")
    _ = a


def test_other_restaurant_does_not_stale():
    reset(fixture(restaurants=[mrest(), mrest("r_two", name="Two")]))
    ada = login(ADA)
    ok(bk(ada, table="t_2"), 201)
    p = ok(replan(ada, "t_2"), 201)
    ok(bk(ada, table="t_2", rid="r_two"), 201)
    q = ok(replan(ada, "t_2", rid="r_two"), 201)
    ok(apply(ada, q["plan_id"], rid="r_two"), 201)
    ok(apply(ada, p["plan_id"]), 201)


def test_concurrent_apply():
    ada, _ = world()
    for t in ("t_1", "t_2"):
        ok(bk(ada, table=t, party=1), 201)
    p1 = ok(replan(ada, "t_2"), 201)
    p2 = ok(replan(ada, "t_1"), 201)
    with ThreadPoolExecutor(2) as ex:
        out = list(ex.map(lambda p: apply(ada, p["plan_id"]), [p1, p2]))
    codes = sorted(r.status_code for r in out)
    assert codes == [201, 409], codes
    assert [r.json()["error"]["code"] for r in out if r.status_code == 409] == ["stale_plan"]
    tabs = sorted(tuple(r["table_ids"]) for r in ok(ada.get("/reservations"), 200)["reservations"])
    assert len(set(tabs)) == 2
    ada, _ = world()
    ok(bk(ada, table="t_2", party=1), 201)
    p = ok(replan(ada, "t_2"), 201)
    with ThreadPoolExecutor(8) as ex:
        out = list(ex.map(lambda _: apply(ada, p["plan_id"]), range(8)))
    codes = sorted(r.status_code for r in out)
    assert codes.count(201) == 1 and codes.count(409) == 7, codes
    assert {r.json()["error"]["code"] for r in out if r.status_code == 409} == {"plan_already_applied"}
    [r] = ok(ada.get("/reservations"), 200)["reservations"]
    assert r["revision"] == 2


def test_lookup_screen_after_apply():
    tables = [{"id": "t_1", "label": "Window", "capacity": 2}, {"id": "t_2", "label": "Garden", "capacity": 4},
              {"id": "t_3", "label": "Terrace", "capacity": 6}]
    reset(fixture(restaurants=[mrest(tables=tables)]))
    ada = login(ADA)
    a = ok(bk(ada, table="t_2", party=4), 201)
    p = ok(replan(ada, "t_2"), 201)
    ok(apply(ada, p["plan_id"]), 201)
    from playwright import sync_api
    with sync_api.sync_playwright() as drv:
        b = drv.chromium.launch(channel="chromium")
        ctx = b.new_context(base_url=BASE)
        page = ctx.new_page()
        page.goto("/login")
        page.fill("[data-testid='login-email']", ADA["email"])
        page.fill("[data-testid='login-password']", ADA["password"])
        page.click("[data-testid='login-submit']")
        page.wait_for_selector("[data-testid='current-user']")
        page.goto("/lookup")
        page.fill("[data-testid='lookup-reference-input']", a["reference"])
        page.click("[data-testid='lookup-submit']")
        page.wait_for_selector("[data-testid='reservation-detail']")
        text = page.text_content("[data-testid='reservation-tables']")
        b.close()
    assert "Terrace" in text and "Garden" not in text, text


# ---------------------------------------------------------------- series amend

def test_series_amend_precedence():
    ada, bob = world()
    a = ok(bk(ada), 201)
    s = ok(series(ada, a["reference"], count=3), 201)
    sid = s["series_id"]
    body = {"expected_revision": 1, "from_index": 0, "local_time": "20:00"}
    err(httpx.post(f"{BASE}/series/{sid}/amend", json=body, headers={"Idempotency-Key": key()}), 401,
        "unauthenticated")
    err(ada.post(f"/series/{sid}/amend", json_=body), 400, "missing_idempotency_key")
    err(ada.post(f"/series/{sid}/amend", json_=body, key_="k" * 256), 422, "validation_failed")
    err(ada.post(f"/series/{sid}/amend", raw=b"{x", key_=key()), 400, "malformed_request")
    err(ada.post("/series/nope/amend", json_=body, key_=key()), 404, "not_found")
    err(bob.post(f"/series/{sid}/amend", json_=body, key_=key()), 404, "not_found")
    err(bob.post(f"/series/{sid}/amend", json_={"expected_revision": True}, key_=key()), 404, "not_found")
    for bad in ({"expected_revision": 0}, {"expected_revision": True}, {"expected_revision": "1"},
                {"expected_revision": 1.5}, {"from_index": -1}, {"from_index": 3}, {"from_index": True},
                {"from_index": "0"}, {"local_time": "24:00"}, {"local_time": "7:00"}, {"local_time": "20:60"},
                {"local_time": "20:00:00"}, {"local_time": 2000}, {"local_time": ""}):
        err(ada.post(f"/series/{sid}/amend", json_={**body, **bad}, key_=key()), 422, "validation_failed")
    for drop in body:
        err(ada.post(f"/series/{sid}/amend", json_={k: v for k, v in body.items() if k != drop}, key_=key()), 422,
            "validation_failed")
    err(amend(ada, sid, 2, 0, "20:00"), 409, "stale_revision")
    # stale before validation of occurrences
    err(amend(ada, sid, 2, 0, "03:17"), 409, "stale_revision")
    k = key()
    r1 = ok(amend(ada, sid, 1, 0, "20:00", k=k), 201)
    assert r1["revision"] == 2
    err(amend(ada, sid, 1, 0, "21:00", k=k), 409, "idempotency_key_reuse")
    ok(ada.patch(f"/reservations/{s['occurrences'][2]['reference']}", json_={"party_size": 1}), 200)
    assert ok(amend(ada, sid, 1, 0, "20:00", k=k), 200) == r1


def test_series_amend_semantics():
    ada, bob = world()
    a = ok(bk(ada, D(7), at="19:00", table="t_2", party=2), 201)
    s = ok(series(ada, a["reference"], count=5, interval=2), 201)
    sid = s["series_id"]
    refs = [o["reference"] for o in s["occurrences"]]
    ok(ada.patch(f"/reservations/{refs[2]}", json_={"party_size": 1}), 200)         # exception (rev 2)
    ok(ada.post(f"/reservations/{refs[3]}/cancel"), 200)                          # cancelled (rev 3)
    ok(ada.post("/restaurants/r_anker/policies", json_=pol(D(35), reservation_duration_minutes=60), key_=key()), 201)
    before = {r: (get(ada, r), hist(ada, r)) for r in refs}
    rr = rev(ada)
    out = ok(amend(ada, sid, 3, 1, "20:30"), 201)
    assert out["revision"] == 4 and rev(ada) == rr + 1
    occ = out["occurrences"]
    assert [o["exception"] for o in occ] == [False, False, True, False, False]
    for i in (0, 2, 3):
        assert (get(ada, refs[i]), hist(ada, refs[i])) == before[refs[i]], i
    for i in (1, 4):
        r = get(ada, refs[i])
        sched = dt.date.fromisoformat(D(7)) + dt.timedelta(days=14 * i)
        assert r["starts_at_local"] == f"{sched.isoformat()}T20:30", r["starts_at_local"]
        assert r["revision"] == before[refs[i]][0]["revision"] + 1
        assert r["table_ids"] == ["t_2"] and r["party_size"] == 2
        h = hist(ada, refs[i])
        assert len(h) == 2 and h[-1]["event"] == "changed"
        assert h[-1]["changes"] == [{"field": "starts_at_local", "from": before[refs[i]][0]["starts_at_local"],
                                     "to": r["starts_at_local"]}]
    assert get(ada, refs[4])["accepted_terms"]["policy_version"] == 1         # D(63) >= D(35)
    assert get(ada, refs[4])["ends_at"][11:16] == "21:30"
    assert get(ada, refs[1])["accepted_terms"]["policy_version"] == 0         # D(21) < D(35)
    # all-no-op and empty eligible sets succeed without changes
    rr = rev(ada)
    same = ok(amend(ada, sid, 4, 1, "20:30"), 201)
    assert same["revision"] == 4 and rev(ada) == rr
    assert ok(amend(ada, sid, 4, 4, "20:30"), 201)["revision"] == 4
    ok(ada.patch(f"/reservations/{refs[4]}", json_={"party_size": 1}), 200)   # now an exception
    e = ok(amend(ada, sid, 5, 4, "18:00"), 201)                               # only index 4: exception -> empty
    assert e["revision"] == 5 and get(ada, refs[4])["starts_at_local"].endswith("T20:30")


def test_series_amend_errors():
    ada, bob = world()
    a = ok(bk(ada, D(7), at="19:00", table="t_2", party=2), 201)
    s = ok(series(ada, a["reference"], count=3), 201)
    sid = s["series_id"]
    refs = [o["reference"] for o in s["occurrences"]]
    ok(ada.post("/restaurants/r_anker/policies", json_=pol(D(21), reservation_duration_minutes=120), key_=key()), 201)
    ok(bk(bob, D(7), at="21:30", table="t_2", party=2), 201)        # conflicts with index 0 at 21:30
    snap = {r: (get(ada, r), hist(ada, r)) for r in refs}
    # index 2 (v1: 120 min) cannot end by 23:00 at 21:30 -> non-occupancy error wins over the index-0 conflict
    err(amend(ada, sid, 1, 0, "21:30"), 422, "outside_opening_hours")
    # without index 2's issue, the conflict at index 0 decides
    err(amend(ada, sid, 1, 0, "21:00"), 409, "table_unavailable")
    err(amend(ada, sid, 1, 0, "19:15"), 422, "not_on_slot_grid")
    err(amend(ada, sid, 1, 0, "03:00"), 422, "outside_opening_hours")
    for r in refs:
        assert (get(ada, r), hist(ada, r)) == snap[r]
    assert ok(ada.get(f"/series/{sid}"), 200)["revision"] == 1
    # the occupancy of unchanged occurrences counts: moving index 1 onto index 1's own slot is a no-op
    ok(amend(ada, sid, 1, 1, "19:00"), 201)
    # DST gap
    reset(fixture(restaurants=[mrest(hours=all_week("00:00", "23:30"))]))
    ada = login(ADA)
    b = ok(bk(ada, "2027-03-21", at="01:00", table="t_2", party=2), 201)
    s2 = ok(series(ada, b["reference"], count=2), 201)
    err(amend(ada, s2["series_id"], 1, 0, "02:30"), 422, "invalid_local_time")
    fb = ok(amend(ada, s2["series_id"], 1, 0, "03:00"), 201)
    assert fb["occurrences"][1]["reservation"]["starts_at"] == "2027-03-28T03:00:00+02:00"


def test_concurrent_series_amend():
    ada, _ = world()
    a = ok(bk(ada), 201)
    s = ok(series(ada, a["reference"], count=3), 201)

    def go(i):
        return amend(ada, s["series_id"], 1, 0, ["20:00", "20:30", "21:00"][i % 3])
    with ThreadPoolExecutor(12) as ex:
        out = list(ex.map(go, range(12)))
    codes = sorted(r.status_code for r in out)
    assert codes.count(201) == 1 and codes.count(409) == 11, codes
    assert ok(ada.get(f"/series/{s['series_id']}"), 200)["revision"] == 2


# ---------------------------------------------------------------- export / upgrade

def test_stage4_roundtrip():
    ada, bob = world()
    a = ok(bk(ada, table="t_2", party=2), 201)
    kp, ka = key(), key()
    p = ok(replan(ada, "t_2", k=kp), 201)
    applied = ok(apply(ada, p["plan_id"], k=ka), 201)
    b = ok(bk(ada, D(9), table="t_1"), 201)
    pending = ok(replan(ada, "t_1", date=D(9)), 201)
    r0 = rev(ada)
    views = {x: (get(ada, x), hist(ada, x)) for x in (a["reference"], b["reference"])}
    snap = ok(httpx.get(f"{BASE}/_test/export"), 200)
    assert snap["state"].get("schema") not in (1, 2, 3)
    reset(fixture(users=[], restaurants=[]))
    ok(imp(snap), 204)
    assert rev(ada) == r0
    for x, v in views.items():
        assert (get(ada, x), hist(ada, x)) == v
    err(bk(bob, at="20:00", table="t_2"), 409, "table_unavailable")                  # closure survived
    assert ok(apply(ada, p["plan_id"], k=ka), 200) == applied
    err(apply(ada, p["plan_id"]), 409, "plan_already_applied")
    assert ok(replan(ada, "t_2", k=kp), 200) == p
    out = ok(apply(ada, pending["plan_id"]), 201)                                  # unapplied plan still valid
    assert out["restaurant_revision"] == r0 + 1


def _stage3_state(prev):
    reset(fixture(restaurants=[mrest(hours=all_week("17:00", "23:30"))]), base=prev)
    ada = login(ADA, prev)
    a = ok(ada.post("/reservations", json_={"restaurant_id": "r_anker", "table_id": "t_2", "party_size": 2,
                                            "starts_at_local": f"{D()}T19:00"}, key_=key()), 201)
    s = ok(ada.post("/series", json_={"anchor_reference": a["reference"], "count": 4, "interval_weeks": 1},
                    key_=key()), 201)
    refs = [o["reference"] for o in s["occurrences"]]
    ok(ada.post("/reservation-moves", json_={"moves": [{"reference": refs[1], "table_id": "t_3"}]}, key_=key()), 201)
    ok(ada.post(f"/reservations/{refs[2]}/cancel"), 200)
    return ada, s, refs


def test_upgrade_from_stage3():
    if not PREV3:
        pytest.skip("PROBE_BASE_URL_PREV3 not set")
    ada3, s, refs = _stage3_state(PREV3)
    k = key()
    receipt = ok(ada3.post("/reservations", json_={"restaurant_id": "r_anker", "table_id": "t_1", "party_size": 2,
                                                   "starts_at_local": f"{D()}T21:00"}, key_=k), 201)
    views = {r: (ok(ada3.get(f"/reservations/{r}"), 200), ok(ada3.get(f"/reservations/{r}/history"), 200))
             for r in refs}
    cur = ok(ada3.get(f"/series/{s['series_id']}"), 200)
    snap = ok(httpx.get(f"{PREV3}/_test/export"), 200)
    reset(fixture(users=[], restaurants=[]))
    ok(imp(snap), 204)
    ada = C(BASE, ada3.token)
    for r in refs:
        assert (get(ada, r), ok(ada.get(f"/reservations/{r}/history"), 200)) == views[r], r
    assert ok(ada.get(f"/series/{s['series_id']}"), 200) == cur
    assert ok(ada.post("/reservations", json_={"restaurant_id": "r_anker", "table_id": "t_1", "party_size": 2,
                                               "starts_at_local": f"{D()}T21:00"}, key_=k), 200) == receipt
    # series amend on imported series: index 1 is an exception (moved), index 2 cancelled
    out = ok(amend(ada, s["series_id"], cur["revision"], 0, "20:00"), 201)
    assert out["revision"] == cur["revision"] + 1
    assert [o["reservation"]["starts_at_local"][-5:] for o in out["occurrences"]] == ["20:00", "19:00", "19:00", "20:00"]
    # replan on imported bookings moves an occurrence (not an exception) and keeps exception flags
    p = ok(replan(ada, "t_2", date=D(28)), 201)
    assert [x["reference"] for x in p["assignments"]] == [refs[3]]
    ok(apply(ada, p["plan_id"]), 201)
    after = ok(ada.get(f"/series/{s['series_id']}"), 200)
    assert after["revision"] == out["revision"] + 1
    assert [o["exception"] for o in after["occurrences"]] == [o["exception"] for o in out["occurrences"]]
    assert hist(ada, refs[3])[-1]["event"] == "reassigned"


def test_upgrade_from_stage1():
    if not PREV1:
        pytest.skip("PROBE_BASE_URL_PREV1 not set")
    reset(fixture(), base=PREV1)
    ada1 = login(ADA, PREV1)
    k = key()
    body = {"restaurant_id": "r_anker", "table_id": "t_2", "party_size": 2, "starts_at_local": f"{D()}T19:00"}
    b1 = ok(ada1.post("/reservations", json_=body, key_=k), 201)
    snap = ok(httpx.get(f"{PREV1}/_test/export"), 200)
    reset(fixture(users=[], restaurants=[]))
    ok(imp(snap), 204)
    ada = C(BASE, ada1.token)
    assert ok(ada.post("/reservations", json_=body, key_=k), 200) == b1
    g = get(ada, b1["reference"])
    assert g["revision"] == 1 and g["table_ids"] == ["t_2"]
    # the stage-1 restaurant had no managers: replans are 403, series amend works
    err(replan(ada, "t_2"), 403, "forbidden")
    s = ok(series(ada, b1["reference"], count=2), 201)
    out = ok(amend(ada, s["series_id"], 1, 0, "20:00"), 201)
    assert all(o["reservation"]["starts_at_local"].endswith("T20:00") for o in out["occurrences"])
