# Final report: Tablekeeper, stages 1-4

All four increments were accepted by the verifier. Each check command (harness, `--mode isolated`)
passed twice on a fresh clone of the final commit.

| Increment | Final commit | Verdict | Check | Verifier probes | Known gaps |
|---|---|---|---|---|---|
| stage-1 | 71e748308f9d | ACCEPT (2nd delivery) | PASS: highest contiguous 1, claimed 1; stage-1 suite 120 passed, stage-2 fails as required | 127/127; ledger 76/76 | none |
| stage-2 | 48a39df74bc1 | ACCEPT (1st delivery) | PASS: highest contiguous 2, claimed 2; stage-2 25 passed, stage-3 fails | 126 + 39 API + 25 browser, 0 failed; ledger 46/46 | none |
| stage-3 | 1937f0188182 | ACCEPT (1st delivery) | PASS: highest contiguous 3, claimed 3; stage-3 7 passed, stage-4 fails | 126 + 37 + 25 + 37, 0 failed; ledger 42/42 | none |
| stage-4 | c150b0fe7708 | ACCEPT (1st delivery) | PASS: highest contiguous 4, claimed 4; stage-4 6 passed | 126 + 37 + 25 + 37 + 80, 0 failed; ledger 26/26 | series-amend `cutoff_passed` checked by code read only (not probeable without wall-clock time passing) |

Rejections: one. stage-1 at 84a7bbb (TRACE/custom methods returned 501; an export holding an
empty display_name was not importable), fixed in 71e7483.

Environment issue found and worked around: the seat shells inherit PYTHONHOME/PYTHONPATH/
LD_LIBRARY_PATH from the Jam app, which breaks python and the git hooks; every seat ran git and
the check under `env -u PYTHONHOME -u PYTHONPATH -u LD_LIBRARY_PATH`. `factory-post` strips the
owner from handles (`@builder`), which Jam cannot resolve; handoffs were posted with full handles
by an equivalent splitter instead.

## Decisions on open points (full text in plans/stage-N/brief.md)

- stage-1 (D1-D17): in-memory state behind one global write lock (serial-equivalent concurrency),
  password hashing (scrypt) outside the lock; fixed precedence orders for idempotent writes
  (auth, key, body, idempotency, validation), for POST/PATCH/cancel errors and for moves (input
  order, occupancy last); cutoff boundary instant counts as inside the cutoff; cancel of a
  cancelled booking is 200 before the cutoff check; DST slots on the wall-clock grid, gaps
  dropped, folds resolved to the first occurrence, same rule for booking; `created_at` in UTC
  `+00:00`; generated references 8 chars A-Z0-9; emails case-insensitive; export state carries an
  internal schema marker for upgrades. D14 revised once: seeded references must follow the §8
  format (the shipped check and the text agree; my earlier relaxation was withdrawn).
- stage-2 (E1-E15): plain client UI served by the service, token in browser storage; restaurant
  detail includes `combinable`; combination cells rendered for every declared pair large enough
  for the party, `data-available` from `available_options`; latest-search-wins by sequence
  number; idempotency key kept while the body is unchanged; network error/5xx = uncertain, any
  4xx = confirmed rejection; lookup requires sign-in (no leaking of others' bookings);
  `table_ids` precedence (both fields 422, >2 ids combination_not_allowed, pair stored in
  `combinable` order); stage-1 exports migrate on import.
- stage-3 (F1-F12): one policy-selection function everywhere; policy fields of wrong type are
  422 (policy section overrides the generic 400 rule); precedence 401/404/403 then idempotency for
  policy writes; stale_revision before cancelled/cutoff; no-op PATCH still needs an editable
  booking; history `at` in the restaurant offset; seeded and imported bookings get revision 1 and
  a synthesised `created` entry; series precedence and index-order failure; restaurant revision
  kept internally from stage 3.
- stage-4 (G1-G10): considered bookings = every confirmed booking of the restaurant overlapping
  the closure (any table), all others fixed; candidates under each booking's own accepted
  capacities; exact lexicographic optimum (moves, unused seats, rank vector by reference);
  `planning_limit` above 6 tables, 4 pairs or 6 considered bookings; apply precedence
  plan_already_applied before stale_plan; `reassigned` history uses a `table_ids` change plus
  `plan_id`; closures block availability, explain, creates, amendments, moves, series and later
  plans; series amend precedence 404, 422, stale_revision, then index-order validation and
  occupancy last.
