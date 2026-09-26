# Increment 1 brief: `stage-1/` (Tablekeeper stage 1: reservations)

Planner: planner seat. Source: `stage-1.md` (full text at the end of this brief, Part B).
Directory: `stage-1/` at the repository root, nothing outside it.

Check command (set with factory-stage; `{repo}` = fresh clone, `{out}` = new output dir, `{n}` = 1):

    cd /home/geekendzone/hackathon/dark-factory-wearedevs && .venv/bin/python -m harness run --track tablekeeper --repo {repo} --stage 1 --mode isolated --out {out} > {out}.log 2>&1; cat {out}.log; grep -q "^highest contiguous stage: 1$" {out}.log && grep -q "^claimed stage: 1" {out}.log

Faster self-check variant: the same command without `--mode isolated`. It passes only when the
stage-1 suite passes and the stage-2 suite does NOT (so nothing from stage 2 may be built here).
The shipped checks are a partial sample: build to the specification text in Part B.

Contract: `stage-1/Dockerfile` and `stage-1/RUN.md` whose command builds and starts the service
with no manual steps. Image runs alone with `-e PORT=<port>` plus a port mapping, listens on
0.0.0.0:$PORT (default 8080), no outbound network at run time (all dependencies, IANA time zone
data included, baked into the image at build time), 2 vCPU / 2 GiB, healthy within 60 s,
50 concurrent requests, 5 s per request (10 s for reset/import/export).

## Part A1. Decisions on open points (binding for builder and verifier)

D1. Storage and concurrency. Language and framework are the builder's choice. In-memory state is
    allowed (state need not survive a restart). Every state-changing operation (including the
    idempotency lookup and record for that request) must run as one atomic critical section
    (e.g. a single global write lock), so concurrent requests behave as if executed one at a time.
    Password hashing (slow) must run outside that lock so 50 concurrent logins stay under 5 s.
    Keep the hash cost moderate (e.g. scrypt N=2^14 or bcrypt cost 10).
D2. Stack choice must also serve a browser UI in stage 2 (HTML/JS assets from the same image).
    Not built now; just do not choose something that cannot.
D3. Export state format: a JSON object with an internal schema marker (e.g. `"schema": 1`)
    so later stages can import stage-1 exports and migrate them. `format_version` stays `1`.
D4. Request processing order for authenticated writes with idempotency
    (`POST /reservations`, `POST /reservation-moves`):
    1) 401 auth; 2) 400 `missing_idempotency_key` (absent or empty header);
    3) 422 `validation_failed` if the key exceeds 255 characters; 4) 400 `malformed_request` if the
    body does not parse or is not a JSON object; 5) idempotency resolution (replay 200 /
    409 `idempotency_key_reuse`); 6) endpoint validation and business rules.
    Idempotency records are scoped by (user, key, method, path). Only 2xx outcomes are recorded;
    a 4xx leaves the key unused. Body equality is JSON-value equality (key order, whitespace
    irrelevant).
D5. `POST /reservations` validation order after idempotency:
    a) required fields present (`restaurant_id`, `table_id`, `starts_at_local`, `party_size`)
       else 422 `validation_failed`;
    b) wrong JSON type for `restaurant_id`/`table_id` (non-string) or non-string `starts_at_local`
       -> 400 `malformed_request`; any invalid `party_size` (string, boolean, float, < 1)
       -> 422 `validation_failed`; `starts_at_local` string not exactly `YYYY-MM-DDTHH:MM` with a
       real calendar date and 00..23:00..59 -> 422 `validation_failed`;
    c) unknown restaurant, unknown table, table of another restaurant -> 404 `not_found`;
    d) nonexistent local time (DST gap) -> 422 `invalid_local_time`;
    e) closed weekday, start before `opens`, or end (start + duration, absolute) after `closes`
       -> 422 `outside_opening_hours`;
    f) not on the grid (local minutes since `opens` not a multiple of `slot_minutes`)
       -> 422 `not_on_slot_grid`;
    g) `party_size` > table capacity -> 422 `party_exceeds_capacity`;
    h) overlap with a confirmed reservation on that table -> 409 `table_unavailable`.
D6. Cutoff rule: cancellation/amendment is refused (409 `cutoff_passed`) when
    `now >= starts_at - cancellation_cutoff_minutes` (the boundary instant is inside the cutoff).
D7. Cancel order: 401 -> 404 (unknown or not the caller's) -> already cancelled: 200 with current
    state (checked before the cutoff) -> 409 `cutoff_passed` -> cancel, 200.
D8. PATCH order: 401 -> 400 body parse/non-object -> 404 reservation -> 409
    `reservation_cancelled` -> 409 `cutoff_passed` (against the current start) -> validation of the
    resulting booking exactly as D5 b..h (overlap excludes the booking itself). Response 200 with
    the full reservation. A PATCH with no amendable field, or with identical values, is a no-op
    that still requires a confirmed booking outside the cutoff, and returns 200 unchanged.
    `restaurant_id` in a PATCH body is ignored.
D9. Availability query: missing parameter -> 422; `date` not a real `YYYY-MM-DD` -> 422;
    `party_size` not plain decimal digits or < 1 -> 422; unknown restaurant -> 404 `not_found`.
D10. DST and slots: generate candidate local wall-clock times from `opens` in `slot_minutes`
    steps; drop nonexistent local times; resolve each remaining time to its first occurrence;
    keep it when start + duration (absolute) <= the instant of `closes` on that local date
    (resolved with the same first-occurrence rule; a nonexistent `closes` shifts forward by the
    gap). Booking checks use exactly the same rule, so every listed slot is bookable and vice
    versa. `starts_at`/`ends_at` are rendered with the restaurant zone's offset at that instant.
D11. Timestamps: `starts_at`/`ends_at` in the restaurant's offset; `created_at` in UTC written
    as `+00:00` (not `Z`), seconds precision.
D12. References: 8 characters from A-Z0-9, random, unique across all reservations (retry on
    collision). `reservation_id` is an opaque id (e.g. `res_<n>`), max 64 chars.
D13. Auth: emails compared case-insensitively (stored as given, matched lowercase) for signup
    uniqueness and login. Email valid = exactly one `@` with non-empty local and domain parts and
    no whitespace. `display_name` required non-empty string. Missing field -> 422; wrong JSON type
    -> 400. Validation (422) is checked before `email_taken` (409). Bearer header must be exactly
    `Bearer <token>`; anything else -> 401. Tokens are random opaque strings (>= 128 bits).
D14. Reset fixture: missing top-level arrays default to `[]`. A fixture that is not an object or
    has invalid data (e.g. an id longer than 64 characters, unknown weekday) -> 422
    `validation_failed` with state unchanged; unparseable JSON -> 400. Seeded reservations are
    trusted (no grid/hours validation), status `confirmed`, `created_at` taken from the fixture if
    present else the reset time.
D15. Unknown routes -> 404 `not_found`; wrong method on a known route -> 405 with the error body
    (code `method_not_allowed`). Every 4xx/5xx carries the §5 error body.
D16. Reservation moves (§11): any shape problem in `moves` (not an array, 0 or > 8 items, an item
    that is not an object, a missing or non-string `reference`, duplicate references, a wrong
    type in an item field) -> 422 `validation_failed`. Then a single pass in input order; for each
    item: 404 unknown/other owner -> 422 if its restaurant differs from the first item's ->
    409 `reservation_cancelled` -> 409 `cutoff_passed` (applies to every listed booking, even an
    unchanged one) -> field validation as D5 c..g. After the pass: occupancy of all resulting
    bookings together (listed bookings' old occupancy is released, so swaps succeed) against each
    other and unlisted confirmed bookings -> 409 `table_unavailable`. Success 201
    `{"reservations": [...]}` in input order; all-or-nothing.
D17. Export/import: import body must be an object with `track == "tablekeeper"`,
    `format_version == 1` and an object `state` that passes structural validation; otherwise
    422 `validation_failed` with state unchanged; unparseable JSON -> 400. Export is taken under
    the write lock (atomic snapshot).

## Part A2. Work orders

WORK ORDER 1.1: Runnable service skeleton, conventions, reset and restaurants
Directory: stage-1/, nothing outside it
Acceptance:
  - `stage-1/Dockerfile` builds; `stage-1/RUN.md` gives one command that builds and runs the image
    with `-e PORT` and a port mapping; no runtime network needed (§2).
  - Listens on 0.0.0.0:$PORT, default 8080 (§3.1); `GET /health` -> 200 `{"status":"ok"}` within
    60 s (§3.2).
  - `POST /_test/reset` replaces all state with the fixture and returns 204; repeated resets work;
    seeded users can log in; D14 (§3.3, §4).
  - JSON responses use `application/json; charset=utf-8`; unknown body fields and query params are
    ignored; every error uses the §5 envelope; D15 (§3.4, §5). No request produces a 5xx.
  - `GET /restaurants` and `GET /restaurants/{id}` are public and return the §8 shapes; unknown
    id -> 404 `not_found`.
Out of scope: anything from stage 2 or later (HTML screens, `combinable`, `table_ids`).

WORK ORDER 1.2: Signup, login and bearer authentication
Directory: stage-1/, nothing outside it
Acceptance:
  - `POST /auth/signup` 201 and `POST /auth/login` 200 with `user_id`, `display_name`, `token`;
    409 `email_taken`; 422 for short password (< 8), bad email, missing field; 401 on wrong
    password/unknown email; D13 (§6).
  - Passwords stored with a password-hashing function (scrypt/bcrypt/Argon2); never plaintext.
  - Tokens never expire; several valid tokens per account; every non-public endpoint returns 401
    `unauthenticated` for a missing, malformed or unknown token (§6).
Out of scope: email verification, password reset, refresh tokens, roles.

WORK ORDER 1.3: Availability with time zones and DST
Directory: stage-1/, nothing outside it
Acceptance:
  - `GET /availability` (public) returns the §8 shape; slot rule, fixture-ordered
    `available_table_ids`, empty lists kept, closed day -> `"slots": []`; D9, D10.
  - Europe/Berlin and America/New_York transitions of §9: skipped times never appear; repeated
    times appear once (first occurrence); offsets follow IANA rules for the date.
Out of scope: `available_options`, `explain`.

WORK ORDER 1.4: Reservations with idempotency and concurrency safety
Directory: stage-1/, nothing outside it
Acceptance:
  - `POST /reservations` per §8 and D4, D5, D11, D12; response shape exactly as §8; half-open
    occupancy `[starts_at, starts_at + duration)`; past starts allowed.
  - §7 idempotency in full: 201 first use; 200 replay with identical JSON body even after the
    booking changed or was cancelled; 409 reuse with a different body (even an invalid one);
    key reusable after a 4xx; same key on a different path is independent; per-user scope;
    concurrent identical requests -> exactly one 201, the rest 200, one booking.
  - Concurrent competing bookings of one table/slot -> exactly one 201, others 409, never 5xx.
  - `GET /reservations` (caller's, `starts_at` descending, both statuses), `GET
    /reservations/{reference}` (404 if not the caller's), `POST /reservations/{reference}/cancel`
    (D6, D7; frees the table immediately), `PATCH /reservations/{reference}` (D8; atomic; failure
    leaves the booking unchanged; reference and reservation_id survive).
Out of scope: `table_ids`, history, revisions, policies.

WORK ORDER 1.5: Export and import
Directory: stage-1/, nothing outside it
Acceptance:
  - `GET /_test/export` 200 `{"track":"tablekeeper","format_version":1,"state":{...}}`; import
    of that object 204 and atomically replaces all state (§10, D3, D17).
  - After export -> reset -> import (also into a fresh container): users log in with their
    passwords, existing tokens still work, restaurants, reservations, references, statuses,
    timestamps and ids are identical, idempotent replays return the original response with 200,
    failed keys remain reusable, reuse with a different body still 409.
  - Importing twice duplicates nothing; invalid imports -> 422 with state unchanged; reset clears
    imported state.
Out of scope: stage-2+ import migrations.

WORK ORDER 1.6: Atomic reservation moves
Directory: stage-1/, nothing outside it
Acceptance:
  - `POST /reservation-moves` per §11 and D4, D16: auth, idempotency, 1..8 distinct references,
    same-owner and same-restaurant rules, error precedence, all-or-nothing commit, swaps allowed,
    201 `{"reservations":[...]}` in input order including unchanged items, replay 200 with the
    original response after later changes, receipts survive export/import.
Out of scope: `table_ids` in moves (stage 2).

## Part A3. Out of scope for this increment

Everything in stage-2.md and later: HTML screens, `combinable`/`table_ids`/`available_options`,
`explain`, history, revisions, policies, series, replans. The stage-1 service must not pass the
stage-2 suite. Nothing outside `stage-1/`.

## Part B. Complete specification: stage-1.md (verbatim)

# Tablekeeper — Stage 1: reservations

This stage defines the initial service and its API.

Build from the supplied requirements. Source code, API documentation and schemas from
existing products in this domain must not be used.

## 1. Scope

Diners can search restaurant availability, book a table and receive a confirmation
reference. They can cancel or amend their bookings, including changing several bookings
together. Each restaurant has its own table capacities, opening hours and cancellation policy.
Only the HTTP API is required.

Two `confirmed` reservations must never occupy the same table at overlapping times,
including during concurrent requests. Occupancy is the half-open interval
`[starts_at, starts_at + reservation_duration)`. A 90-minute booking at 19:00 therefore
does not overlap a booking starting at 20:30. Retries and rejected requests must not
create duplicate or partial bookings.

## 2. Delivery and deployment

Deliver an HTTP service, a `Dockerfile` and a `RUN.md` with a command that builds and
starts the service without manual setup. Language, framework and storage are unrestricted.
A `docker-compose.yml` is optional.

The submission is a containerized HTTP service, not a Python package. Python is not
required in the implementation. TypeScript/JavaScript, Go, Rust, Java, Python and any
other language are equally valid. The harness builds the submitted `Dockerfile`, starts
the resulting image and tests only its HTTP behavior; it does not import or execute the
submission's source files on the judge host.

The image must run on its own with `-e PORT=<port>` and a port mapping. Runtime networking
has no outbound access. All runtime dependencies, initialization and seed data must work
within that single container. Compose configuration is not used to start the service.

### Resource limits

The service must operate within these limits:

| Limit | Value |
|---|---|
| CPU | 2 vCPU |
| Memory | 2 GiB |
| Start to first healthy response | 60 s |
| Concurrent requests | up to 50 in flight |
| Per-request timeout | 5 s (10 s for `POST /_test/reset`) |
| Outbound network | available during `docker build`, **none at run time** |
| Disk | ephemeral; state need not survive a container restart |

Runtime assets and dependencies must be included in the image. This includes fonts,
scripts and stylesheets; external services are unavailable at runtime.

## 3. Runtime contract

### 3.1 Listening

Listen on `0.0.0.0` using the `PORT` environment variable, default `8080`.

### 3.2 Health

```http
GET /health  ->  200  {"status": "ok"}
```

Return 200 once the service and its data store can serve requests, within 60 seconds
of container start. Non-200 responses are permitted before the service is ready.

### 3.3 Reset and seed

```http
POST /_test/reset
Content-Type: application/json

{ ...fixture... }

->  204 No Content
```

Replace all service state with the fixture in the request body (§4). When reset returns
204, subsequent requests must see only that fixture. Repeated resets are supported.
This test endpoint must be enabled in the delivered image and requires no authentication.

### 3.4 Conventions

- Requests and responses are `application/json; charset=utf-8`.
- Timestamps in responses are RFC 3339 with an explicit offset, e.g. `2026-09-24T19:00:00+02:00`.
- Unknown fields in a request body are ignored, never an error.
- Unknown query parameters are ignored.
- IDs are opaque strings of at most 64 characters. Their format is yours. This limit
  also applies to IDs supplied in reset fixtures.

## 4. Model

Restaurants and tables are supplied through `POST /_test/reset` only. Restaurant and
table creation endpoints are out of scope.

| Field | On | Meaning |
|---|---|---|
| `timezone` | Restaurant | IANA zone name, e.g. `Europe/Berlin`. All of the restaurant's times are local to this |
| `slot_minutes` | Restaurant | Bookings start on a grid of this many minutes from opening time |
| `reservation_duration_minutes` | Restaurant | How long every reservation occupies its table |
| `cancellation_cutoff_minutes` | Restaurant | A booking cannot be cancelled or changed within this many minutes of its start |
| `opening_hours` | Restaurant | Per weekday. A day with no entry is closed |
| `capacity` | Table | Maximum party size |

### Fixture format

```json
{
  "users": [
    { "id": "u_ada", "email": "ada@example.com",
      "password": "correct horse", "display_name": "Ada" }
  ],
  "restaurants": [
    {
      "id": "r_anker",
      "name": "Zum Anker",
      "timezone": "Europe/Berlin",
      "slot_minutes": 30,
      "reservation_duration_minutes": 90,
      "cancellation_cutoff_minutes": 120,
      "opening_hours": [
        { "weekday": "thu", "opens": "18:00", "closes": "23:00" },
        { "weekday": "fri", "opens": "18:00", "closes": "23:30" }
      ],
      "tables": [
        { "id": "t_1", "label": "1", "capacity": 2 },
        { "id": "t_2", "label": "2", "capacity": 4 }
      ]
    }
  ],
  "reservations": []
}
```

- `weekday` is one of `mon tue wed thu fri sat sun`.
- `opens` and `closes` are local `HH:MM`, 24-hour. `closes` is always later than `opens` on the
  same local day — opening hours never cross midnight.
- Seeded users must be able to log in with the given password immediately.
- `reservations` may seed confirmed bookings, with the same fields as a `POST /reservations`
  body plus `id`, `reference` and `user_id`.

Fixtures may use any calendar date. A booking must not be rejected solely because its
start is in the past; the cancellation and amendment cutoff rules still apply.

## 5. Errors

Every 4xx and 5xx response carries this body:

```json
{ "error": { "code": "table_unavailable", "message": "human readable, any wording" } }
```

Use the specified HTTP status and `code`. The human-readable `message` may use any wording.
Endpoint-specific errors are listed with each endpoint.

| Status | `code` | When |
|---|---|---|
| 400 | `malformed_request` | Unparseable body, or a field of the wrong JSON type |
| 400 | `missing_idempotency_key` | Required `Idempotency-Key` header absent or empty |
| 401 | `unauthenticated` | Missing, malformed or unknown bearer token |
| 403 | `forbidden` | Authenticated, but not permitted to touch this resource |
| 404 | `not_found` | No such resource, or not visible to this caller |
| 409 | `idempotency_key_reuse` | Key already used by this caller with a different request body |
| 422 | `validation_failed` | A required field or query parameter is missing, or a stated rule is violated with no more specific code |

A field of the correct JSON type with an invalid format or out-of-range value gives
422 `validation_failed`, unless an endpoint specifies a different error. This includes
invalid dates, negative counts and values exceeding a stated maximum or length. In addition:

- Endpoint-specific field rules take precedence: invalid `party_size` values (including strings
  and booleans) and `starts_at_local` strings that are not a bare local `YYYY-MM-DDTHH:MM` are
  422 `validation_failed`. Other wrong JSON types follow the rule below.
- An integer-valued **query parameter** is written as plain decimal digits: `1e9`, `4.0` and `+4`
  are 422 `validation_failed` whatever their numeric value.
- Reserve 400 `malformed_request` for a body that does not parse or a field of the wrong type.

Shared ranges, enforced on every endpoint that takes them:

| Field | Valid | Otherwise |
|---|---|---|
| `Idempotency-Key` | 1 to 255 characters | 422 `validation_failed` |

Requests must not produce 5xx responses, including under concurrent load.

## 6. Authentication

Authentication supports signup and login. Email verification, password reset, refresh
tokens and role-management endpoints are out of scope. Permissions specified elsewhere
in these requirements still apply.

```http
POST /auth/signup
{ "email": "a@example.com", "password": "correct horse", "display_name": "Ada" }

->  201  { "user_id": "u_1", "display_name": "Ada", "token": "..." }
```

```http
POST /auth/login
{ "email": "a@example.com", "password": "correct horse" }

->  200  { "user_id": "u_1", "display_name": "Ada", "token": "..." }
```

| Case | Response |
|---|---|
| Email already registered | 409 `email_taken` |
| Password shorter than 8 characters | 422 `validation_failed` |
| `email` not of the form `local@domain` | 422 `validation_failed` |
| Wrong password or unknown email on login | 401 `unauthenticated` |

Every other endpoint requires a bearer token, except `/health`, `/_test/reset`, the two above, and
the three public endpoints named at the top of §8 — `GET /restaurants`, `GET /restaurants/{id}` and
`GET /availability`:

```http
Authorization: Bearer <token>
```

Tokens do not expire. An account may have multiple valid tokens and concurrent sessions.

Passwords must be stored using a password-hashing function such as bcrypt, scrypt or
Argon2, or an equivalent. Plaintext password storage is not permitted.

## 7. Idempotency

Two write paths require an idempotency key: **`POST /reservations`** (§8) and
**`POST /reservation-moves`** (§11).

```http
Idempotency-Key: <client-chosen string, 1..255 characters>
```

The key is scoped to **the authenticated user**. Two different users may use the same key string
with no interaction between them.

A replay means the same user sending the **same method, the same path and the same body**. The
same key with the same body on a different path is a different request, not a replay, and must
succeed normally.

After the body has been parsed as a JSON object and the caller authenticated, idempotency
is resolved before endpoint-specific field validation or current-resource checks. Thus a
used key with a different JSON body returns `409 idempotency_key_reuse` even when that new
body would otherwise be invalid.

| Situation | Response |
|---|---|
| Header absent or empty | 400 `missing_idempotency_key` |
| First use of the key | The normal response, **201** |
| Replay: same key, same body | **200**, body identical to the original response as a JSON value |
| Same key, different body | 409 `idempotency_key_reuse` |
| Key reused after the original request failed with 4xx | Treated as a first use |

"Same body" means the same JSON value after parsing — key order and whitespace do not matter.

For concurrent identical requests with an unused key, exactly one returns 201.
The others return 200 with the same body. The operation takes effect only once.

A successful replay returns the original response, even after the resource changes or
is cancelled. It makes no further state changes.

## 8. API

`GET /restaurants`, `GET /restaurants/{id}` and `GET /availability` are **public** — no bearer
token. Everything else needs one. Diners browse before they sign in.

### `GET /restaurants`

```json
{ "restaurants": [ { "id": "r_anker", "name": "Zum Anker", "timezone": "Europe/Berlin" } ] }
```

### `GET /restaurants/{id}`

The restaurant with its `slot_minutes`, `reservation_duration_minutes`,
`cancellation_cutoff_minutes`, `opening_hours` and `tables`, in the fixture's shape. 404 if
unknown.

### `GET /availability`

```http
GET /availability?restaurant_id=r_anker&date=2026-09-24&party_size=4
```

All three parameters are required; a missing one is 422 `validation_failed`. `date` is a local
calendar date at the restaurant.

```json
{
  "restaurant_id": "r_anker",
  "date": "2026-09-24",
  "timezone": "Europe/Berlin",
  "slots": [
    { "starts_at_local": "2026-09-24T18:00",
      "starts_at": "2026-09-24T18:00:00+02:00",
      "available_table_ids": ["t_2"] }
  ]
}
```

`starts_at_local` is the full `YYYY-MM-DDTHH:MM` and goes into `POST /reservations` unchanged.

A slot appears for every `slot_minutes` step from `opens` such that
`slot + reservation_duration_minutes <= closes`. `available_table_ids` lists the tables of that
restaurant with `capacity >= party_size` and no overlapping confirmed reservation, in fixture
order. A slot with no available table still appears, with an empty list.

A closed day returns `"slots": []`.

### `POST /reservations`

`Idempotency-Key` is required; see §7.

```http
POST /reservations
Authorization: Bearer <token>
Idempotency-Key: 2f9c1a...

{ "restaurant_id": "r_anker", "table_id": "t_2",
  "starts_at_local": "2026-09-24T19:00", "party_size": 4 }
```

`starts_at_local` is wall-clock at the restaurant, with no offset and no `Z`. Resolve it against
the restaurant's `timezone`.

```json
201
{
  "reservation_id": "res_7",
  "reference": "K3P7QW",
  "restaurant_id": "r_anker",
  "table_id": "t_2",
  "party_size": 4,
  "status": "confirmed",
  "starts_at_local": "2026-09-24T19:00",
  "starts_at": "2026-09-24T19:00:00+02:00",
  "ends_at": "2026-09-24T20:30:00+02:00",
  "created_at": "2026-09-21T11:04:03+00:00"
}
```

`reference` is 6 to 12 characters of `A-Z0-9`, unique across all reservations, and never changes.

| Case | Response |
|---|---|
| The table is taken for an overlapping interval | 409 `table_unavailable` |
| `starts_at_local` is not on the slot grid | 422 `not_on_slot_grid` |
| Slot outside opening hours, or the reservation would end after `closes` | 422 `outside_opening_hours` |
| `party_size` exceeds the table's `capacity` | 422 `party_exceeds_capacity` |
| `party_size` below 1, or not an integer | 422 `validation_failed` |
| `starts_at_local` is a local time that does not exist (see §9) | 422 `invalid_local_time` |
| Unknown restaurant, unknown table, or the table belongs to another restaurant | 404 `not_found` |

### `GET /reservations`

The caller's reservations, `starts_at` descending, confirmed and cancelled alike.
Return `200` with `{"reservations": [...]}`; each entry has the same shape as the
create response. An empty list is `{"reservations": []}`.

### `GET /reservations/{reference}`

One reservation. **404 if it is not the caller's** — do not leak the existence of other people's
bookings.

### `POST /reservations/{reference}/cancel`

```json
200
{ "reference": "K3P7QW", "status": "cancelled", ... }
```

Frees the table immediately: the next `GET /availability` must offer that slot again.

| Case | Response |
|---|---|
| Already cancelled | 200 with the current state — cancelling twice is not an error |
| Now is within `cancellation_cutoff_minutes` of `starts_at`, or later | 409 `cutoff_passed` |
| Not the caller's reservation | 404 `not_found` |

### `PATCH /reservations/{reference}`

Change the time, the table or the party size. Any subset of `table_id`, `starts_at_local`,
`party_size`. No idempotency key is required here.

Validation is identical to `POST /reservations`, and the same cutoff rule as cancel applies
(409 `cutoff_passed`), measured against the **current** start time. A cancelled reservation is
409 `reservation_cancelled`. A successful amendment releases the old slot and reserves the
new one together. A failed amendment leaves the original booking and its occupancy unchanged.

`reference` and `reservation_id` survive a change.

## 9. Time and DST

Local dates and times follow the restaurant's `timezone`, including daylight-saving transitions.

**Spring forward.** Local times in the skipped hour do not exist. They never appear in
availability, and booking one is 422 `invalid_local_time`.

**Fall back.** Local times in the repeated hour occur twice. **Always resolve to the first
occurrence — the one before the clocks change.** The slot appears once in availability, and the
second occurrence is not bookable.

`reservation_duration_minutes` is **absolute time**, not wall-clock. A 90-minute reservation
starting at 01:30 on a fall-back night ends 90 real minutes later, and its local `ends_at` will
read 02:00, not 03:00.

The transitions that must be handled:

| Zone | Spring forward | Fall back |
|---|---|---|
| `Europe/Berlin` | 2026-03-29, 02:00 → 03:00 | 2026-10-25, 03:00 → 02:00 |
| `America/New_York` | 2026-03-08, 02:00 → 03:00 | 2026-11-01, 02:00 → 01:00 |

Offsets must follow the IANA rules for the specified zone and date.

## 10. Export and import

The service must support `GET /_test/export` and `POST /_test/import`. Like reset, these
are unauthenticated test endpoints.
Exports may contain credentials and session tokens; handle them as private test artifacts.
Return 200 from export with a JSON object containing `track: "tablekeeper"`,
`format_version: 1` and `state` (an implementation-defined JSON object). The state format
is opaque to the caller and must be accepted unchanged by import.

Import takes that entire object and atomically replaces the service's state, returning
204. It must accept an unchanged export produced by this service. No dependency on the
source process, files, volume, port or network address is allowed. Import is replacement,
not merge; repeating it restores the exported state without duplicating anything. Invalid
JSON follows §5; missing fields, wrong track/version or an invalid state give 422
`validation_failed` without changing the destination. Test control calls have a 10-second
timeout. Export is an atomic, read-only snapshot; subsequent source writes do not change it.

Preserve accounts and hashed-password login, existing bearer tokens, fixture configuration,
reservations, references, all completed idempotent request bodies and original responses.
Identities, statuses and timestamps must not be regenerated. Failed request keys remain
reusable. Existing receipts, references, tokens and retries must remain valid after import;
replacing the state with a fresh fixture does not satisfy this requirement. Import removes
all previous destination data and credentials. Reset continues to clear all state, including
imported state. State need not survive an abrupt container restart.

## 11. Atomic reservation moves

A diner may change several bookings in one request.

`POST /reservation-moves` requires authentication and an idempotency key. Body:

```json
{"moves": [{"reference": "BOOK01", "table_id": "t_2"},
           {"reference": "BOOK02", "table_id": "t_1"}]}
```

`moves` contains 1..8 objects with distinct string references. Invalid shape or duplicate
references gives 422 `validation_failed`. Every booking must belong to the caller and the
same restaurant. Unknown/another owner's reference gives 404 `not_found`; different
restaurants give 422 `validation_failed`. No token gives 401.

Each item accepts the ordinary PATCH fields `table_id`, `starts_at_local`, `party_size`;
omitted fields retain their current values and unknown fields are ignored. The booking's
identity, owner and creation time never change. Cancelled bookings give 409
`reservation_cancelled`. Each booking's existing cutoff applies. Non-occupancy errors use
ordinary amendment codes and take precedence in input order, with cutoff errors preceding
other changes for that booking. An overlap among resulting bookings or with an unlisted
booking gives 409 `table_unavailable`. Unchanged listed bookings retain their occupancy.

Either every move commits or nothing changes: occupancy, reservation records and retry
keys. On success return 201 with `{"reservations": [...]}` in input order, including
unchanged items.
Replays return that original response with 200, even after amendments or cancellations.
No-op moves retain all existing values. Export/import preserves successful batch receipts
as well as the resulting bookings. No batch UI is required.
