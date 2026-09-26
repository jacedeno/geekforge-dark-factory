# Requirements ledger: stage-4 (Tablekeeper stage 4)

Source: `plans/stage-4/brief.md` (G1..G10, Part B) plus stages 1-3 (their ledgers, rerun as
regression). Coverage: `S` = shipped check, `P1`/`P2A`/`P2U`/`P3` = earlier probes rerun against
stage-4, `P4:<test>` = `reviews/stage-4/probes/test_api4.py`, `R` = code read.

| # | Requirement | Source | Coverage |
|---|---|---|---|
| 1 | Stages 1-3 unchanged | Part B | S: stage 1-3 suites; P1, P2A, P2U, P3 |
| 2 | Only `stage-4/` changed; WO 4.1 is a plain copy of accepted `stage-3/` | brief | S: scope; R: tree hashes |
| 3 | Restaurant revision: 0 after reset; +1 per booking, adoption, moves batch, real amendment, cancel, policy, series amend with change, plan application; not for no-ops, failures, previews, replays | G1 | P4: test_restaurant_revision_counting |
| 4 | Replans precedence: 401, 404 restaurant, 403, key 400/422, body 400, replay, 422 interval (missing/non-string/no offset/from>=to), 404 table, 422 planning_limit | G2 | P4: test_replan_precedence, test_planning_limit |
| 5 | Considered set = confirmed bookings of the restaurant overlapping [from,to) on any table; cancelled ignored; others fixed | G3 | P4: test_planner_matches_reference |
| 6 | Candidate options: capacity under own accepted terms, no closed table, no conflict with fixed, closures, other assignments; cutoffs ignored | G3 | P4: test_planner_matches_reference, test_planner_own_terms_and_cutoff |
| 7 | Exact lexicographic optimum (moved count, unused seats, rank vector by reference) | G4 | P4: test_planner_matches_reference (randomised vs exhaustive reference) |
| 8 | Fast at the limits (6 tables, 4 pairs, 6 bookings) under 5 s | G4 | P4: test_planner_at_limits_is_fast |
| 9 | Preview shape: plan_id, restaurant_revision, closure echo, assignments in reference order with table_ids order and changed, moved_count, unused_seats; empty plan when nothing considered | G5 | S: empty; P4: test_planner_matches_reference, test_preview_shape_and_no_side_effects |
| 10 | Preview changes nothing (no closure, revisions, history); replay returns same plan; 409 no_feasible_plan changes nothing | Part B | P4: test_preview_shape_and_no_side_effects, test_no_feasible_plan |
| 11 | Apply precedence: 401, 404, 403, key, body object, replay 200, 404 unknown/other restaurant plan, 409 plan_already_applied, 409 stale_plan | G6 | P4: test_apply_precedence |
| 12 | Apply effects: new table sets, revision +1, one reassigned history entry (plan_id, table_ids change), terms/times unchanged; unmoved unchanged; restaurant +1 once; series +1 once, exceptions unchanged; response reservations in reference order | G7 | P4: test_apply_effects |
| 13 | Closure applies everywhere: availability, options, explain no_overlap false, creates, amendments, moves, series adoption, series amend (409), later plans; half-open boundaries | G8 | P4: test_closure_everywhere |
| 14 | Closure at another restaurant does not invalidate a plan | Part B | P4: test_other_restaurant_does_not_stale |
| 15 | Concurrent applications never partially move (two plans at one revision; one plan under two keys) | Part B | P4: test_concurrent_apply |
| 16 | Lookup/confirmation screens show new tables after an applied plan | WO 4.3 | P4: test_lookup_screen_after_apply |
| 17 | Series amend precedence: 401, key, body, replay, 404, 422 (expected_revision, from_index 0..count-1, local_time HH:MM, booleans), 409 stale_revision | G9 | S: success; P4: test_series_amend_precedence |
| 18 | Eligible = index >= from_index, not cancelled, not exception; scheduled dates; no-op identical; policy of resulting date; no exception marks | G9 | P4: test_series_amend_semantics |
| 19 | Non-occupancy error in index order beats occupancy; occupancy vs other bookings, unchanged occurrences, closures; all-or-nothing | G9 | P4: test_series_amend_errors |
| 20 | Series amend DST: nonexistent -> invalid_local_time | G9 | P4: test_series_amend_errors |
| 21 | Success: changed occurrences revision +1 and one changed entry; series and restaurant +1 once; all-no-op / empty -> 201 unchanged; replay 200 original after edits | G9 | P4: test_series_amend_semantics |
| 22 | Concurrent amends from one expected revision: at most one real change | Part B | P4: test_concurrent_series_amend |
| 23 | Stage-4 round trip keeps closures, plans (applied/unapplied, revisions), restaurant revisions, receipts | G10 | P4: test_stage4_roundtrip |
| 24 | Imports of stage-1/2/3 exports; replans and series amend on imported state incl. moved and cancelled occurrences; receipts/histories/retries valid | G10 | P4: test_upgrade_from_stage3, test_upgrade_from_stage1 |
| 25 | Cutoff check in series amend for an occurrence inside its accepted cutoff | G9 | R only (requires wall-clock time to pass between adoption and amendment; not constructible in a probe) |
| 26 | No code written to the checks | mandate | R |
