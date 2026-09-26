"""Service state, its construction from reset fixtures and exports, and its export form."""

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import clock
from .errors import invalid
from .passwords import hash_password, parse_hash

STATE_SCHEMA = 3
STAGE_1_SCHEMA = 1  # exports of the stage-1 service: single tables, no combinable pairs
STAGE_2_SCHEMA = 2  # exports of the stage-2 service: no policies, revisions or history
STATUSES = ("confirmed", "cancelled")
EVENTS = ("created", "changed", "cancelled")
REFERENCE_RE = re.compile(r"[A-Z0-9]{6,12}")


@dataclass(frozen=True)
class Window:
    weekday: str
    opens: int
    closes: int
    opens_text: str
    closes_text: str

    def view(self):
        return {"weekday": self.weekday, "opens": self.opens_text, "closes": self.closes_text}


@dataclass(frozen=True)
class Terms:
    """A complete booking policy without its effective date; policy 0 is the fixture's rules."""
    version: int
    slot_minutes: int
    duration: int
    cutoff: int
    hours: tuple  # Windows
    capacities: tuple  # (table id, capacity) in table order

    def capacity(self, table_id):
        return dict(self.capacities)[table_id]

    def seats(self, table_ids):
        caps = dict(self.capacities)
        return sum(caps[t] for t in table_ids)

    def snapshot(self):
        return {
            "policy_version": self.version,
            "slot_minutes": self.slot_minutes,
            "reservation_duration_minutes": self.duration,
            "cancellation_cutoff_minutes": self.cutoff,
            "opening_hours": [w.view() for w in self.hours],
            "capacities": dict(self.capacities),
        }


@dataclass(frozen=True)
class Policy:
    effective_from: date
    terms: Terms

    def view(self):
        v = {"effective_from": self.effective_from.isoformat()}
        v.update({k: val for k, val in self.terms.snapshot().items() if k != "policy_version"})
        v["policy_version"] = self.terms.version
        return v


@dataclass
class Restaurant:
    id: str
    name: str
    timezone: str
    zone: ZoneInfo
    base: Terms  # policy 0: the fixture's own rules
    tables: list  # fixture-shaped dicts, in fixture order
    combinable: list  # declared pairs, fixture order, each as given
    managers: list = field(default_factory=list)
    policies: list = field(default_factory=list)  # published Policy objects, publication order
    revision: int = 0  # internal restaurant revision (F5), not exposed in stage 3

    @property
    def table_ids(self):
        return [t["id"] for t in self.tables]

    def terms_for(self, day):
        """The policy for a local date: greatest effective_from <= day, ties by version (F2)."""
        best = None
        for p in self.policies:
            if p.effective_from <= day and (best is None or (p.effective_from, p.terms.version) >
                                            (best.effective_from, best.terms.version)):
                best = p
        return best.terms if best else self.base

    def pair(self, ids):
        """The declared pair holding exactly these two tables, in combinable order, or None."""
        wanted = set(ids)
        for p in self.combinable:
            if set(p) == wanted:
                return list(p)
        return None

    def options(self):
        """Every bookable table set: singles in fixture order, then distinct declared pairs."""
        sets = [[t["id"]] for t in self.tables]
        seen = set()
        for p in self.combinable:
            if frozenset(p) not in seen:
                seen.add(frozenset(p))
                sets.append(list(p))
        return sets

    def summary(self):
        return {"id": self.id, "name": self.name, "timezone": self.timezone}

    def detail(self):
        """The original fixture configuration (managers and policies are not exposed)."""
        return {
            "id": self.id,
            "name": self.name,
            "timezone": self.timezone,
            "slot_minutes": self.base.slot_minutes,
            "reservation_duration_minutes": self.base.duration,
            "cancellation_cutoff_minutes": self.base.cutoff,
            "opening_hours": [w.view() for w in self.base.hours],
            "tables": [dict(t) for t in self.tables],
            "combinable": [list(p) for p in self.combinable],
        }


@dataclass
class User:
    id: str
    email: str
    display_name: str
    password_hash: str


@dataclass
class HistoryEntry:
    seq: int
    at: datetime  # UTC
    event: str
    changes: list
    revision: int
    terms: Terms

    def view(self, zone):
        return {"seq": self.seq, "at": clock.render(self.at, zone), "event": self.event,
                "changes": [dict(c) for c in self.changes], "revision": self.revision,
                "accepted_terms": self.terms.snapshot()}


@dataclass
class Reservation:
    id: str
    reference: str
    user_id: str
    restaurant_id: str
    table_ids: list  # one table, or a declared pair in combinable order
    party_size: int
    status: str
    starts_local: datetime  # naive wall-clock time at the restaurant
    starts_utc: datetime
    created_at: datetime
    terms: Terms  # accepted terms
    revision: int = 1
    history: list = field(default_factory=list)
    series_id: str = None

    @property
    def ends_utc(self):
        return self.starts_utc + timedelta(minutes=self.terms.duration)

    def view(self, restaurant):
        view = {
            "reservation_id": self.id,
            "reference": self.reference,
            "restaurant_id": self.restaurant_id,
            "table_ids": list(self.table_ids),
            "party_size": self.party_size,
            "status": self.status,
            "starts_at_local": clock.format_local(self.starts_local),
            "starts_at": clock.render(self.starts_utc, restaurant.zone),
            "ends_at": clock.render(self.ends_utc, restaurant.zone),
            "created_at": clock.render_utc(self.created_at),
            "revision": self.revision,
            "accepted_terms": self.terms.snapshot(),
        }
        if len(self.table_ids) == 1:
            view["table_id"] = self.table_ids[0]
        return view

    def record(self, event, changes, at):
        """Append a history entry; `at` never goes back in time (history rule 1)."""
        if self.history and at < self.history[-1].at:
            at = self.history[-1].at
        self.history.append(HistoryEntry(len(self.history) + 1, at, event, changes,
                                         self.revision, self.terms))


def table_changes(old, new):
    """The history change for a table set (combined-table history rules)."""
    if old is not None and list(old) == list(new):
        return []
    if old is not None and len(old) == 1 and len(new) == 1:
        return [{"field": "table_id", "from": old[0], "to": new[0]}]
    if old is None and len(new) == 1:
        return [{"field": "table_id", "from": None, "to": new[0]}]
    return [{"field": "table_ids", "from": None if old is None else list(old), "to": list(new)}]


def created_changes(res):
    return table_changes(None, res.table_ids) + [
        {"field": "starts_at_local", "from": None, "to": clock.format_local(res.starts_local)},
        {"field": "party_size", "from": None, "to": res.party_size}]


def synthesize_history(res):
    """History of a booking that predates stage 3: one created entry, plus cancelled (F8)."""
    res.history = [HistoryEntry(1, res.created_at, "created", created_changes(res), 1, res.terms)]
    if res.status == "cancelled":
        res.history.append(HistoryEntry(2, res.created_at, "cancelled", [], 1, res.terms))


@dataclass
class Series:
    id: str
    user_id: str
    interval_weeks: int
    occurrences: list  # reservation ids in index order
    revision: int = 1
    exceptions: set = field(default_factory=set)  # reservation ids marked as diner exceptions


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
    series: dict = field(default_factory=dict)  # id -> Series
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
            "restaurants": [dict(r.detail(), manager_user_ids=list(r.managers),
                                 policies=[p.view() for p in r.policies], revision=r.revision)
                            for r in self.restaurants.values()],
            "reservations": [{
                "id": r.id, "reference": r.reference, "user_id": r.user_id,
                "restaurant_id": r.restaurant_id, "table_ids": list(r.table_ids),
                "party_size": r.party_size, "status": r.status,
                "starts_at_local": clock.format_local(r.starts_local),
                "created_at": clock.render_utc(r.created_at),
                "revision": r.revision, "accepted_terms": r.terms.snapshot(),
                "series_id": r.series_id,
                "history": [{"seq": h.seq, "at": clock.render_utc(h.at), "event": h.event,
                             "changes": h.changes, "revision": h.revision,
                             "accepted_terms": h.terms.snapshot()} for h in r.history],
            } for r in self.reservations.values()],
            "series": [{"id": s.id, "user_id": s.user_id, "interval_weeks": s.interval_weeks,
                        "occurrences": list(s.occurrences), "revision": s.revision,
                        "exceptions": [o for o in s.occurrences if o in s.exceptions]}
                       for s in self.series.values()],
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


def _int(obj, name, minimum, maximum=None):
    v = obj.get(name)
    if not is_int(v) or v < minimum or (maximum is not None and v > maximum):
        raise invalid(f"{name} must be an integer from {minimum}"
                      + (f" to {maximum}" if maximum is not None else ""))
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


def parse_hours(raw, unique_weekdays):
    """Stage-1 opening hours; a policy additionally forbids duplicate weekdays."""
    if not isinstance(raw, list):
        raise invalid("opening_hours must be an array")
    hours, seen = [], set()
    for h in raw:
        _obj(h, "opening_hours entry")
        weekday = h.get("weekday")
        if not isinstance(weekday, str) or weekday not in clock.WEEKDAYS:
            raise invalid("weekday must be one of mon tue wed thu fri sat sun")
        if unique_weekdays and weekday in seen:
            raise invalid("opening_hours must not repeat a weekday")
        seen.add(weekday)
        opens = clock.parse_hhmm(h.get("opens"))
        closes = clock.parse_hhmm(h.get("closes"), allow_midnight_end=True)
        if opens is None or closes is None or closes <= opens:
            raise invalid("opens and closes must be HH:MM with closes later than opens")
        hours.append(Window(weekday, opens, closes, h["opens"], h["closes"]))
    return tuple(hours)


def parse_capacities(raw, table_ids, maximum=None):
    """A capacities object naming exactly the restaurant's tables, in table order."""
    if not isinstance(raw, dict) or set(raw) != set(table_ids):
        raise invalid("capacities must name exactly the restaurant's tables")
    for t in table_ids:
        v = raw[t]
        if not is_int(v) or v < 1 or (maximum is not None and v > maximum):
            raise invalid("capacities must be integers" +
                          (f" from 1 to {maximum}" if maximum else " >= 1"))
    return tuple((t, raw[t]) for t in table_ids)


def parse_policy(raw, restaurant, version):
    """A published policy (policy section): every problem is 422 validation_failed."""
    for name in ("effective_from", "slot_minutes", "reservation_duration_minutes",
                 "cancellation_cutoff_minutes", "opening_hours", "capacities"):
        if name not in raw:
            raise invalid(f"{name} is required")
    effective = clock.parse_date(raw["effective_from"])
    if effective is None:
        raise invalid("effective_from must be a real YYYY-MM-DD date")
    terms = Terms(version, _int(raw, "slot_minutes", 1, 1440),
                  _int(raw, "reservation_duration_minutes", 1, 1440),
                  _int(raw, "cancellation_cutoff_minutes", 0, 10080),
                  parse_hours(raw["opening_hours"], unique_weekdays=True),
                  parse_capacities(raw["capacities"], restaurant.table_ids, maximum=100))
    return Policy(effective, terms)


def _terms_snapshot(raw, restaurant):
    """Accepted terms as exported: a policy snapshot with its version."""
    _obj(raw, "accepted_terms")
    return Terms(_int(raw, "policy_version", 0), _int(raw, "slot_minutes", 1),
                 _int(raw, "reservation_duration_minutes", 1),
                 _int(raw, "cancellation_cutoff_minutes", 0),
                 parse_hours(raw.get("opening_hours"), unique_weekdays=False),
                 parse_capacities(raw.get("capacities"), restaurant.table_ids))


def _restaurant(raw, exported=False):
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
    hours = parse_hours(_list(raw, "opening_hours"), unique_weekdays=False)
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
    combinable = []
    for p in _list(raw, "combinable"):
        if (not isinstance(p, list) or len(p) != 2 or not all(isinstance(t, str) for t in p)
                or p[0] == p[1] or not all(t in capacity for t in p)):
            raise invalid("combinable entries must be pairs of two distinct tables of the "
                          "restaurant")
        combinable.append(list(p))
    managers = _list(raw, "manager_user_ids")
    if not all(isinstance(m, str) for m in managers):
        raise invalid("manager_user_ids must be an array of user ids")
    base = Terms(0, slot, duration, cutoff, hours, tuple(capacity.items()))
    restaurant = Restaurant(rid, name, tz, zone, base, tables, combinable, list(managers))
    if exported:
        for i, p in enumerate(_list(raw, "policies")):
            _obj(p, "policy")
            if p.get("policy_version") != i + 1 or not is_int(p.get("policy_version")):
                raise invalid("policy versions must run from 1 in publication order")
            restaurant.policies.append(parse_policy(p, restaurant, i + 1))
        restaurant.revision = _int(raw, "revision", 0) if "revision" in raw else 0
    return restaurant


def _created_at(raw, default):
    if "created_at" not in raw or raw["created_at"] is None:
        return default
    return _timestamp(raw["created_at"], "created_at")


def _timestamp(v, name):
    if not isinstance(v, str):
        raise invalid(f"{name} must be a string")
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        raise invalid(f"{name} must be an RFC 3339 timestamp")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0)


def _seeded_tables(raw, restaurant):
    """`table_id` or `table_ids` (one table or two distinct tables) of a seeded reservation."""
    if ("table_id" in raw) == ("table_ids" in raw):
        raise invalid("a reservation needs exactly one of table_id and table_ids")
    ids = [_id(raw, "table_id")] if "table_id" in raw else raw["table_ids"]
    if (not isinstance(ids, list) or not 1 <= len(ids) <= 2 or len(set(map(str, ids))) != len(ids)
            or not all(isinstance(t, str) and t in restaurant.table_ids for t in ids)):
        raise invalid("reservation tables must be one or two distinct tables of the restaurant")
    return (restaurant.pair(ids) or list(ids)) if len(ids) == 2 else list(ids)


def _reservation(raw, state, default_created, schema=None):
    """A seeded (schema None) or imported reservation."""
    _obj(raw, "reservation")
    rid = _id(raw, "id")
    reference = raw.get("reference")
    if not isinstance(reference, str) or not REFERENCE_RE.fullmatch(reference):
        raise invalid("reference must be 6 to 12 characters of A-Z0-9")
    user_id = _id(raw, "user_id")
    restaurant_id = _id(raw, "restaurant_id")
    restaurant = state.restaurants.get(restaurant_id)
    if restaurant is None:
        raise invalid("reservation refers to an unknown restaurant")
    if schema == STAGE_1_SCHEMA and "table_ids" in raw:
        raise invalid("stage-1 reservations carry table_id only")
    table_ids = _seeded_tables(raw, restaurant)
    party = _int(raw, "party_size", 1)
    local = clock.parse_local(raw.get("starts_at_local"))
    if local is None:
        raise invalid("starts_at_local must be YYYY-MM-DDTHH:MM")
    status = raw.get("status", "confirmed")
    if status not in STATUSES:
        raise invalid("unknown reservation status")
    if rid in state.reservations or reference in state.references:
        raise invalid("duplicate reservation id or reference")
    utc, _ = clock.resolve(restaurant.zone, local)
    created = _created_at(raw, default_created)
    if schema != STATE_SCHEMA:
        # Seeded, stage-1 and stage-2 bookings: revision 1 under policy 0 (F8).
        res = Reservation(rid, reference, user_id, restaurant_id, table_ids, party, status, local,
                          utc, created, restaurant.base)
        synthesize_history(res)
        return res
    res = Reservation(rid, reference, user_id, restaurant_id, table_ids, party, status, local,
                      utc, created, _terms_snapshot(raw.get("accepted_terms"), restaurant),
                      _int(raw, "revision", 1))
    for i, h in enumerate(_list(raw, "history", required=True)):
        _obj(h, "history entry")
        if h.get("seq") != i + 1 or h.get("event") not in EVENTS or not isinstance(
                h.get("changes"), list):
            raise invalid("invalid history entry")
        res.history.append(HistoryEntry(i + 1, _timestamp(h.get("at"), "at"), h["event"],
                                        h["changes"], _int(h, "revision", 1),
                                        _terms_snapshot(h.get("accepted_terms"), restaurant)))
    if not res.history:
        raise invalid("a reservation needs its history")
    series_id = raw.get("series_id")
    if series_id is not None and not isinstance(series_id, str):
        raise invalid("series_id must be a string")
    res.series_id = series_id
    return res


def _series(raw, state):
    _obj(raw, "series")
    sid, uid = _id(raw, "id"), _id(raw, "user_id")
    occurrences = _list(raw, "occurrences", required=True)
    exceptions = _list(raw, "exceptions")
    if (sid in state.series or not 2 <= len(occurrences) <= 12
            or len(set(map(str, occurrences))) != len(occurrences)
            or not all(isinstance(o, str) and o in state.reservations
                       and state.reservations[o].series_id == sid for o in occurrences)
            or not all(o in occurrences for o in exceptions)):
        raise invalid("invalid series")
    return Series(sid, uid, _int(raw, "interval_weeks", 1, 4), list(occurrences),
                  _int(raw, "revision", 1), set(exceptions))


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


def _add_restaurants(state, raws, exported=False):
    for raw in raws:
        r = _restaurant(raw, exported)
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
        state.add_reservation(_reservation(raw, state, now))
    # Structure is validated before any slow hashing happens.
    for u in users:
        _add_user(state, u["id"], u["email"], u["display_name"], "")
    with ThreadPoolExecutor(max_workers=4) as pool:
        hashes = list(pool.map(hash_password, [u["password"] for u in users]))
    for u, h in zip(users, hashes):
        state.users[u["id"]].password_hash = h
    return state


def from_export(doc):
    """Rebuild a state from an export document (spec section 10, D17, E13, F12).

    Accepts this service's exports and those of the stage-1 and stage-2 services, migrating
    older bookings to revision 1 under policy 0. Raises ApiError 422."""
    _obj(doc, "import body")
    if doc.get("track") != "tablekeeper":
        raise invalid("track must be tablekeeper")
    if not is_int(doc.get("format_version")) or doc["format_version"] != 1:
        raise invalid("format_version must be 1")
    raw = _obj(doc.get("state"), "state")
    schema = raw.get("schema")
    if not is_int(schema) or schema not in (STAGE_1_SCHEMA, STAGE_2_SCHEMA, STATE_SCHEMA):
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
    _add_restaurants(state, _list(raw, "restaurants", required=True),
                     exported=schema == STATE_SCHEMA)
    now = clock.now_utc().replace(microsecond=0)
    for r in _list(raw, "reservations", required=True):
        state.add_reservation(_reservation(r, state, now, schema=schema))
    if schema == STATE_SCHEMA:
        for s in _list(raw, "series", required=True):
            series = _series(s, state)
            state.series[series.id] = series
        for r in state.reservations.values():
            if r.series_id is not None and r.id not in state.series.get(r.series_id,
                                                                         Series("", "", 1, [])).occurrences:
                raise invalid("reservation refers to an unknown series")
    for i in _list(raw, "idempotency", required=True):
        _obj(i, "idempotency record")
        rec = IdempotencyRecord(_id(i, "user_id"), _str(i, "key"), _str(i, "method"),
                                _str(i, "path"), _str(i, "request"), i.get("status"),
                                i.get("response"))
        if not is_int(rec.status) or not isinstance(rec.response, dict) or len(rec.key) > 255:
            raise invalid("invalid idempotency record")
        state.add_idempotency(rec)
    return state
