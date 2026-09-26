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
from .model import (IdempotencyRecord, Reservation, State, User, from_export, from_fixture,
                    is_int)
from .passwords import hash_password, verify_password

_BEARER_RE = re.compile(r"Bearer ([^\s]+)")
_DIGITS_RE = re.compile(r"[0-9]+")
_REFERENCE_ALPHABET = string.ascii_uppercase + string.digits
_REFERENCE_LENGTH = 8
_MAX_MOVES = 8


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

    def _own(self, user_id, reference):
        rid = self.state.references.get(reference)
        res = self.state.reservations.get(rid) if rid else None
        if res is None or res.user_id != user_id:
            raise not_found("reservation not found")
        return res

    def _view(self, res):
        return res.view(self.state.restaurants[res.restaurant_id])

    def _cutoff_passed(self, res):
        restaurant = self.state.restaurants[res.restaurant_id]
        return clock.now_utc() >= res.starts_utc - timedelta(minutes=restaurant.cutoff)

    def _overlaps(self, restaurant, table_id, start, exclude):
        duration = timedelta(minutes=restaurant.duration)
        end = start + duration
        for other in self.state.reservations.values():
            if (other.status == "confirmed" and other.id not in exclude
                    and other.restaurant_id == restaurant.id and other.table_id == table_id
                    and other.starts_utc < end and start < other.starts_utc + duration):
                return True
        return False

    def _check_booking(self, restaurant, table_id, local, party):
        """D5 c..g for a known restaurant. Returns the start instant."""
        if table_id not in restaurant.capacity:
            raise not_found("table not found")
        start, error = clock.check_start(restaurant, local)
        if error == "invalid_local_time":
            raise ApiError(422, error, "that local time does not exist in the restaurant's zone")
        if error == "outside_opening_hours":
            raise ApiError(422, error, "the reservation is outside opening hours")
        if error == "not_on_slot_grid":
            raise ApiError(422, error, "the start time is not on the slot grid")
        if party > restaurant.capacity[table_id]:
            raise ApiError(422, "party_exceeds_capacity", "party size exceeds the table capacity")
        return start

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

    # ---- restaurants and availability ---------------------------------------------------------

    def restaurants(self, req):
        with self.lock:
            return 200, {"restaurants": [r.summary() for r in self.state.restaurants.values()]}

    def restaurant(self, req):
        with self.lock:
            r = self.state.restaurants.get(req.params[0])
            if r is None:
                raise not_found("restaurant not found")
            return 200, r.detail()

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
        party = int(q["party_size"])
        with self.lock:
            restaurant = self.state.restaurants.get(q["restaurant_id"])
            if restaurant is None:
                raise not_found("restaurant not found")
            duration = timedelta(minutes=restaurant.duration)
            booked = [r for r in self.state.reservations.values()
                      if r.status == "confirmed" and r.restaurant_id == restaurant.id]
            result = []
            for local, start in clock.slots(restaurant, day):
                end = start + duration
                busy = {r.table_id for r in booked
                        if r.starts_utc < end and start < r.starts_utc + duration}
                result.append({
                    "starts_at_local": clock.format_local(local),
                    "starts_at": clock.render(start, restaurant.zone),
                    "available_table_ids": [t["id"] for t in restaurant.tables
                                            if t["capacity"] >= party and t["id"] not in busy],
                })
            return 200, {"restaurant_id": restaurant.id, "date": q["date"],
                         "timezone": restaurant.timezone, "slots": result}

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
            if name not in body:
                raise invalid(f"{name} is required")
        for name in ("restaurant_id", "table_id", "starts_at_local"):
            if not isinstance(body[name], str):
                raise malformed(f"{name} must be a string")
        if not valid_party(body["party_size"]):
            raise invalid("party_size must be an integer >= 1")
        local = clock.parse_local(body["starts_at_local"])
        if local is None:
            raise invalid("starts_at_local must be a local YYYY-MM-DDTHH:MM")
        restaurant = self.state.restaurants.get(body["restaurant_id"])
        if restaurant is None:
            raise not_found("restaurant not found")
        table_id, party = body["table_id"], body["party_size"]
        start = self._check_booking(restaurant, table_id, local, party)
        if self._overlaps(restaurant, table_id, start, exclude=()):
            raise self._unavailable()
        res = Reservation(
            id=self._new_id("res_", self.state.reservations), reference=self._new_reference(),
            user_id=uid, restaurant_id=restaurant.id, table_id=table_id, party_size=party,
            status="confirmed", starts_local=local, starts_utc=start,
            created_at=clock.now_utc().replace(microsecond=0))
        self.state.add_reservation(res)
        return self._view(res)

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

    def cancel(self, req):
        with self.lock:
            uid = self._auth(req)
            res = self._own(uid, req.params[0])
            if res.status == "cancelled":
                return 200, self._view(res)
            if self._cutoff_passed(res):
                raise ApiError(409, "cutoff_passed", "the cancellation cutoff has passed")
            res.status = "cancelled"
            return 200, self._view(res)

    def _amendable(self, res):
        if res.status == "cancelled":
            raise ApiError(409, "reservation_cancelled", "the reservation is cancelled")
        if self._cutoff_passed(res):
            raise ApiError(409, "cutoff_passed", "the amendment cutoff has passed")

    @staticmethod
    def _amended_values(res, item):
        """Resulting (table_id, local, party, changed) after value checks; types already checked."""
        table_id = item.get("table_id", res.table_id)
        party = item.get("party_size", res.party_size)
        if not valid_party(party):
            raise invalid("party_size must be an integer >= 1")
        local = res.starts_local
        if "starts_at_local" in item:
            local = clock.parse_local(item["starts_at_local"])
            if local is None:
                raise invalid("starts_at_local must be a local YYYY-MM-DDTHH:MM")
        changed = (table_id, local, party) != (res.table_id, res.starts_local, res.party_size)
        return table_id, local, party, changed

    def _apply(self, res, table_id, local, party, start):
        res.table_id, res.starts_local, res.party_size, res.starts_utc = (
            table_id, local, party, start)

    def patch(self, req):
        with self.lock:
            uid = self._auth(req)
            body = parse_object(req.body)
            res = self._own(uid, req.params[0])
            self._amendable(res)
            for name in ("table_id", "starts_at_local"):
                if name in body and not isinstance(body[name], str):
                    raise malformed(f"{name} must be a string")
            table_id, local, party, changed = self._amended_values(res, body)
            if not changed:
                return 200, self._view(res)
            restaurant = self.state.restaurants[res.restaurant_id]
            start = self._check_booking(restaurant, table_id, local, party)
            if self._overlaps(restaurant, table_id, start, exclude=(res.id,)):
                raise self._unavailable()
            self._apply(res, table_id, local, party, start)
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
            self._amendable(res)
            table_id, local, party, changed = self._amended_values(res, item)
            start = res.starts_utc
            if changed:
                start = self._check_booking(restaurant, table_id, local, party)
            planned.append((res, table_id, local, party, start, changed))

        listed = {p[0].id for p in planned}
        duration = timedelta(minutes=restaurant.duration)
        for i, (res, table_id, _, _, start, changed) in enumerate(planned):
            if changed and self._overlaps(restaurant, table_id, start, exclude=listed):
                raise self._unavailable()
            for other in planned[i + 1:]:
                if not (changed or other[5]) or other[1] != table_id:
                    continue
                if other[4] < start + duration and start < other[4] + duration:
                    raise self._unavailable()

        for res, table_id, local, party, start, changed in planned:
            if changed:
                self._apply(res, table_id, local, party, start)
        return {"reservations": [self._view(p[0]) for p in planned]}
