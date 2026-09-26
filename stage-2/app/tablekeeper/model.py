"""Service state, its construction from reset fixtures and exports, and its export form."""

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import clock
from .errors import invalid
from .passwords import hash_password, parse_hash

STATE_SCHEMA = 1
STATUSES = ("confirmed", "cancelled")
REFERENCE_RE = re.compile(r"[A-Z0-9]{6,12}")


@dataclass
class Window:
    weekday: str
    opens: int
    closes: int
    opens_text: str
    closes_text: str


@dataclass
class Restaurant:
    id: str
    name: str
    timezone: str
    zone: ZoneInfo
    slot_minutes: int
    duration: int
    cutoff: int
    hours: list
    tables: list  # fixture-shaped dicts, in fixture order
    capacity: dict  # table id -> capacity

    def summary(self):
        return {"id": self.id, "name": self.name, "timezone": self.timezone}

    def detail(self):
        return {
            "id": self.id,
            "name": self.name,
            "timezone": self.timezone,
            "slot_minutes": self.slot_minutes,
            "reservation_duration_minutes": self.duration,
            "cancellation_cutoff_minutes": self.cutoff,
            "opening_hours": [{"weekday": w.weekday, "opens": w.opens_text, "closes": w.closes_text}
                              for w in self.hours],
            "tables": [dict(t) for t in self.tables],
        }


@dataclass
class User:
    id: str
    email: str
    display_name: str
    password_hash: str


@dataclass
class Reservation:
    id: str
    reference: str
    user_id: str
    restaurant_id: str
    table_id: str
    party_size: int
    status: str
    starts_local: datetime  # naive wall-clock time at the restaurant
    starts_utc: datetime
    created_at: datetime

    def ends_utc(self, restaurant):
        return self.starts_utc + timedelta(minutes=restaurant.duration)

    def view(self, restaurant):
        return {
            "reservation_id": self.id,
            "reference": self.reference,
            "restaurant_id": self.restaurant_id,
            "table_id": self.table_id,
            "party_size": self.party_size,
            "status": self.status,
            "starts_at_local": clock.format_local(self.starts_local),
            "starts_at": clock.render(self.starts_utc, restaurant.zone),
            "ends_at": clock.render(self.ends_utc(restaurant), restaurant.zone),
            "created_at": clock.render_utc(self.created_at),
        }


@dataclass
class IdempotencyRecord:
    user_id: str
    key: str
    method: str
    path: str
    request: str  # canonical JSON of the request body
    status: int
    response: dict


@dataclass
class State:
    users: dict = field(default_factory=dict)  # id -> User, insertion ordered
    emails: dict = field(default_factory=dict)  # lowercased email -> user id
    tokens: dict = field(default_factory=dict)  # token -> user id
    restaurants: dict = field(default_factory=dict)  # id -> Restaurant, fixture ordered
    reservations: dict = field(default_factory=dict)  # id -> Reservation, insertion ordered
    references: dict = field(default_factory=dict)  # reference -> reservation id
    idempotency: dict = field(default_factory=dict)  # (user, key, method, path) -> record

    def add_reservation(self, r):
        self.reservations[r.id] = r
        self.references[r.reference] = r.id

    def add_idempotency(self, rec):
        self.idempotency[(rec.user_id, rec.key, rec.method, rec.path)] = rec

    def export(self):
        return {
            "schema": STATE_SCHEMA,
            "users": [{"id": u.id, "email": u.email, "display_name": u.display_name,
                       "password_hash": u.password_hash} for u in self.users.values()],
            "tokens": [{"token": t, "user_id": uid} for t, uid in self.tokens.items()],
            "restaurants": [r.detail() for r in self.restaurants.values()],
            "reservations": [{
                "id": r.id, "reference": r.reference, "user_id": r.user_id,
                "restaurant_id": r.restaurant_id, "table_id": r.table_id,
                "party_size": r.party_size, "status": r.status,
                "starts_at_local": clock.format_local(r.starts_local),
                "created_at": clock.render_utc(r.created_at),
            } for r in self.reservations.values()],
            "idempotency": [{
                "user_id": i.user_id, "key": i.key, "method": i.method, "path": i.path,
                "request": i.request, "status": i.status, "response": i.response,
            } for i in self.idempotency.values()],
        }


# ---- structural validation helpers ------------------------------------------------------------

def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _id(obj, name):
    v = obj.get(name)
    if not isinstance(v, str) or not 1 <= len(v) <= 64:
        raise invalid(f"{name} must be a string of 1 to 64 characters")
    return v


def _str(obj, name, allow_empty=False):
    v = obj.get(name)
    if not isinstance(v, str) or (not allow_empty and v == ""):
        raise invalid(f"{name} must be a string")
    return v


def _int(obj, name, minimum):
    v = obj.get(name)
    if not is_int(v) or v < minimum:
        raise invalid(f"{name} must be an integer >= {minimum}")
    return v


def _list(obj, name, required=False):
    if name not in obj and not required:
        return []
    v = obj.get(name)
    if not isinstance(v, list):
        raise invalid(f"{name} must be an array")
    return v


def _obj(v, what):
    if not isinstance(v, dict):
        raise invalid(f"{what} must be an object")
    return v


def _restaurant(raw):
    _obj(raw, "restaurant")
    rid = _id(raw, "id")
    name = _str(raw, "name", allow_empty=True)
    tz = _str(raw, "timezone")
    try:
        zone = ZoneInfo(tz)
    except Exception:
        raise invalid(f"unknown time zone {tz!r}")
    slot = _int(raw, "slot_minutes", 1)
    duration = _int(raw, "reservation_duration_minutes", 1)
    cutoff = _int(raw, "cancellation_cutoff_minutes", 0)
    hours = []
    for h in _list(raw, "opening_hours"):
        _obj(h, "opening_hours entry")
        weekday = h.get("weekday")
        if weekday not in clock.WEEKDAYS:
            raise invalid("weekday must be one of mon tue wed thu fri sat sun")
        opens = clock.parse_hhmm(h.get("opens"))
        closes = clock.parse_hhmm(h.get("closes"), allow_midnight_end=True)
        if opens is None or closes is None or closes <= opens:
            raise invalid("opens and closes must be HH:MM with closes later than opens")
        hours.append(Window(weekday, opens, closes, h["opens"], h["closes"]))
    tables, capacity = [], {}
    for t in _list(raw, "tables"):
        _obj(t, "table")
        tid = _id(t, "id")
        if tid in capacity:
            raise invalid(f"duplicate table id {tid!r}")
        cap = _int(t, "capacity", 1)
        table = {"id": tid}
        if "label" in t:
            table["label"] = _str(t, "label", allow_empty=True)
        table["capacity"] = cap
        tables.append(table)
        capacity[tid] = cap
    return Restaurant(rid, name, tz, zone, slot, duration, cutoff, hours, tables, capacity)


def _created_at(raw, default):
    if "created_at" not in raw or raw["created_at"] is None:
        return default
    v = raw["created_at"]
    if not isinstance(v, str):
        raise invalid("created_at must be a string")
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        raise invalid("created_at must be an RFC 3339 timestamp")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0)


def _reservation(raw, state, default_created, statuses):
    _obj(raw, "reservation")
    rid = _id(raw, "id")
    reference = raw.get("reference")
    if not isinstance(reference, str) or not REFERENCE_RE.fullmatch(reference):
        raise invalid("reference must be 6 to 12 characters of A-Z0-9")
    user_id = _id(raw, "user_id")
    restaurant_id = _id(raw, "restaurant_id")
    table_id = _id(raw, "table_id")
    restaurant = state.restaurants.get(restaurant_id)
    if restaurant is None or table_id not in restaurant.capacity:
        raise invalid("reservation refers to an unknown restaurant or table")
    party = _int(raw, "party_size", 1)
    local = clock.parse_local(raw.get("starts_at_local"))
    if local is None:
        raise invalid("starts_at_local must be YYYY-MM-DDTHH:MM")
    status = raw.get("status", "confirmed") if statuses else "confirmed"
    if status not in STATUSES:
        raise invalid("unknown reservation status")
    if rid in state.reservations or reference in state.references:
        raise invalid("duplicate reservation id or reference")
    utc, _ = clock.resolve(restaurant.zone, local)
    return Reservation(rid, reference, user_id, restaurant_id, table_id, party, status, local, utc,
                       _created_at(raw, default_created))


def _user_fields(u):
    """The user fields shared by reset fixtures and exports, validated by one rule."""
    _obj(u, "user")
    return _id(u, "id"), _str(u, "email"), _str(u, "display_name", allow_empty=True)


def _add_user(state, uid, email, display_name, password_hash):
    if uid in state.users:
        raise invalid(f"duplicate user id {uid!r}")
    if email.lower() in state.emails:
        raise invalid(f"duplicate email {email!r}")
    state.users[uid] = User(uid, email, display_name, password_hash)
    state.emails[email.lower()] = uid


def _add_restaurants(state, raws):
    for raw in raws:
        r = _restaurant(raw)
        if r.id in state.restaurants:
            raise invalid(f"duplicate restaurant id {r.id!r}")
        state.restaurants[r.id] = r


def from_fixture(fixture):
    """Build a fresh state from a reset fixture (spec section 4, D14). Raises ApiError 422."""
    _obj(fixture, "fixture")
    users = _list(fixture, "users")
    for u in users:
        _user_fields(u)
        _str(u, "password", allow_empty=True)
    restaurants = _list(fixture, "restaurants")
    reservations = _list(fixture, "reservations")

    state = State()
    _add_restaurants(state, restaurants)
    now = clock.now_utc().replace(microsecond=0)
    for raw in reservations:
        state.add_reservation(_reservation(raw, state, now, statuses=False))
    # Structure is validated before any slow hashing happens.
    for u in users:
        _add_user(state, u["id"], u["email"], u["display_name"], "")
    with ThreadPoolExecutor(max_workers=4) as pool:
        hashes = list(pool.map(hash_password, [u["password"] for u in users]))
    for u, h in zip(users, hashes):
        state.users[u["id"]].password_hash = h
    return state


def from_export(doc):
    """Rebuild a state from an export document (spec section 10, D17). Raises ApiError 422."""
    _obj(doc, "import body")
    if doc.get("track") != "tablekeeper":
        raise invalid("track must be tablekeeper")
    if not is_int(doc.get("format_version")) or doc["format_version"] != 1:
        raise invalid("format_version must be 1")
    raw = _obj(doc.get("state"), "state")
    if not is_int(raw.get("schema")) or raw["schema"] != STATE_SCHEMA:
        raise invalid("unsupported state schema")

    state = State()
    for u in _list(raw, "users", required=True):
        uid, email, display_name = _user_fields(u)
        if parse_hash(u.get("password_hash")) is None:
            raise invalid("invalid password hash")
        _add_user(state, uid, email, display_name, u["password_hash"])
    for t in _list(raw, "tokens", required=True):
        _obj(t, "token")
        token, uid = _str(t, "token"), _id(t, "user_id")
        if uid not in state.users or token in state.tokens:
            raise invalid("invalid token entry")
        state.tokens[token] = uid
    _add_restaurants(state, _list(raw, "restaurants", required=True))
    now = clock.now_utc().replace(microsecond=0)
    for r in _list(raw, "reservations", required=True):
        state.add_reservation(_reservation(r, state, now, statuses=True))
    for i in _list(raw, "idempotency", required=True):
        _obj(i, "idempotency record")
        rec = IdempotencyRecord(_id(i, "user_id"), _str(i, "key"), _str(i, "method"),
                                _str(i, "path"), _str(i, "request"), i.get("status"),
                                i.get("response"))
        if not is_int(rec.status) or not isinstance(rec.response, dict) or len(rec.key) > 255:
            raise invalid("invalid idempotency record")
        state.add_idempotency(rec)
    return state
