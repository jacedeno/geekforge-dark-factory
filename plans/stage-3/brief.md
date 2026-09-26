# Increment 3 brief: `stage-3/` (Tablekeeper stage 3: policies, history, recurring reservations)

Planner: planner seat. Source: `stage-3.md` (full text in Part B). Stage-1 and stage-2
requirements and the decisions in `plans/stage-1/brief.md` (D1..D17) and `plans/stage-2/brief.md`
(E1..E15) continue to apply unless a decision here says otherwise.
Directory: `stage-3/` at the repository root, nothing outside it. It starts as a copy of the
accepted `stage-2/`.

Check command (`{n}` = 3):

    cd /home/geekendzone/hackathon/dark-factory-wearedevs && .venv/bin/python -m harness run --track tablekeeper --repo {repo} --stage 3 --mode isolated --out {out} > {out}.log 2>&1; cat {out}.log; grep -q "^highest contiguous stage: 3$" {out}.log && grep -q "^claimed stage: 3" {out}.log

It passes only when the stage-1..3 suites pass and the stage-4 suite does not. Run it under
`env -u PYTHONHOME -u PYTHONPATH -u LD_LIBRARY_PATH`. The shipped checks are a sample: build to
Part B.

Contract: unchanged (`stage-3/Dockerfile`, `stage-3/RUN.md`, `-e PORT`, no runtime network).

## Part A1. Decisions on open points

F1. `explain`: absent -> stage-1 shape (no `explain` key). Exactly `true` -> every slot carries
    `explain`. Any other value (`false`, `1`, `True`, empty) -> 422 `validation_failed`.
    Each entry's `policy_version` is the policy selected for the searched `date`.
F2. Policy selection (one function used everywhere): for a local date D, the published policy
    with the greatest `effective_from <= D`, ties -> greatest `policy_version`; if none, policy 0
    (the fixture rules: its slot grid, duration, cutoff, opening hours and table capacities).
    Availability (slots, grid, duration, opening hours, capacity rule, `available_options`
    capacities) uses the policy of the `date` parameter. Bookings and amendments use the policy of
    the resulting local start date.
F3. `POST /restaurants/{id}/policies` order: 401 no/invalid token -> 404 unknown restaurant ->
    403 `forbidden` non-manager -> 400 `missing_idempotency_key` -> 422 key > 255 -> 400
    unparseable/non-object body -> idempotency resolution (scope: user, key, method, path) ->
    policy validation. Every invalid policy field, including a wrong JSON type (e.g. a string or
    boolean where an integer is required), is 422 `validation_failed` (the policy section's own
    rule takes precedence over stage-1 §5's 400 for types). `opening_hours` may be an empty array
    (closed every day); entries follow stage-1 rules (`weekday` in mon..sun, `HH:MM`,
    `closes > opens`), no duplicate weekday. `capacities` must have exactly the restaurant's table
    ids as keys. Response 201: the six policy fields as supplied (normalised JSON values) plus
    `policy_version`; unknown fields are not echoed. `GET /restaurants/{id}/policies` (public,
    404 unknown restaurant) returns `{"policies":[...]}` with those objects in publication order.
F4. `manager_user_ids` (fixture, default `[]`) is not exposed by restaurant detail, which keeps
    returning the original fixture configuration.
F5. Restaurant revision: keep an internal per-restaurant counter from 0 after reset, incremented
    once per successful new booking, real amendment, cancellation, policy publication, series
    adoption and moves batch (once per batch per restaurant). Not exposed in stage 3.
F6. Reservation responses gain `revision` and `accepted_terms` (F2 policy of the start date at the
    time of creation/real amendment, keys: `policy_version`, `slot_minutes`,
    `reservation_duration_minutes`, `cancellation_cutoff_minutes`, `opening_hours`, `capacities`).
    `ends_at` = start + accepted duration. Stored idempotent responses are never rewritten.
F7. Amendment order (PATCH): 401 -> 400 body -> 404 -> `expected_revision` present and not a
    positive integer (booleans invalid) -> 422 -> mismatch -> 409 `stale_revision` -> 409
    `reservation_cancelled` -> no-op test -> 409 `cutoff_passed` (accepted cutoff vs current start)
    -> stage-1/2 validation (D5, E10) against the F2 policy of the resulting start date -> 409
    `table_unavailable`. A no-op (every supplied field equals the current value; a table set is
    compared as a set) still requires a confirmed booking outside the accepted cutoff, returns 200
    with the unchanged booking, no revision, no history. A real change replaces terms and end
    time and increments revision once. Cancel: accepted cutoff; revision +1 once; history entry.
    With a single global lock, two concurrent PATCHes with the same `expected_revision` cannot
    both make a real change.
F8. History (`GET /reservations/{reference}/history`) and decision
    (`GET /reservations/{reference}/decision`): owner only; unknown, another user's, and no or
    invalid token all -> 404 `not_found`. History `at` is rendered in the restaurant's offset
    (as the example), non-decreasing with `seq`; every entry carries `revision` and full
    `accepted_terms` as they were after that event. Changes follow the text (field order
    `table_id`/`table_ids`, `starts_at_local`, `party_size`; pairs use `table_ids` with complete
    lists in `combinable` order; single-to-single uses `table_id`). Seeded and imported
    pre-stage-3 bookings: revision 1, policy-0 terms, history synthesised as one `created` entry
    at `created_at` (plus a `cancelled` entry with revision 1 if the booking is cancelled).
F9. Series `POST /series` order: 401 -> 400/422 idempotency key -> 400 body -> idempotency
    resolution -> field validation (`anchor_reference` string, `count` int 2..12,
    `interval_weeks` int 1..4, booleans invalid; else 422) -> 404 anchor unknown/other owner ->
    409 `reservation_cancelled` -> 409 `already_in_series` (anchor is any occurrence of any series)
    -> 409 `cutoff_passed` (anchor's accepted cutoff) -> occurrences 1..count-1 in index order,
    each fully checked (invalid_local_time, opening hours, grid, capacity under its date's policy,
    then occupancy `table_unavailable` against all bookings and earlier generated occurrences);
    the first failure is returned and nothing is created. Success 201; occurrence 0 is the
    untouched anchor; generated occurrences are ordinary new reservations of the caller (revision
    1, `created` history, their own terms). Series revision starts at 1.
F10. Series revision: +1 per real individual PATCH of an occurrence (marks `exception: true`
    permanently), +1 per cancellation of an occurrence (no exception), +1 once per moves batch
    that really changes one or more of its occurrences (each changed occurrence marked exception).
    No-ops, failures and replays change nothing. `GET /series/{id}`: owner only, otherwise
    (including no token) 404.
F11. Moves (stage-1 D16 extended): per item, after 404 and restaurant check: `expected_revision`
    invalid -> 422, mismatch -> 409 `stale_revision`, then cancelled, then accepted cutoff, then
    validation against the resulting date's policy; occupancy last. Each really changed booking:
    revision +1 and one `changed` history entry; no-ops unchanged.
F12. Export/import: stage-3 exports carry a new schema marker and all new state (policies,
    managers, revisions, terms, histories, series, restaurant counters, receipts). Import accepts
    stage-1 and stage-2 exports and migrates them per F8; series adoption works on imported
    bookings; tokens, receipts and retries stay valid.

## Part A2. Work orders

WORK ORDER 3.1: Start stage-3 from stage-2
Directory: stage-3/, nothing outside it
Acceptance:
  - `stage-3/` is a copy of the accepted `stage-2/` (no VCS metadata), committed on its own;
    stage-1 and stage-2 suites still pass against it.
Out of scope: any new behaviour in that commit.

WORK ORDER 3.2: Policies, accepted terms, revisions and explanations
Directory: stage-3/, nothing outside it
Acceptance:
  - Managers, publication, listing, validation, versioning and selection per the text and
    F2..F6; failed writes and replays allocate no version.
  - Availability and booking decisions use the selected policy; `explain=true` per the text and
    F1 (all four numbered rules).
  - Every reservation response carries `revision` and `accepted_terms`; amendment, no-op, cancel,
    `expected_revision` and `stale_revision` semantics per the text and F7.
Out of scope: replans, closures, series amendment (stage 4).

WORK ORDER 3.3: Reservation history and decision
Directory: stage-3/, nothing outside it
Acceptance:
  - History and decision endpoints per the text and F8, including the five numbered history
    rules, combined-table history, owner-only 404 without authentication.
  - Moves under policies per the text and F11 (revisions, history, series effects).
Out of scope: `reassigned` history entries (stage 4).

WORK ORDER 3.4: Recurring reservations
Directory: stage-3/, nothing outside it
Acceptance:
  - `POST /series` and `GET /series/{id}` per the text, F9 and F10: all-or-nothing adoption,
    per-occurrence policy selection, DST rules, exception and revision rules, replays.
Out of scope: `POST /series/{id}/amend` (stage 4).

WORK ORDER 3.5: Upgrade path
Directory: stage-3/, nothing outside it
Acceptance:
  - Imports of stage-1 and stage-2 exports work (F12), including tokens, idempotent replays,
    lookup by reference, history/decision of imported bookings and series adoption on them.
  - Stage-3 export/import round trip preserves every new piece of state.
Out of scope: stage-4 migration.

## Part A3. Out of scope for this increment

Everything in stage-4.md (replans, closures, plan application, `restaurant_revision` in
responses, `planning_limit`, `no_feasible_plan`, `stale_plan`, `plan_already_applied`,
`reassigned` history, `POST /series/{id}/amend`). The stage-3 service must not pass the stage-4
suite. No new screens. Nothing outside `stage-3/`.

## Part B. Complete specification: stage-3.md (verbatim)

# Tablekeeper — Stage 3: booking policies, history and recurring reservations

The requirements from stages 1 and 2 continue to apply, with the additions below.
Numbered section references such as §5 and §7 refer to `stage-1.md`.

Restaurants can publish dated booking policies. Diners can see why a table is unavailable,
view their reservation history and arrange recurring bookings.

## Availability explanations

Whether a table is available for a slot is decided by two rules, each independent of the other:

| Rule | Holds when |
|---|---|
| `capacity` | `party_size` is at most the table's `capacity` |
| `no_overlap` | no confirmed reservation on that table overlaps the slot's interval |

A table is available exactly when both hold. `available_table_ids` is unchanged in meaning.

```http
GET /availability?restaurant_id=r_anker&date=2026-09-24&party_size=4&explain=true
```

`explain` is optional. Its only accepted value is `true`; any other value, including `false`,
`1` and the empty string, is 422 `validation_failed`. **Without it the response keeps stage
1's shape** — no explanation fields appear. Published policies can change the slot values.

With it, every slot carries one further field:

```json
{ "starts_at_local": "2026-09-24T18:00",
  "starts_at": "2026-09-24T18:00:00+02:00",
  "available_table_ids": ["t_2"],
  "explain": [
    { "table_id": "t_1", "policy_version": 0, "available": false,
      "rules": [ { "rule": "capacity", "holds": false },
                 { "rule": "no_overlap", "holds": true } ] },
    { "table_id": "t_2", "policy_version": 0, "available": true,
      "rules": [ { "rule": "capacity", "holds": true },
                 { "rule": "no_overlap", "holds": true } ] }
  ] }
```

1. **Every table of the restaurant appears exactly once**, available or not, in fixture order —
   the same order `available_table_ids` uses.
2. **Both rules are reported for every table**, in the order above. A rule that holds is
   reported holding; a table excluded by both reports both false. No rule may be omitted.
3. **`available` is true exactly when both rules hold**, and the `table_id`s whose `available`
   is true are exactly `available_table_ids`, in the same order.
4. A closed day still returns `"slots": []`, and a slot with no available table still appears —
   now with a full `explain` for every table.

## Reservation history

```http
GET /reservations/{reference}/history
```

The reservation's own record, oldest first. Only its owner may read it; anyone else, signed in
or not, gets the same 404 `not_found` that §8 gives for a reservation that is not theirs. A
cancelled reservation still has its history.

The example below shows the event fields; every entry also carries `revision` and
`accepted_terms` as specified under “Policies and accepted terms”.

```json
{ "reference": "ABC12345",
  "entries": [
    { "seq": 1, "at": "2026-09-17T12:00:00+02:00", "event": "created",
      "changes": [ { "field": "table_id", "from": null, "to": "t_2" },
                   { "field": "starts_at_local", "from": null, "to": "2026-09-24T19:00" },
                   { "field": "party_size", "from": null, "to": 4 } ] },
    { "seq": 2, "at": "2026-09-17T12:05:00+02:00", "event": "changed",
      "changes": [ { "field": "table_id", "from": "t_2", "to": "t_3" } ] },
    { "seq": 3, "at": "2026-09-17T12:09:00+02:00", "event": "cancelled", "changes": [] } ]
}
```

1. **`seq` starts at 1 and increases by exactly 1**, so the order is total even when two writes
   land in the same second. Entries are returned in `seq` order, which is also `at` order.
2. **`created` names all three fields**, each with `"from": null`.
3. **`changed` names only the fields that actually changed**, in the order `table_id`,
   `starts_at_local`, `party_size`. A `PATCH` that sets a field to the value it already has
   changed nothing: it still succeeds, and it records **no entry at all**.
4. **`cancelled` carries an empty `changes`**, and nothing follows it.
5. Replaying an idempotent `POST /reservations` records nothing — a replay returns the original
   response and does not re-run the operation (§7).

## Existing screens

No new screens are required for explanations or history. The availability grid continues
to follow the stage-2 rules.

## Policies and accepted terms

Restaurants may now declare `manager_user_ids` in their reset fixture (default `[]`). Only
these users may publish policies. Unknown restaurant is 404;
an authenticated non-manager is 403 `forbidden`; no token is 401. This extends stage 1's
minimal permissions; managers do not gain access to other diners' private lookup/history.

`POST /restaurants/{id}/policies` requires an idempotency key, with stage 1's replay rules.
It accepts a **complete policy**, not a patch:

```json
{
  "effective_from": "2026-09-28",
  "slot_minutes": 30,
  "reservation_duration_minutes": 120,
  "cancellation_cutoff_minutes": 60,
  "opening_hours": [{"weekday": "mon", "opens": "18:00", "closes": "23:00"}],
  "capacities": {"t_1": 2, "t_2": 4, "t_3": 6}
}
```

Returns 201 with the supplied policy plus `policy_version`, an integer starting at 1 and
increasing by one per restaurant. Failed writes and replays allocate no version. Policy 0
is the original fixture's rules and applies before any published policy. Policies are
immutable. Publication order may differ from effective-date order. For a booking's **local
start date**, choose the greatest `effective_from` not later than that date; ties choose
the greatest `policy_version`. A new same-date policy supersedes the old one for future
decisions, without changing any accepted reservation. Effective dates may be in the past;
publication never retroactively edits a booking.

All fields above are required. `effective_from` is an actual `YYYY-MM-DD` date; grid and
duration are integers 1..1440; cutoff is an integer 0..10080; booleans are not integers.
Opening hours follow stage 1 and contain no duplicate weekdays. `capacities` names **exactly**
the restaurant's table ids with integer capacities 1..100. Invalid policy is 422
`validation_failed`, with no version or state change. Table ids, labels, timezone and
declared combinations cannot be changed by a policy. Unknown fields are ignored.

`GET /restaurants/{id}/policies` is public and returns `{"policies": [...]}` in publication
order, omitting policy 0. The ordinary restaurant detail still returns its original fixture
configuration. Availability and booking decisions use the selected policy, not that detail.
With `explain=true`, each table explanation additionally identifies its `policy_version`.

Every reservation response gains `revision` (1 at creation) and `accepted_terms`:

```json
{"policy_version": 0, "slot_minutes": 30,
 "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120,
 "opening_hours": [{"weekday": "mon", "opens": "18:00", "closes": "23:00"}],
 "capacities": {"t_1": 2, "t_2": 4, "t_3": 6}}
```

These are a snapshot of the entire selected policy, excluding `effective_from`. Seeded
bookings start at revision 1 under policy 0. Responses to old idempotency keys remain the
original response, including the original revision and terms.

- A policy publication does not change existing bookings, their end times, or their history.
- Cancel checks the accepted cutoff, against the current start.
- A real diner amendment (time, tables or party size) checks the old accepted cutoff first,
  then validates **all** resulting fields against the policy applicable to the resulting start
  date. It atomically replaces accepted terms and end time and increments revision once.
- A no-op amendment retains terms, end time and revision and records no history. It still
  requires a confirmed, editable booking.
- Failed amendments change nothing. Cancel increments revision once; repeated cancel does not.
- `PATCH` optionally accepts `expected_revision`. A positive integer differing from the current
  revision gives 409 `stale_revision` before cutoff/validation; invalid type/range gives 422.
  Omission retains stage 1 semantics. Two concurrent amendments using one revision: at most one
  real change succeeds. Unrelated unknown fields remain ignored.

Each history entry additionally carries the reservation's resulting `revision` and complete
`accepted_terms`. Old entries never acquire newer terms. `GET /reservations/{reference}/decision`
returns `{"reference": "...", "revision": 1, "accepted_terms": {...}}` for the current booking,
including after cancellation, with history's owner-only 404 rule. History and decision return
404 even without authentication, resolving the exception to stage 1's general 401 rule.

## Recurring reservations

`POST /series` adopts an existing reservation as occurrence zero of a recurring agreement.
An idempotency key is required. Body:

```json
{"anchor_reference": "ABC12345", "count": 8, "interval_weeks": 1}
```

The anchor must belong to the caller, be confirmed and satisfy its accepted cancellation
cutoff. Unknown or another owner's anchor gives 404 `not_found`; cancelled gives 409
`reservation_cancelled`; already adopted gives 409 `already_in_series`. `count` is an integer
2..12 including the anchor; `interval_weeks` is an integer 1..4. Invalid values, including
booleans, give 422 `validation_failed`. No token gives 401.

Occurrence zero is the anchor itself: its reference, identity, revision, terms, history,
timestamps and original idempotent response remain unchanged. Occurrence i starts on the
anchor's local calendar date plus i × interval_weeks × 7 days, at the same local clock time.
Each generated occurrence independently selects its date's policy, including duration and
capacity, and obeys ordinary opening, DST and occupancy rules. A nonexistent local time
rejects the entire adoption with `invalid_local_time`; repeated times use stage 1's first
occurrence rule. Generated occurrences use the anchor's party size and table selection.
No partial series, reservations, histories, counters or idempotency claim survive failure.
The first failing occurrence in index order determines the ordinary booking error.

Return 201:

```json
{"series_id": "opaque", "revision": 1, "interval_weeks": 1,
 "occurrences": [{"index": 0, "reference": "ABC12345", "exception": false,
                  "reservation": {"...": "ordinary reservation response"}}]}
```

The array includes all count occurrences in index order. Each has a distinct ordinary
reservation reference; references and indices never change when dates or tables change.
Occurrences appear in ordinary reservation lists, occupy tables, and have ordinary histories.
`GET /series/{series_id}` returns this shape with current reservation states. Only the owner
may read it: another user or no token gives 404 `not_found`.

A real individual PATCH permanently marks that occurrence as `exception: true` and increments
the series revision once; a no-op or failure changes neither. Cancellation increments the
series revision once, retaining the cancelled occurrence, but does not mark it as an
exception; repeated cancel does nothing.
Cancelling the anchor does not cancel its siblings. Ordinary cutoff and revision checks still
apply. Adoption increments the restaurant revision once for the whole operation. Replays
return the original series response, even after later changes, and change no counter.
Series creation adds one idempotent write path. Unknown fields are ignored.

A stage-3 service must accept exports produced by the same team's stage-1 or stage-2
service. Adoption must work on reservations imported this way. Existing confirmation links,
sessions and original booking retries remain valid.

## Combined-table history

Stage 3's accepted terms apply to combinations too; capacity is the sum of the **selected
policy's** capacities. In history, retain stage-3 fields for single-to-single operations.
For a creation of a pair, replace the `table_id` change by `table_ids` (from null to the pair).
For a change involving a pair, use `table_ids` (complete before/after lists) instead of
`table_id`. Table-set order is the declared combination order. A reversed input pair names
the same set and is not an amendment on its own. Policy selection, revision and replay rules
are unchanged.

## Collective moves under policies and agreements

Each real change in `POST /reservation-moves` uses individual PATCH semantics: check the
old accepted cutoff, then adopt the resulting date's policy. Per-move `expected_revision`
is optional and follows PATCH validation and stale-revision rules. A no-op retains its
terms and history. All resulting bookings must satisfy amendment and occupancy rules;
failure leaves every booking unchanged. Every changed booking gains one revision and
changed history entry; the restaurant revision increases once for the whole batch.
Each affected series revision increases once,
and each changed series occurrence becomes a permanent diner exception. A
failed batch or replay changes no revisions, histories or exception flags.
