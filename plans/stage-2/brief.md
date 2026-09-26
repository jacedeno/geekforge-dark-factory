# Increment 2 brief: `stage-2/` (Tablekeeper stage 2: online booking and combined tables)

Planner: planner seat. Source: `stage-2.md` (full text in Part B). Stage-1 requirements and the
stage-1 brief decisions D1..D17 (`plans/stage-1/brief.md`, restated where they change below)
continue to apply unchanged unless a decision here says otherwise.
Directory: `stage-2/` at the repository root, nothing outside it. It starts as a copy of the
accepted `stage-1/`.

Check command (`{n}` = 2):

    cd /home/geekendzone/hackathon/dark-factory-wearedevs && .venv/bin/python -m harness run --track tablekeeper --repo {repo} --stage 2 --mode isolated --out {out} > {out}.log 2>&1; cat {out}.log; grep -q "^highest contiguous stage: 2$" {out}.log && grep -q "^claimed stage: 2" {out}.log

It passes only when the stage-1 and stage-2 suites pass and the stage-3 suite does not. Run it
under `env -u PYTHONHOME -u PYTHONPATH -u LD_LIBRARY_PATH`. The stage-2 suite drives a real
browser against the screens by `data-testid`. The shipped checks are a sample: build to Part B.

Contract: unchanged from stage 1 (`stage-2/Dockerfile`, `stage-2/RUN.md`, `-e PORT`, no runtime
network: every script, stylesheet and font is served from the image; no CDN, no web fonts from
the internet).

## Part A1. Decisions on open points

E1. UI technology: builder's choice; server-rendered or client-rendered. All assets are served
    by the same service. Screen routes `/`, `/signup`, `/login`, `/lookup` return
    `text/html; charset=utf-8`. API routes keep their JSON behaviour unchanged. Any text coming
    from data (names, labels, display names) is inserted as text, never as HTML.
E2. Session: the token from signup/login is kept in browser storage (e.g. localStorage) so it
    survives navigation between the four routes; `current-user` (display name) is shown on every
    screen while signed in; `logout-button` forgets the token. Tokens survive export/import
    (stage-1 §10), so a signed-in browser stays signed in across an upgrade.
E3. Restaurant detail: `GET /restaurants/{id}` also returns `combinable` (fixture shape, default
    `[]`), as the fixture now carries it ("in the fixture's shape", stage-1 §8).
E4. Grid: one single-table cell `slot-{table_id}-{HH:MM}` for every table of the restaurant and
    every slot, `data-available` exactly per stage-2 text. Combination cells
    `slot-{t_a}+{t_b}-{HH:MM}` (ids in `combinable` order): rendered for every declared pair whose
    summed capacity is >= the searched party size, with `data-available="true"` exactly when that
    pair is in the slot's `available_options`, else `"false"`; pairs too small for the party are
    not rendered. `HH:MM` is the local time of `starts_at_local`. Cells show human labels
    (e.g. "Table 1 + Table 2, seats 6"), not raw ids. Clicking an unavailable cell does nothing.
    Signed out, clicking an available cell shows `auth-error` (with a link to `/login`) and keeps
    the grid.
E5. Out-of-order search: each search gets a sequence number; a response is applied only if it
    belongs to the latest search started. The booking form, grid and labels always describe the
    latest search. Opening a new search closes a form that belongs to an older search.
E6. Booking attempt identity: when the form opens, the client creates an idempotency key. The key
    is kept while the request body (restaurant, table set, `starts_at_local`, party size) is
    unchanged, including after success (a resubmission replays and shows the same
    `confirmation-reference`, no `booking-error`) and after an uncertain outcome. Any field change
    makes the next submission a new request with a new key.
E7. Outcomes:
    - 201/200: show `confirmation` with `confirmation-reference`, `confirmation-details`,
      `confirmation-tables`; remove `booking-error` and `booking-uncertain`; keep the form visible.
    - Network failure, aborted/timeout connection, unreadable response, or a 5xx: "uncertain":
      show nonempty `booking-uncertain`, no `booking-error`, no new confirmation; the form stays
      unchanged; the next submit retries with the same key and body.
    - Any 4xx: a confirmed rejection: show `booking-error` (human message), no confirmation for
      this attempt; remove `booking-uncertain`. For 409 `table_unavailable` also re-run the current
      search to refresh availability while keeping the form and its inputs as they are.
    - The browser never shows a success it did not receive from the server.
E8. `booking-summary`, `confirmation-details` and lookup detail show the local start as date and
    24-hour `HH:MM` (e.g. "Thu 24 Sep 2026 · 19:00"); `confirmation-details` also names the
    restaurant and every table label; `confirmation-tables` and `reservation-tables` contain every
    table label of the reservation.
E9. Lookup (`/lookup`) uses the signed-in user's token with `GET /reservations/{reference}`
    (stage-1 §8 forbids leaking other people's bookings). Signed out or not found:
    `reservation-error`. Found: `reservation-detail`, `reservation-status` (exact text),
    `reservation-tables`, and `reservation-cancel-button` while confirmed. A refused cancel (e.g.
    409 `cutoff_passed`) shows `reservation-error`; a successful cancel updates the status to
    `cancelled` and removes the button.
E10. `table_ids` rules for `POST /reservations` and `PATCH`, in this order after stage-1 D5 a/b:
    both `table_id` and `table_ids` present -> 422 `validation_failed`; `table_ids` not an array
    of strings -> 400 `malformed_request`; empty array or a duplicate id -> 422
    `validation_failed`; more than two ids -> 422 `combination_not_allowed`; unknown table or a
    table of another restaurant -> 404 `not_found`; a two-table set not declared in `combinable`
    (either order) -> 422 `combination_not_allowed`; then stage-1 D5 d..h with the summed capacity
    and overlap on any member. A pair is stored and returned in `combinable` order, whatever the
    input order. Responses always carry `table_ids`, plus `table_id` only for a single table.
    For POST exactly one of `table_id`/`table_ids` is required (neither -> 422).
E11. Reservation moves accept `table_ids` per move under E10 (both fields in one move -> 422).
E12. Seeded reservations may carry `table_id` or `table_ids` and `status` (`confirmed` default or
    `cancelled`); other statuses -> 422 at reset. Declared pairs must name two distinct tables of
    that restaurant, else reset 422.
E13. Upgrade: `POST /_test/import` accepts exports of the stage-1 service (its state schema marker)
    and migrates them (single `table_id` -> set of one, `combinable` -> `[]`), preserving users,
    password logins, tokens, reservations, references, timestamps, idempotency receipts and
    original responses byte-for-byte as JSON values. Exports of stage 2 carry a new schema marker.
    A retry after upgrade of a lost booking returns the original stage-1 response with 200.
E14. Availability: `available_table_ids` unchanged (singles only); `available_options` as the
    text says (singles first in fixture order, then pairs in `combinable` order, capacity is the
    sum, member order in `combinable` order).
E15. Visual quality is judged: a warm hospitality design with a consistent system (type scale,
    spacing, colour tokens), clear primary actions, distinct visual states for available,
    unavailable, selected, loading, success, refused and uncertain; visible labels on inputs;
    visible keyboard focus; sufficient contrast; no horizontal scroll at 375 px and at desktop
    widths; considered empty, loading and error states; consistent navigation header on the four
    routes (links to search, lookup, login/signup or current user + logout).

## Part A2. Work orders

WORK ORDER 2.1: Start stage-2 from stage-1
Directory: stage-2/, nothing outside it
Acceptance:
  - `stage-2/` is a copy of the accepted `stage-1/` (no VCS metadata), committed on its own.
  - Stage-1 behaviour unchanged: the stage-1 suite still passes against `stage-2/`.
Out of scope: any new behaviour in that commit.

WORK ORDER 2.2: Combined tables in the API and model, upgrade import
Directory: stage-2/, nothing outside it
Acceptance:
  - Fixture `combinable`, seeded `table_ids`/`status` (E12); restaurant detail `combinable` (E3).
  - `GET /availability` `available_options` (E14); `available_table_ids` unchanged.
  - `POST /reservations` and `PATCH` with `table_ids` per E10; errors exactly as the stage-2
    table; combination occupies both tables for the full duration; cancel frees every table.
  - Reservation moves accept `table_ids` (E11); no table in overlapping resulting bookings.
  - Stage-1 exports import and keep working (E13), including idempotent replays and tokens.
  - Concurrent requests stay linearizable (serial-equivalent) and never 5xx.
Out of scope: `explain`, history, revisions, policies (stage 3).

WORK ORDER 2.3: Browser UI
Directory: stage-2/, nothing outside it
Acceptance:
  - Routes `/`, `/signup`, `/login`, `/lookup` return HTML; every `data-testid` of the stage-2
    tables exists with the stated semantics (auth, grid, booking form, confirmation, lookup,
    combination cells, `confirmation-tables`, `reservation-tables`, `booking-uncertain`).
  - E2, E4..E9 behaviours: out-of-order searches, 409 refresh with preserved form, lost-response
    uncertainty and same-key retry recovering the original reference, unchanged resubmission
    returning the same reference, changed field -> new booking, signed-out booking attempt.
  - The pending retry identity and form survive an export/import upgrade between requests.
  - E15 product and visual quality at 375 px and desktop widths.
Out of scope: background polling, live updates, cross-tab sync, recovery across page reload,
anything from stage 3.

## Part A3. Out of scope for this increment

Everything in stage-3.md and later (`explain`, history, decision, revisions, `expected_revision`,
policies, managers, series, replans). The stage-2 service must not pass the stage-3 suite.
Nothing outside `stage-2/`; `stage-1/` must not change.

## Part B. Complete specification: stage-2.md (verbatim)

# Tablekeeper — Stage 2: online booking and combined tables

The stage-1 requirements continue to apply, with the additions below. Numbered section
references such as §5 and §7 refer to `stage-1.md`.

Diners can search, book and manage reservations in a browser. Restaurants can offer
approved pairs of tables for larger parties.

The following screens must be reachable by URL. Other screens must be reachable through
the UI. Server-side and client-side rendering are both permitted.

| Route | Screen |
|---|---|
| `/` | Search and availability grid |
| `/signup` | Signup |
| `/login` | Login |
| `/lookup` | Look up a reservation by reference |

A screen route returns HTML; §3.4's `application/json` convention is about the API, and does
not govern the routes in the table above.

## Competing clients and uncertain outcomes

The UI must handle responses arriving out of order and connections failing after submission.

- If search A starts before search B but finishes after it, the grid, table labels and
  booking form must describe B. A late response must not restore A's results.
- If another client takes a table after the form opens, a `409 table_unavailable` response
  shows `booking-error` and refreshes availability. Preserve the selected form and its
  inputs so the diner can change their choice. Do not show a confirmation for that attempt.
- If a booking response is lost, including after the booking commits, show nonempty
  `booking-uncertain` text, without `booking-error` or a new confirmation. The unchanged
  form must retry with the same idempotency key and body. A successful retry removes the
  uncertainty/error elements and shows the original reference. A confirmed rejection
  uses `booking-error`.

These rules apply to combination bookings too. No background polling, live updates,
cross-tab storage synchronization, or recovery across a page reload is required. The server
remains authoritative; the browser must not manufacture a successful result from cached data.

The UI must expose the `data-testid` attributes listed below for integration testing.
Additional elements are permitted, and the visual implementation is the team's choice subject
to the product-quality requirements below.

## Product and visual direction

The browser experience must feel like a coherent, presentation-ready restaurant product, not a
test harness with controls attached. Aim for a warm, confident hospitality character. The search,
availability and booking flow should have an obvious visual hierarchy; a diner should be able to
scan dates, times, party size and table choices without having to interpret raw API data. Combined
tables should read as intentional seating options, not as concatenated technical identifiers.

Use a consistent visual system for typography, spacing, colour, controls and feedback. Primary
actions must be easy to identify. Available, unavailable, selected, loading, successful, refused
and uncertain states must be visually distinct as well as satisfying the behavioural requirements
below. Use human-readable restaurant and table labels prominently; expose technical identifiers
only where they help the user.

The required flows must remain clear and usable at a 375 CSS-pixel viewport and at conventional
desktop widths, without horizontal page scrolling. Inputs need visible labels, keyboard focus must
be apparent, and text and controls need sufficient contrast. Provide considered empty, loading and
error states, and keep navigation consistent across the required routes. A custom illustration,
brand asset or exact visual match to a reference is not required.

## Signup and login

| `data-testid` | Element |
|---|---|
| `signup-email`, `signup-password`, `signup-display-name` | Inputs |
| `signup-submit` | Button |
| `login-email`, `login-password`, `login-submit` | Inputs and button |
| `auth-error` | Error message. Present only when there is one |
| `current-user` | Visible on every screen when signed in. Text contains the display name |
| `logout-button` | Button |

## Search and availability grid — `/`

| `data-testid` | Element |
|---|---|
| `restaurant-select` | Selects a restaurant. Option values are restaurant ids |
| `date-input` | Date, value `YYYY-MM-DD` |
| `party-size-input` | Number |
| `search-button` | Runs the search |
| `availability-grid` | Container for the results |
| `slot-{table_id}-{HH:MM}` | One cell per table per slot, e.g. `slot-t_2-19:00` |
| `no-slots` | Shown instead of the grid when the day has no slots |

Each cell carries `data-available="true"` or `data-available="false"`. A cell is `true` exactly
when its `table_id` is in that slot's `available_table_ids` from `GET /availability` for the party
size that was searched, and `false` otherwise. Clicking an available cell
opens the booking form for that table and slot. Clicking an unavailable cell does nothing.
Booking requires a signed-in user: clicking an available cell while signed out shows `auth-error`
or navigates to `/login`, your choice.

## Booking form

| `data-testid` | Element |
|---|---|
| `booking-form` | Container |
| `booking-summary` | Text contains the table label and the local start time |
| `booking-party-size` | Number input, pre-filled from the search |
| `booking-submit` | Button |
| `booking-error` | Error message, when the booking fails |

Keep the booking form on screen after success. Submitting it again without changing a
field must return the same `confirmation-reference`, without `booking-error` or another
booking. Changing a field makes the next submission a new booking request. Retries follow §7.

## Confirmation

Shown after a successful booking.

| `data-testid` | Element |
|---|---|
| `confirmation` | Container |
| `confirmation-reference` | Text is exactly the reference, no surrounding words |
| `confirmation-details` | Text contains the restaurant name, table label and local start time |

## Lookup — `/lookup`

| `data-testid` | Element |
|---|---|
| `lookup-reference-input`, `lookup-submit` | Input and button |
| `reservation-detail` | Container, shown when found |
| `reservation-status` | Text is exactly `confirmed` or `cancelled` |
| `reservation-cancel-button` | Cancels. Absent once cancelled |
| `reservation-error` | Shown when not found, or when a cancel is refused |

## Existing clients after an upgrade

A stage-2 service must accept an export produced by the same team's stage-1 service. A
browser signed in before that export/import upgrade must remain signed in afterwards.
A retained booking reference still works through the lookup screen. A booking whose response
was lost before export remains retryable after import with the same body and key; the UI
must recover the original confirmation. These requirements apply when import completes
between browser requests; migration during an in-flight request is not required. No page
reload or new screen is required. The form and pending retry identity must survive the upgrade.

## Combined tables

A party may book two tables that the restaurant has declared combinable. The booking
occupies both tables for its full duration.
Existing single-table request formats remain supported.

## Model

The restaurant fixture gains one field:

```json
{
  "id": "r_anker",
  "combinable": [ ["t_1", "t_2"], ["t_2", "t_3"] ],
  ...
}
```

Each entry is an unordered pair of table ids in that restaurant. **Pairs only** — never three or
more. A pair not listed cannot be combined, whatever the table sizes are. Combining is not
transitive: `[t_1,t_2]` and `[t_2,t_3]` do not make `{t_1,t_3}` bookable.

A combination's capacity is the sum of its tables' capacities.

Seeded `reservations` are `confirmed` unless they carry a `status` of `cancelled`, and may hold
either `table_id` or `table_ids`.

## API

### `GET /availability`

Slots gain `available_options`. `available_table_ids` stays exactly as it was — single tables
only.

```json
{
  "slots": [
    {
      "starts_at_local": "2026-09-24T19:00",
      "starts_at": "2026-09-24T19:00:00+02:00",
      "available_table_ids": ["t_3"],
      "available_options": [
        { "table_ids": ["t_3"], "capacity": 4 },
        { "table_ids": ["t_1", "t_2"], "capacity": 6 }
      ]
    }
  ]
}
```

`available_options` lists every single table and every declared pair with
`capacity >= party_size` and no overlapping confirmed reservation on any member. Singles first in
fixture order, then pairs in `combinable` order. `table_ids` within a pair is in `combinable`
order.

### `POST /reservations`

The body takes `table_ids` instead of `table_id`:

```json
{ "restaurant_id": "r_anker", "table_ids": ["t_1", "t_2"],
  "starts_at_local": "2026-09-24T19:00", "party_size": 6 }
```

`table_id` is still accepted and means a set of one. Sending both is 422 `validation_failed`.

Responses always carry `table_ids`. They also carry `table_id` **when the set has exactly one
member**, and omit it otherwise.

| Case | Response |
|---|---|
| The pair is not in `combinable` | 422 `combination_not_allowed` |
| More than two tables | 422 `combination_not_allowed` |
| Any table in the set is taken for an overlapping interval | 409 `table_unavailable` |
| `party_size` exceeds the combination's summed capacity | 422 `party_exceeds_capacity` |
| Duplicate table id in the set | 422 `validation_failed` |

`PATCH /reservations/{reference}` accepts `table_ids` under the same rules. Cancelling frees every
table in the set.

## UI

The availability grid gains combination cells, shown when a declared pair is available for the
searched party size:

| `data-testid` | Element |
|---|---|
| `slot-{t_a}+{t_b}-{HH:MM}` | A combination cell, e.g. `slot-t_1+t_2-19:00`. Ids in `combinable` order. Carries `data-available` like a single cell |
| `confirmation-tables` | Text contains every table label in the reservation |
| `reservation-tables` | On the lookup screen. Same |

`booking-summary` must name every table in the selection. A single-table booking's cell testid,
confirmation and lookup are unchanged.

Atomic reservation moves from stage 1 also accept `table_ids` per move. No table may
belong to overlapping resulting bookings. The existing browser recovery and original-receipt
requirements also apply to combined-table bookings.

## Concurrent bookings and amendments

Concurrent requests must produce the same results as executing them one at a time in some
order, and the requirements above hold at every read.
