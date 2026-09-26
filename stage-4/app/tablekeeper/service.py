"""Endpoint behaviour. Every state change runs under one lock, so concurrent requests behave as
if executed one at a time (D1). Password hashing runs outside the lock."""

import itertools
import json
import re
import secrets
import string
import threading
from datetime import timedelta

from . import clock
from .errors import ApiError, invalid, malformed, not_found, unauthenticated
from .model import (Closure, IdempotencyRecord, Plan, Reservation, Series, State, User,
                    created_changes, from_export, from_fixture, is_int, parse_instant,
                    parse_policy, table_changes)
from .planner import Booking, Candidate, best_plan
from .passwords import hash_password, verify_password

_BEARER_RE = re.compile(r"Bearer ([^\s]+)")
_DIGITS_RE = re.compile(r"[0-9]+")
_REFERENCE_ALPHABET = string.ascii_uppercase + string.digits
_REFERENCE_LENGTH = 8
_MAX_MOVES = 8
_AMENDABLE = ("table_id", "table_ids", "starts_at_local", "party_size")
_PLAN_MAX_TABLES, _PLAN_MAX_PAIRS, _PLAN_MAX_BOOKINGS = 6, 4, 6


class Request:
    def __init__(self, method, path, params, query, headers, body):
        self.method = method
        self.path = path  # the request path without the query string
        self.params = params  # path parameters, in order
        self.query = query  # first value of each query parameter
        self.headers = headers
        self.body = body  # raw bytes


def _reject_constant(name):
    raise ValueError(f"invalid JSON constant {name}")


def parse_json(raw):
    try:
        return json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise malformed("request body is not valid JSON")


def parse_object(raw):
    body = parse_json(raw)
    if not isinstance(body, dict):
        raise malformed("request body must be a JSON object")
    return body


def canonical(value):
    """JSON-value identity: key order and whitespace do not matter."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def valid_email(email):
    if any(c.isspace() for c in email) or email.count("@") != 1:
        return False
    local, domain = email.split("@")
    return bool(local) and bool(domain)


def _combination_not_allowed(message):
    return ApiError(422, "combination_not_allowed", message)


def valid_party(v):
    return is_int(v) and v >= 1


class Service:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = State()
        self._ids = itertools.count(1)

    # ---- helpers (call with the lock held) ----------------------------------------------------

    def _new_id(self, prefix, taken):
        while True:
            candidate = f"{prefix}{next(self._ids)}"
            if candidate not in taken:
                return candidate

    def _new_reference(self):
        while True:
            ref = "".join(secrets.choice(_REFERENCE_ALPHABET) for _ in range(_REFERENCE_LENGTH))
            if ref not in self.state.references:
                return ref

    def _new_token(self, user_id):
        token = secrets.token_urlsafe(32)
        self.state.tokens[token] = user_id
        return token

    def _auth(self, req):
        m = _BEARER_RE.fullmatch(req.headers.get("Authorization") or "")
        user_id = self.state.tokens.get(m.group(1)) if m else None
        if user_id is None or user_id not in self.state.users:
            raise unauthenticated()
        return user_id

    @staticmethod
    def _idempotency_key(req):
        key = req.headers.get("Idempotency-Key")
        if not key:
            raise ApiError(400, "missing_idempotency_key", "Idempotency-Key header is required")
        if len(key) > 255:
            raise invalid("Idempotency-Key must be 1 to 255 characters")
        return key

    def _replay(self, user_id, key, req, body):
        """Return a stored (200, response) for a replay, raise 409 on reuse, None on first use."""
        rec = self.state.idempotency.get((user_id, key, req.method, req.path))
        if rec is None:
            return None
        if rec.request != canonical(body):
            raise ApiError(409, "idempotency_key_reuse",
                           "Idempotency-Key was already used with a different request body")
        return 200, rec.response

    def _record(self, user_id, key, req, body, status, response):
        self.state.add_idempotency(IdempotencyRecord(user_id, key, req.method, req.path,
                                                     canonical(body), status, response))

    def _optional_user(self, req):
        """The caller's user id, or None; for endpoints that answer 404 to everyone else."""
        m = _BEARER_RE.fullmatch(req.headers.get("Authorization") or "")
        user_id = self.state.tokens.get(m.group(1)) if m else None
        return user_id if user_id in self.state.users else None

    def _own(self, user_id, reference):
        rid = self.state.references.get(reference)
        res = self.state.reservations.get(rid) if rid else None
        if res is None or user_id is None or res.user_id != user_id:
            raise not_found("reservation not found")
        return res

    def _view(self, res):
        return res.view(self.state.restaurants[res.restaurant_id])

    @staticmethod
    def _now():
        return clock.now_utc().replace(microsecond=0)

    @staticmethod
    def _cutoff_passed(res):
        """The accepted cutoff, measured against the current start."""
        return clock.now_utc() >= res.starts_utc - timedelta(minutes=res.terms.cutoff)

    def _overlaps(self, restaurant, table_ids, start, end, exclude):
        """True when an applied closure or a confirmed reservation not in `exclude` holds any of
        these tables during [start, end)."""
        if restaurant.closed(table_ids, start, end):
            return True
        members = set(table_ids)
        for other in self.state.reservations.values():
            if (other.status == "confirmed" and other.id not in exclude
                    and other.restaurant_id == restaurant.id and members.intersection(other.table_ids)
                    and other.starts_utc < end and start < other.ends_utc):
                return True
        return False

    @staticmethod
    def _requested_tables(item, wrong_type):
        """E10 checks that need no restaurant. Returns the named table ids, or None if absent."""
        if "table_id" in item and "table_ids" in item:
            raise invalid("send either table_id or table_ids, not both")
        if "table_id" in item:
            return [item["table_id"]]
        if "table_ids" not in item:
            return None
        ids = item["table_ids"]
        if not isinstance(ids, list) or not all(isinstance(t, str) for t in ids):
            raise wrong_type("table_ids must be an array of strings")
        if not ids or len(set(ids)) != len(ids):
            raise invalid("table_ids must name at least one table, without duplicates")
        if len(ids) > 2:
            raise _combination_not_allowed("at most two tables can be combined")
        return list(ids)

    @staticmethod
    def _resolve_tables(restaurant, ids):
        """The table set in stored order: 404 for unknown tables, 422 for undeclared pairs."""
        for t in ids:
            if t not in restaurant.table_ids:
                raise not_found("table not found")
        if len(ids) == 1:
            return list(ids)
        pair = restaurant.pair(ids)
        if pair is None:
            raise _combination_not_allowed("these tables cannot be combined")
        return pair

    @staticmethod
    def _check_booking(restaurant, table_ids, local, party):
        """D5 d..g under the policy of the local start date (F2). Returns (start, terms)."""
        terms = restaurant.terms_for(local.date())
        start, error = clock.check_start(restaurant.zone, terms, local)
        if error == "invalid_local_time":
            raise ApiError(422, error, "that local time does not exist in the restaurant's zone")
        if error == "outside_opening_hours":
            raise ApiError(422, error, "the reservation is outside opening hours")
        if error == "not_on_slot_grid":
            raise ApiError(422, error, "the start time is not on the slot grid")
        if party > terms.seats(table_ids):
            raise ApiError(422, "party_exceeds_capacity", "party size exceeds the table capacity")
        return start, terms

    @staticmethod
    def _unavailable():
        return ApiError(409, "table_unavailable", "the table is taken for an overlapping interval")

    # ---- runtime and test control -------------------------------------------------------------

    def health(self, req):
        return 200, {"status": "ok"}

    def reset(self, req):
        state = from_fixture(parse_json(req.body))
        with self.lock:
            self.state = state
        return 204, None

    def export(self, req):
        with self.lock:
            snapshot = json.dumps({"track": "tablekeeper", "format_version": 1,
                                   "state": self.state.export()}, ensure_ascii=False)
        return 200, json.loads(snapshot)

    def import_(self, req):
        state = from_export(parse_json(req.body))
        with self.lock:
            self.state = state
        return 204, None

    # ---- authentication -----------------------------------------------------------------------

    @staticmethod
    def _credentials(body, names):
        for name in names:
            if name in body and not isinstance(body[name], str):
                raise malformed(f"{name} must be a string")
        for name in names:
            if name not in body:
                raise invalid(f"{name} is required")
        return [body[n] for n in names]

    def signup(self, req):
        body = parse_object(req.body)
        email, password, display_name = self._credentials(body, ("email", "password",
                                                                 "display_name"))
        if not valid_email(email):
            raise invalid("email must be of the form local@domain")
        if len(password) < 8:
            raise invalid("password must be at least 8 characters")
        if not display_name.strip():
            raise invalid("display_name must not be empty")
        taken = ApiError(409, "email_taken", "email already registered")
        with self.lock:
            if email.lower() in self.state.emails:
                raise taken
        password_hash = hash_password(password)
        with self.lock:
            state = self.state
            if email.lower() in state.emails:
                raise taken
            uid = self._new_id("u_", state.users)
            state.users[uid] = User(uid, email, display_name, password_hash)
            state.emails[email.lower()] = uid
            token = self._new_token(uid)
        return 201, {"user_id": uid, "display_name": display_name, "token": token}

    def login(self, req):
        body = parse_object(req.body)
        email, password = self._credentials(body, ("email", "password"))
        with self.lock:
            state = self.state
            uid = state.emails.get(email.lower())
            stored = state.users[uid].password_hash if uid else None
        if stored is None or not verify_password(password, stored):
            raise unauthenticated("wrong email or password")
        with self.lock:
            if self.state is not state or uid not in state.users:
                raise unauthenticated("wrong email or password")
            user = state.users[uid]
            token = self._new_token(uid)
        return 200, {"user_id": uid, "display_name": user.display_name, "token": token}

    # ---- restaurants, policies and availability -----------------------------------------------

    def restaurants(self, req):
        with self.lock:
            return 200, {"restaurants": [r.summary() for r in self.state.restaurants.values()]}

    def restaurant(self, req):
        with self.lock:
            r = self.state.restaurants.get(req.params[0])
            if r is None:
                raise not_found("restaurant not found")
            return 200, r.detail()

    def list_policies(self, req):
        with self.lock:
            r = self.state.restaurants.get(req.params[0])
            if r is None:
                raise not_found("restaurant not found")
            return 200, {"policies": [p.view() for p in r.policies]}

    def publish_policy(self, req):
        with self.lock:
            uid = self._auth(req)
            restaurant = self.state.restaurants.get(req.params[0])
            if restaurant is None:
                raise not_found("restaurant not found")
            if uid not in restaurant.managers:
                raise ApiError(403, "forbidden", "only the restaurant's managers may publish policies")
            key = self._idempotency_key(req)
            body = parse_object(req.body)
            replay = self._replay(uid, key, req, body)
            if replay:
                return replay
            policy = parse_policy(body, restaurant, len(restaurant.policies) + 1)
            restaurant.policies.append(policy)
            restaurant.revision += 1
            response = policy.view()
            self._record(uid, key, req, body, 201, response)
            return 201, response

    def availability(self, req):
        q = req.query
        for name in ("restaurant_id", "date", "party_size"):
            if name not in q:
                raise invalid(f"query parameter {name} is required")
        day = clock.parse_date(q["date"])
        if day is None:
            raise invalid("date must be a real YYYY-MM-DD date")
        if not _DIGITS_RE.fullmatch(q["party_size"]) or int(q["party_size"]) < 1:
            raise invalid("party_size must be a positive integer")
        if "explain" in q and q["explain"] != "true":
            raise invalid("explain only accepts true")
        explain = "explain" in q
        party = int(q["party_size"])
        with self.lock:
            restaurant = self.state.restaurants.get(q["restaurant_id"])
            if restaurant is None:
                raise not_found("restaurant not found")
            terms = restaurant.terms_for(day)
            duration = timedelta(minutes=terms.duration)
            booked = [r for r in self.state.reservations.values()
                      if r.status == "confirmed" and r.restaurant_id == restaurant.id]
            options = [o for o in restaurant.options() if terms.seats(o) >= party]
            result = []
            for local, start in clock.slots(restaurant.zone, terms, day):
                end = start + duration
                busy = {t for r in booked if r.starts_utc < end and start < r.ends_utc
                        for t in r.table_ids}
                busy.update(c.table_id for c in restaurant.closures
                            if c.start < end and start < c.end)
                free = [o for o in options if busy.isdisjoint(o)]
                slot = {
                    "starts_at_local": clock.format_local(local),
                    "starts_at": clock.render(start, restaurant.zone),
                    "available_table_ids": [o[0] for o in free if len(o) == 1],
                    "available_options": [{"table_ids": list(o), "capacity": terms.seats(o)}
                                          for o in free],
                }
                if explain:
                    slot["explain"] = [self._explain(t, terms, party, busy)
                                       for t in restaurant.table_ids]
                result.append(slot)
            return 200, {"restaurant_id": restaurant.id, "date": q["date"],
                         "timezone": restaurant.timezone, "slots": result}

    @staticmethod
    def _explain(table_id, terms, party, busy):
        capacity = party <= terms.capacity(table_id)
        no_overlap = table_id not in busy
        return {"table_id": table_id, "policy_version": terms.version,
                "available": capacity and no_overlap,
                "rules": [{"rule": "capacity", "holds": capacity},
                          {"rule": "no_overlap", "holds": no_overlap}]}

    # ---- reservations -------------------------------------------------------------------------

    def create_reservation(self, req):
        with self.lock:
            uid = self._auth(req)
            key = self._idempotency_key(req)
            body = parse_object(req.body)
            replay = self._replay(uid, key, req, body)
            if replay:
                return replay
            response = self._create(uid, body)
            self._record(uid, key, req, body, 201, response)
            return 201, response

    def _create(self, uid, body):
        for name in ("restaurant_id", "table_id", "starts_at_local", "party_size"):
            if name == "table_id":
                if "table_id" not in body and "table_ids" not in body:
                    raise invalid("table_id or table_ids is required")
            elif name not in body:
                raise invalid(f"{name} is required")
        for name in ("restaurant_id", "table_id", "starts_at_local"):
            if name in body and not isinstance(body[name], str):
                raise malformed(f"{name} must be a string")
        if not valid_party(body["party_size"]):
            raise invalid("party_size must be an integer >= 1")
        local = clock.parse_local(body["starts_at_local"])
        if local is None:
            raise invalid("starts_at_local must be a local YYYY-MM-DDTHH:MM")
        ids = self._requested_tables(body, malformed)
        restaurant = self.state.restaurants.get(body["restaurant_id"])
        if restaurant is None:
            raise not_found("restaurant not found")
        table_ids, party = self._resolve_tables(restaurant, ids), body["party_size"]
        start, terms = self._check_booking(restaurant, table_ids, local, party)
        if self._overlaps(restaurant, table_ids, start,
                          start + timedelta(minutes=terms.duration), exclude=()):
            raise self._unavailable()
        res = self._new_reservation(uid, restaurant, table_ids, party, local, start, terms)
        restaurant.revision += 1
        return self._view(res)

    def _new_reservation(self, uid, restaurant, table_ids, party, local, start, terms):
        now = self._now()
        res = Reservation(
            id=self._new_id("res_", self.state.reservations), reference=self._new_reference(),
            user_id=uid, restaurant_id=restaurant.id, table_ids=list(table_ids), party_size=party,
            status="confirmed", starts_local=local, starts_utc=start, created_at=now, terms=terms)
        res.record("created", created_changes(res), now)
        self.state.add_reservation(res)
        return res

    def list_reservations(self, req):
        with self.lock:
            uid = self._auth(req)
            mine = [r for r in self.state.reservations.values() if r.user_id == uid]
            mine.sort(key=lambda r: r.starts_utc, reverse=True)
            return 200, {"reservations": [self._view(r) for r in mine]}

    def get_reservation(self, req):
        with self.lock:
            uid = self._auth(req)
            return 200, self._view(self._own(uid, req.params[0]))

    def history(self, req):
        with self.lock:
            res = self._own(self._optional_user(req), req.params[0])
            zone = self.state.restaurants[res.restaurant_id].zone
            return 200, {"reference": res.reference,
                         "entries": [h.view(zone) for h in res.history]}

    def decision(self, req):
        with self.lock:
            res = self._own(self._optional_user(req), req.params[0])
            return 200, {"reference": res.reference, "revision": res.revision,
                         "accepted_terms": res.terms.snapshot()}

    def cancel(self, req):
        with self.lock:
            uid = self._auth(req)
            res = self._own(uid, req.params[0])
            if res.status == "cancelled":
                return 200, self._view(res)
            if self._cutoff_passed(res):
                raise ApiError(409, "cutoff_passed", "the cancellation cutoff has passed")
            res.status = "cancelled"
            res.revision += 1
            res.record("cancelled", [], self._now())
            self.state.restaurants[res.restaurant_id].revision += 1
            if res.series_id:
                self.state.series[res.series_id].revision += 1
            return 200, self._view(res)

    @staticmethod
    def _expected_revision(res, item):
        if "expected_revision" not in item:
            return
        expected = item["expected_revision"]
        if not is_int(expected) or expected < 1:
            raise invalid("expected_revision must be a positive integer")
        if expected != res.revision:
            raise ApiError(409, "stale_revision", "the reservation has changed since that revision")

    @staticmethod
    def _is_noop(res, item):
        """Every supplied amendable field equals the current value (a table set as a set)."""
        if "table_id" in item and "table_ids" in item:
            return False
        for name in _AMENDABLE:
            if name not in item:
                continue
            v = item[name]
            if name == "table_id":
                same = isinstance(v, str) and res.table_ids == [v]
            elif name == "table_ids":
                same = (isinstance(v, list) and all(isinstance(t, str) for t in v)
                        and len(set(v)) == len(v) and set(v) == set(res.table_ids))
            elif name == "starts_at_local":
                same = v == clock.format_local(res.starts_local)
            else:
                same = is_int(v) and v == res.party_size
            if not same:
                return False
        return True

    def _amendable(self, res):
        if res.status == "cancelled":
            raise ApiError(409, "reservation_cancelled", "the reservation is cancelled")
        if self._cutoff_passed(res):
            raise ApiError(409, "cutoff_passed", "the amendment cutoff has passed")

    def _amended_values(self, res, item, wrong_type):
        """Resulting (table_ids, local, party, changed) of an amendment, D5 b and E10 checks."""
        for name in ("table_id", "starts_at_local"):
            if name in item and not isinstance(item[name], str):
                raise wrong_type(f"{name} must be a string")
        party = item.get("party_size", res.party_size)
        if not valid_party(party):
            raise invalid("party_size must be an integer >= 1")
        local = res.starts_local
        if "starts_at_local" in item:
            local = clock.parse_local(item["starts_at_local"])
            if local is None:
                raise invalid("starts_at_local must be a local YYYY-MM-DDTHH:MM")
        ids = self._requested_tables(item, wrong_type)
        table_ids = res.table_ids
        if ids is not None:
            table_ids = self._resolve_tables(self.state.restaurants[res.restaurant_id], ids)
        changed = (table_ids, local, party) != (res.table_ids, res.starts_local, res.party_size)
        return table_ids, local, party, changed

    def _apply(self, res, table_ids, local, party, start, terms, now):
        """A real amendment: new values and terms, one revision, one `changed` entry."""
        changes = table_changes(res.table_ids, table_ids)
        if local != res.starts_local:
            changes.append({"field": "starts_at_local", "from": clock.format_local(res.starts_local),
                            "to": clock.format_local(local)})
        if party != res.party_size:
            changes.append({"field": "party_size", "from": res.party_size, "to": party})
        res.table_ids, res.starts_local, res.party_size, res.starts_utc, res.terms = (
            list(table_ids), local, party, start, terms)
        res.revision += 1
        res.record("changed", changes, now)

    def patch(self, req):
        with self.lock:
            uid = self._auth(req)
            body = parse_object(req.body)
            res = self._own(uid, req.params[0])
            self._expected_revision(res, body)
            if res.status == "cancelled":
                raise ApiError(409, "reservation_cancelled", "the reservation is cancelled")
            noop = self._is_noop(res, body)
            if self._cutoff_passed(res):
                raise ApiError(409, "cutoff_passed", "the amendment cutoff has passed")
            if noop:
                return 200, self._view(res)
            table_ids, local, party, changed = self._amended_values(res, body, malformed)
            if not changed:
                return 200, self._view(res)
            restaurant = self.state.restaurants[res.restaurant_id]
            start, terms = self._check_booking(restaurant, table_ids, local, party)
            if self._overlaps(restaurant, table_ids, start,
                              start + timedelta(minutes=terms.duration), exclude=(res.id,)):
                raise self._unavailable()
            self._apply(res, table_ids, local, party, start, terms, self._now())
            restaurant.revision += 1
            if res.series_id:
                series = self.state.series[res.series_id]
                series.exceptions.add(res.id)
                series.revision += 1
            return 200, self._view(res)

    # ---- atomic moves -------------------------------------------------------------------------

    def moves(self, req):
        with self.lock:
            uid = self._auth(req)
            key = self._idempotency_key(req)
            body = parse_object(req.body)
            replay = self._replay(uid, key, req, body)
            if replay:
                return replay
            response = self._move(uid, body.get("moves"))
            self._record(uid, key, req, body, 201, response)
            return 201, response

    @staticmethod
    def _move_shape(moves):
        if not isinstance(moves, list) or not 1 <= len(moves) <= _MAX_MOVES:
            raise invalid(f"moves must be an array of 1 to {_MAX_MOVES} items")
        seen = set()
        for item in moves:
            if not isinstance(item, dict):
                raise invalid("each move must be an object")
            ref = item.get("reference")
            if not isinstance(ref, str):
                raise invalid("each move needs a string reference")
            if ref in seen:
                raise invalid("references must be distinct")
            seen.add(ref)
            for name in ("table_id", "starts_at_local"):
                if name in item and not isinstance(item[name], str):
                    raise invalid(f"{name} must be a string")
            if "table_ids" in item and (not isinstance(item["table_ids"], list) or not all(
                    isinstance(t, str) for t in item["table_ids"])):
                raise invalid("table_ids must be an array of strings")
            if "table_id" in item and "table_ids" in item:
                raise invalid("send either table_id or table_ids, not both")
            if "party_size" in item and not is_int(item["party_size"]):
                raise invalid("party_size must be an integer")

    def _move(self, uid, moves):
        self._move_shape(moves)
        planned = []
        restaurant = None
        for item in moves:
            res = self._own(uid, item["reference"])
            if restaurant is None:
                restaurant = self.state.restaurants[res.restaurant_id]
            elif res.restaurant_id != restaurant.id:
                raise invalid("all moved reservations must belong to the same restaurant")
            self._expected_revision(res, item)
            self._amendable(res)
            table_ids, local, party, changed = self._amended_values(res, item, invalid)
            start, end, terms = res.starts_utc, res.ends_utc, res.terms
            if changed:
                start, terms = self._check_booking(restaurant, table_ids, local, party)
                end = start + timedelta(minutes=terms.duration)
            planned.append((res, table_ids, local, party, start, end, terms, changed))

        listed = {p[0].id for p in planned}
        for i, (res, table_ids, _, _, start, end, _, changed) in enumerate(planned):
            if changed and self._overlaps(restaurant, table_ids, start, end, exclude=listed):
                raise self._unavailable()
            for other in planned[i + 1:]:
                if not (changed or other[7]) or set(other[1]).isdisjoint(table_ids):
                    continue
                if other[4] < end and start < other[5]:
                    raise self._unavailable()

        now = self._now()
        touched_series = set()
        for res, table_ids, local, party, start, _, terms, changed in planned:
            if changed:
                self._apply(res, table_ids, local, party, start, terms, now)
                if res.series_id:
                    self.state.series[res.series_id].exceptions.add(res.id)
                    touched_series.add(res.series_id)
        if any(p[7] for p in planned):
            restaurant.revision += 1
        for sid in touched_series:
            self.state.series[sid].revision += 1
        return {"reservations": [self._view(p[0]) for p in planned]}

    # ---- recurring reservations ---------------------------------------------------------------

    def create_series(self, req):
        with self.lock:
            uid = self._auth(req)
            key = self._idempotency_key(req)
            body = parse_object(req.body)
            replay = self._replay(uid, key, req, body)
            if replay:
                return replay
            response = self._adopt(uid, body)
            self._record(uid, key, req, body, 201, response)
            return 201, response

    def _adopt(self, uid, body):
        anchor_ref = body.get("anchor_reference")
        count, interval = body.get("count"), body.get("interval_weeks")
        if not isinstance(anchor_ref, str):
            raise invalid("anchor_reference must be a string")
        if not is_int(count) or not 2 <= count <= 12:
            raise invalid("count must be an integer from 2 to 12")
        if not is_int(interval) or not 1 <= interval <= 4:
            raise invalid("interval_weeks must be an integer from 1 to 4")
        anchor = self._own(uid, anchor_ref)
        if anchor.status == "cancelled":
            raise ApiError(409, "reservation_cancelled", "the reservation is cancelled")
        if anchor.series_id is not None:
            raise ApiError(409, "already_in_series", "the reservation already belongs to a series")
        if self._cutoff_passed(anchor):
            raise ApiError(409, "cutoff_passed", "the anchor's cutoff has passed")
        restaurant = self.state.restaurants[anchor.restaurant_id]
        tables = anchor.table_ids
        planned = []
        for i in range(1, count):
            local = anchor.starts_local + timedelta(days=7 * interval * i)
            start, terms = self._check_booking(restaurant, tables, local, anchor.party_size)
            end = start + timedelta(minutes=terms.duration)
            if self._overlaps(restaurant, tables, start, end, exclude=()) or any(
                    s < end and start < e for _, s, e, _ in planned):
                raise self._unavailable()
            planned.append((local, start, end, terms))

        series_id = self._new_id("ser_", self.state.series)
        occurrences = [anchor.id]
        for local, start, _, terms in planned:
            res = self._new_reservation(uid, restaurant, tables, anchor.party_size, local, start,
                                        terms)
            res.series_id = series_id
            occurrences.append(res.id)
        anchor.series_id = series_id
        series = Series(series_id, uid, interval, occurrences,
                        anchor_date=anchor.starts_local.date())
        self.state.series[series_id] = series
        restaurant.revision += 1
        return self._series_view(series)

    def _series_view(self, series):
        occurrences = []
        for index, rid in enumerate(series.occurrences):
            res = self.state.reservations[rid]
            occurrences.append({"index": index, "reference": res.reference,
                                "exception": rid in series.exceptions,
                                "reservation": self._view(res)})
        return {"series_id": series.id, "revision": series.revision,
                "interval_weeks": series.interval_weeks, "occurrences": occurrences}

    def get_series(self, req):
        with self.lock:
            uid = self._optional_user(req)
            series = self.state.series.get(req.params[0])
            if series is None or uid is None or series.user_id != uid:
                raise not_found("series not found")
            return 200, self._series_view(series)

    def amend_series(self, req):
        with self.lock:
            uid = self._auth(req)
            key = self._idempotency_key(req)
            body = parse_object(req.body)
            replay = self._replay(uid, key, req, body)
            if replay:
                return replay
            response = self._amend_series(uid, req.params[0], body)
            self._record(uid, key, req, body, 201, response)
            return 201, response

    def _amend_series(self, uid, series_id, body):
        series = self.state.series.get(series_id)
        if series is None or series.user_id != uid:
            raise not_found("series not found")
        expected, from_index = body.get("expected_revision"), body.get("from_index")
        local_time = body.get("local_time")
        if not is_int(expected) or expected < 1:
            raise invalid("expected_revision must be a positive integer")
        if not is_int(from_index) or not 0 <= from_index < len(series.occurrences):
            raise invalid("from_index must be an occurrence index")
        minute = clock.parse_hhmm(local_time)
        if minute is None:
            raise invalid("local_time must be HH:MM")
        if expected != series.revision:
            raise ApiError(409, "stale_revision", "the series has changed since that revision")

        restaurant = None
        planned = []
        for index in range(from_index, len(series.occurrences)):
            res = self.state.reservations[series.occurrences[index]]
            if res.status == "cancelled" or res.id in series.exceptions:
                continue
            restaurant = self.state.restaurants[res.restaurant_id]
            day = series.scheduled_date(index)
            local = clock.day_start(day) + timedelta(minutes=minute)
            if local == res.starts_local:
                continue  # identical result: a no-op that keeps its terms
            if self._cutoff_passed(res):
                raise ApiError(409, "cutoff_passed", "an occurrence's cutoff has passed")
            start, terms = self._check_booking(restaurant, res.table_ids, local, res.party_size)
            planned.append((res, local, start, start + timedelta(minutes=terms.duration), terms))
        if not planned:
            return self._series_view(series)

        changed = {p[0].id for p in planned}
        for i, (res, _, start, end, _) in enumerate(planned):
            if self._overlaps(restaurant, res.table_ids, start, end, exclude=changed):
                raise self._unavailable()
            for other in planned[i + 1:]:
                if (not set(other[0].table_ids).isdisjoint(res.table_ids)
                        and other[2] < end and start < other[3]):
                    raise self._unavailable()

        now = self._now()
        for res, local, start, _, terms in planned:
            self._apply(res, res.table_ids, local, res.party_size, start, terms, now)
        series.revision += 1
        restaurant.revision += 1
        return self._series_view(series)

    # ---- seating changes after a table closure ------------------------------------------------

    def _manager(self, req):
        """401, 404 unknown restaurant, 403 non-manager (G2, G6). Returns (user, restaurant)."""
        uid = self._auth(req)
        restaurant = self.state.restaurants.get(req.params[0])
        if restaurant is None:
            raise not_found("restaurant not found")
        if uid not in restaurant.managers:
            raise ApiError(403, "forbidden", "only the restaurant's managers may do this")
        return uid, restaurant

    def replan(self, req):
        with self.lock:
            uid, restaurant = self._manager(req)
            key = self._idempotency_key(req)
            body = parse_object(req.body)
            replay = self._replay(uid, key, req, body)
            if replay:
                return replay
            response = self._replan(restaurant, body)
            self._record(uid, key, req, body, 201, response)
            return 201, response

    def _replan(self, restaurant, body):
        table_id = body.get("table_id")
        start, end = parse_instant(body.get("from")), parse_instant(body.get("to"))
        if not isinstance(table_id, str):
            raise invalid("table_id must be a string")
        if start is None or end is None:
            raise invalid("from and to must be RFC 3339 date-times with an offset")
        if start >= end:
            raise invalid("from must be earlier than to")
        if table_id not in restaurant.table_ids:
            raise not_found("table not found")
        considered = sorted((r for r in self.state.reservations.values()
                             if r.status == "confirmed" and r.restaurant_id == restaurant.id
                             and r.starts_utc < end and start < r.ends_utc),
                            key=lambda r: r.reference)
        options = restaurant.options()
        if (len(restaurant.table_ids) > _PLAN_MAX_TABLES
                or len(options) - len(restaurant.table_ids) > _PLAN_MAX_PAIRS
                or len(considered) > _PLAN_MAX_BOOKINGS):
            raise ApiError(422, "planning_limit", "this closure is too large to plan")

        ids = {r.id for r in considered}
        fixed = [r for r in self.state.reservations.values()
                 if r.status == "confirmed" and r.restaurant_id == restaurant.id
                 and r.id not in ids]
        bookings = []
        for r in considered:
            candidates = []
            for rank, option in enumerate(options):
                if table_id in option or r.terms.seats(option) < r.party_size:
                    continue
                if restaurant.closed(option, r.starts_utc, r.ends_utc) or any(
                        not set(option).isdisjoint(f.table_ids)
                        and f.starts_utc < r.ends_utc and r.starts_utc < f.ends_utc
                        for f in fixed):
                    continue
                candidates.append(Candidate(tuple(option), rank,
                                            r.terms.seats(option) - r.party_size,
                                            set(option) != set(r.table_ids)))
            bookings.append(Booking(r.id, r.starts_utc, r.ends_utc, candidates))
        chosen = best_plan(bookings)
        if chosen is None:
            raise ApiError(409, "no_feasible_plan", "no seating plan satisfies every booking")

        plan_id = self._new_id("plan_", self.state.plans)
        closure = Closure(table_id, body["from"], body["to"], start, end, plan_id)
        plan = Plan(plan_id, restaurant.id, restaurant.revision, closure,
                    [(r.id, list(c.tables), c.changed) for r, c in zip(considered, chosen)])
        self.state.plans[plan_id] = plan
        return {
            "plan_id": plan_id,
            "restaurant_revision": restaurant.revision,
            "closure": closure.view(),
            "assignments": [{"reference": r.reference, "table_ids": list(c.tables),
                             "changed": c.changed} for r, c in zip(considered, chosen)],
            "moved_count": sum(c.changed for c in chosen),
            "unused_seats": sum(c.unused for c in chosen),
        }

    def apply_plan(self, req):
        with self.lock:
            uid, restaurant = self._manager(req)
            key = self._idempotency_key(req)
            body = parse_object(req.body)
            replay = self._replay(uid, key, req, body)
            if replay:
                return replay
            plan = self.state.plans.get(req.params[1])
            if plan is None or plan.restaurant_id != restaurant.id:
                raise not_found("plan not found")
            if plan.applied:
                raise ApiError(409, "plan_already_applied", "this plan has already been applied")
            if plan.revision != restaurant.revision:
                raise ApiError(409, "stale_plan", "the restaurant has changed since this plan")
            response = self._apply_plan(restaurant, plan)
            self._record(uid, key, req, body, 201, response)
            return 201, response

    def _apply_plan(self, restaurant, plan):
        now = self._now()
        touched_series = set()
        for rid, tables, changed in plan.assignments:
            res = self.state.reservations[rid]
            if not changed:
                continue
            before = list(res.table_ids)
            res.table_ids = list(tables)
            res.revision += 1
            res.record("reassigned", [{"field": "table_ids", "from": before, "to": list(tables)}],
                       now, plan_id=plan.id)
            if res.series_id:
                touched_series.add(res.series_id)
        restaurant.closures.append(plan.closure)
        plan.applied = True
        restaurant.revision += 1
        for sid in touched_series:
            self.state.series[sid].revision += 1
        return {"plan_id": plan.id, "restaurant_revision": restaurant.revision,
                "reservations": [self._view(self.state.reservations[rid])
                                 for rid, _, _ in plan.assignments]}
