# Increment 4 brief: `stage-4/` (Tablekeeper stage 4: seating changes and recurring amendments)

Planner: planner seat. Source: `stage-4.md` (full text in Part B). All earlier requirements and
decisions (`plans/stage-1/brief.md` D1..D17, `plans/stage-2/brief.md` E1..E15,
`plans/stage-3/brief.md` F1..F12) continue to apply unless a decision here says otherwise.
Directory: `stage-4/` at the repository root, nothing outside it. It starts as a copy of the
accepted `stage-3/`.

Check command (`{n}` = 4):

    cd /home/geekendzone/hackathon/dark-factory-wearedevs && .venv/bin/python -m harness run --track tablekeeper --repo {repo} --stage 4 --mode isolated --out {out} > {out}.log 2>&1; cat {out}.log; grep -q "^highest contiguous stage: 4$" {out}.log && grep -q "^claimed stage: 4" {out}.log

It passes only when the stage-1..4 suites pass. Run it under
`env -u PYTHONHOME -u PYTHONPATH -u LD_LIBRARY_PATH`. The shipped checks are a sample: build to
Part B.

Contract: unchanged (`stage-4/Dockerfile`, `stage-4/RUN.md`, `-e PORT`, no runtime network).

## Part A1. Decisions on open points

G1. Restaurant revision (F5, now exposed): 0 after reset; +1 for each successful new booking
    (including each series adoption as a whole and each moves batch as a whole), real amendment,
    cancellation, policy publication, series amendment that changed something, and plan
    application. No-ops, failures, previews and replays do not change it. Imported state keeps
    its counter; stage-1..3 exports without a counter start at 0.
G2. `POST /restaurants/{id}/replans` order: 401 -> 404 unknown restaurant -> 403 non-manager ->
    idempotency key (400/422) -> 400 unparseable/non-object body -> idempotency resolution ->
    422 `validation_failed` (`table_id` missing or not a string; `from`/`to` missing, not
    strings, or not RFC 3339 date-times with an explicit offset (`Z` or `+HH:MM`); `from >= to`;
    wrong types are 422 here, as the interval rules say "invalid interval is 422") -> 404
    table not in this restaurant -> 422 `planning_limit` when the restaurant has more than 6
    tables or more than 4 declared pairs, or more than 6 bookings are considered -> planning ->
    409 `no_feasible_plan` (nothing stored) -> 201.
G3. Considered bookings: every confirmed booking of this restaurant whose occupancy
    `[starts_at, ends_at)` overlaps `[from, to)`, on any table (not only the closed one), as the
    text says. All other confirmed bookings are fixed. Candidate options for a considered booking:
    each single table and each declared pair whose capacity under that booking's own
    `accepted_terms.capacities` is >= its party size, that does not contain the closed table,
    and that does not overlap (during the booking's own interval) a fixed booking, an applied
    closure, or another considered booking's assignment. Cutoffs are ignored.
G4. Objective, lexicographic: (1) number of bookings whose table set differs from the current one
    (set comparison); (2) total unused seats = sum of (option capacity under own terms - party
    size); (3) the vector of option ranks ordered by reservation reference ascending (plain string
    code-point order), compared lexicographically; rank = index among singles in fixture order,
    then pairs in `combinable` order (after the singles). The optimum is unique under (3). Search
    must be exact (e.g. branch and bound) and fast at the limits (6 tables, 4 pairs, 6 bookings).
G5. Preview response: `plan_id` (opaque), `restaurant_revision` (current value, unchanged),
    `closure` echoing `table_id`, `from`, `to` as supplied, `assignments` for every considered
    booking in reference order (`table_ids` in fixture/`combinable` order, `changed`),
    `moved_count`, `unused_seats`. With no considered booking the plan is empty and feasible.
    The plan records the restaurant revision it was computed at.
G6. `POST /restaurants/{id}/replans/{plan_id}/apply` order: 401 -> 404 restaurant -> 403 ->
    idempotency key -> body must be a JSON object (400 otherwise) -> idempotency resolution
    (replay of the successful key -> 200 original) -> 404 unknown plan or a plan of another
    restaurant -> 409 `plan_already_applied` (applied under another key) -> 409 `stale_plan`
    (restaurant revision differs from the plan's) -> apply atomically -> 201.
G7. Application: records the closure; each moved booking gets its new table set, revision +1,
    one history entry `{"event": "reassigned", "plan_id": ..., "changes": [{"field":
    "table_ids", "from": [...], "to": [...]}], "revision", "accepted_terms", "seq", "at"}`;
    accepted terms, times, owner, party size unchanged; unmoved bookings unchanged; restaurant
    revision +1 once; each series with at least one moved member +1 once; exception flags
    unchanged. Response `reservations`: every considered booking's current reservation response
    in reference order.
G8. Closures: an applied closure makes its table (and every pair containing it) unavailable for
    any occupancy overlapping `[from, to)`: availability, `available_options`, explanations
    (`no_overlap` false), creates, amendments, moves, series adoption and series amendment
    (409 `table_unavailable`), and later plans.
G9. `POST /series/{series_id}/amend` order: 401 -> idempotency key -> 400 body -> idempotency
    resolution -> 404 unknown/other owner -> 422 validation (`expected_revision` positive integer,
    `from_index` integer 0..count-1, `local_time` exactly `HH:MM` 00:00..23:59; booleans invalid)
    -> 409 `stale_revision` (series revision) -> eligible occurrences = index >= from_index, not
    cancelled, not exception; for each in index order: resulting start = its original scheduled
    local date (anchor date + index x interval_weeks x 7 days) at `local_time`; identical result ->
    no-op; otherwise accepted-cutoff check (409 `cutoff_passed`) then validation against the policy
    of the resulting date (`invalid_local_time`, `outside_opening_hours`, `not_on_slot_grid`,
    `party_exceeds_capacity`); the first non-occupancy error in index order wins; then occupancy of
    all resulting occurrences together against every other booking, unchanged occurrences and
    applied closures -> 409 `table_unavailable`. Success 201 with the current series response;
    changed occurrences: revision +1, one `changed` history entry, new terms/end; series and
    restaurant revision +1 once if anything changed; no exception marks. All-no-op or no eligible
    occurrence -> 201, nothing changes.
G10. Export/import: stage-4 exports carry a new schema marker plus closures, plans (applied or
    not, with their revision), restaurant revisions and receipts. Import accepts stage-1, -2 and
    -3 exports; replans and series amendment work on imported state, including moved and
    cancelled occurrences.

## Part A2. Work orders

WORK ORDER 4.1: Start stage-4 from stage-3
Directory: stage-4/, nothing outside it
Acceptance:
  - `stage-4/` is a copy of the accepted `stage-3/` (no VCS metadata), committed on its own;
    stage-1..3 suites still pass against it.
Out of scope: any new behaviour in that commit.

WORK ORDER 4.2: Restaurant revision and seating-plan preview
Directory: stage-4/, nothing outside it
Acceptance:
  - Restaurant revision per the text and G1.
  - Replans preview per the text and G2..G5: permissions, validation, considered set, exact
    lexicographic optimum, `planning_limit`, `no_feasible_plan`, preview changes nothing but the
    stored plan, idempotent replay.
Out of scope: series amendment.

WORK ORDER 4.3: Plan application and closures
Directory: stage-4/, nothing outside it
Acceptance:
  - Apply per the text, G6 and G7: atomic, `stale_plan`, `plan_already_applied`, replay 200,
    reassigned history, revisions, series effects; concurrent applications never partially move.
  - Closures per G8 everywhere occupancy is decided; a closure at another restaurant does not
    invalidate a plan; confirmation and lookup screens show the new tables after an applied plan.
Out of scope: UI for managers (no new screens).

WORK ORDER 4.4: Recurring amendments
Directory: stage-4/, nothing outside it
Acceptance:
  - `POST /series/{id}/amend` per the text and G9, including concurrent amendments from the same
    expected revision (at most one real change), replays after later edits.
Out of scope: anything not in stage-4.md.

WORK ORDER 4.5: Upgrade path
Directory: stage-4/, nothing outside it
Acceptance:
  - Imports of stage-1, -2 and -3 exports work (G10); earlier receipts, histories, retries and
    sessions remain valid; replans and series amendment work on imported series including moved
    and cancelled occurrences; stage-4 round trip preserves closures and plans.
Out of scope: none beyond the specification.

## Part A3. Out of scope for this increment

Anything not in stage-1..4 specifications; no new screens. Nothing outside `stage-4/`.

## Part B. Complete specification: stage-4.md (verbatim)

# Tablekeeper — Stage 4: seating changes and recurring amendments

Extends all earlier stages, including stage-1 atomic reservation moves, stage-2 table
combinations and stage-3 policies and recurring agreements. All earlier requirements apply.

## Seating changes after a table closure

When a table becomes unavailable, a manager can review a proposed seating arrangement
before applying it. Customers must keep their booking times, party sizes and accepted terms.
No new screens are required. Existing availability, confirmation and lookup screens must
reflect an applied plan.

`POST /restaurants/{id}/replans` requires a manager and an idempotency key. Body:

```json
{"table_id": "t_2", "from": "2026-09-28T18:00:00+02:00",
 "to": "2026-09-28T23:00:00+02:00"}
```

The instants have explicit offsets and `from < to`; invalid interval is 422
`validation_failed`, unknown table 404. The proposed closure is the half-open interval
`[from,to)`. Consider every confirmed booking at this restaurant overlapping that interval.
Other bookings retain their assignments.
Planning must support up to 6 tables, 4 declared pairs and 6 considered bookings; larger
inputs may return 422 `planning_limit`. Each considered booking must retain its reference,
owner, party size, start, end and accepted terms. Assign it a single or a declared pair with
enough capacity under **its own accepted terms**, without conflicts with fixed bookings,
other assignments, previously applied closures or the proposed closure. Diners' cancellation
cutoffs do not prevent an operator repair. No booking may disappear or be cancelled.

Among feasible plans minimize, in order:

1. Number of bookings whose table set changes.
2. Total unused seats across all considered bookings (capacity minus party size).
3. The vector of option ranks in ascending reservation-reference order. Singles are ranked
   first in fixture order, then pairs in declared order, starting at 0.

Returns 201:

```json
{"plan_id": "opaque", "restaurant_revision": 4,
 "closure": {"table_id": "t_2", "from": "...", "to": "..."},
 "assignments": [{"reference": "ABC12345", "table_ids": ["t_1"], "changed": true}],
 "moved_count": 1, "unused_seats": 0}
```

Assignments include every considered booking in reference order. A restaurant revision starts
at 0 after reset and increments once for each successful new booking, real amendment,
cancellation, policy publication or plan application. No-op writes, failures, previews and
replays do not increment it. Preview stores only a plan: no closure, occupancy, reservation
revision or history changes. No feasible plan gives 409 `no_feasible_plan`, changing nothing.

`POST /restaurants/{id}/replans/{plan_id}/apply`, body `{}`, requires a manager and an
idempotency key. Return 201 with `{"plan_id": "...", "restaurant_revision": 5,
"reservations": [...]}`; reservations include every considered booking in reference order.
Unknown plan or one from another restaurant is 404. Any intervening restaurant revision
invalidates the plan: 409 `stale_plan`, changing nothing. A plan already applied under a
different key gives 409 `plan_already_applied`; replay of the successful key returns the
original response with 200, even after later changes. Application is atomic.

Application records the closure and all assignments together. Each moved booking increments
its revision once and gains one `reassigned` history entry with a `table_ids` change and
`plan_id`; accepted terms and times remain identical. Unmoved bookings gain nothing. The
restaurant revision increments once for the **whole plan**. Closures thereafter exclude
singles and pairs from availability and reject creates/amendments with 409 `table_unavailable`.
In explanations, `no_overlap` is false for a closure as for a conflicting booking.

Concurrent applications must not leave partially moved bookings. A closure at another
restaurant does not invalidate this plan.

## Amend recurring reservations

`POST /series/{series_id}/amend` is an owner-only idempotent write. Unknown or another owner's
series is 404; no token is 401. Body:

```json
{"expected_revision": 3, "from_index": 2, "local_time": "20:00"}
```

Revision must be a positive integer; from_index an integer in 0..count-1; local_time exactly
HH:MM in 00:00..23:59. Booleans are invalid integers. Invalid input gives 422
`validation_failed`; a mismatched series revision gives 409 `stale_revision` before any
occurrence's cutoff or booking validation. Unknown fields are ignored.

Consider indices at or after from_index, excluding cancelled occurrences and those marked
exception. Change their clock time on their original scheduled local dates, retaining each
reference, owner, party size and current table selection. A change with identical
resulting fields is a no-op and retains its terms. Each real change checks its old accepted
cutoff, then adopts the policy for its resulting start date, just like an individual PATCH.

The resulting occurrences must not conflict with unchanged occurrences, other bookings
or applied closures. On failure, histories, idempotency records and all revisions remain
unchanged. Non-occupancy errors take precedence in occurrence-index order; otherwise an
occupancy conflict returns `table_unavailable`.

On success return 201 with the current series response. Each changed occurrence gains one
ordinary changed history entry and one reservation revision. The series and restaurant
revisions each increase once for the entire operation if anything changed. Series amendments
do not mark exceptions. All-no-op or empty eligible sets succeed without changing revisions.
Replay returns the original response with 200 even after further edits or cancellations.

Seating repairs may move series occurrences. They preserve their exception flags, scheduled
dates, identities and accepted terms. Each affected series revision increases once per plan
application if at least one member moved.
Concurrent amendments from the same expected revision may not both make a real change.

A stage-4 service must accept exports produced by the same team's stages 1–3. These
operations must support imported series, including moved and cancelled occurrences.
Earlier booking and series receipts, histories and retries remain valid.
