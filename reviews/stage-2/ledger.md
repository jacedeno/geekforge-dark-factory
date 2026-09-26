# Requirements ledger: stage-2 (Tablekeeper stage 2)

Source: `plans/stage-2/brief.md` (E1..E15, Part B) plus every stage-1 requirement
(`reviews/stage-1/ledger.md`, rerun as regression).
Coverage: `S` = shipped check (harness `tablekeeper/test/stage_1`, `stage_2`), `P1:<test>` = stage-1
probe rerun against stage-2, `PA:<test>` = `reviews/stage-2/probes/test_api2.py`,
`PU:<test>` = `reviews/stage-2/probes/test_ui2.py`, `R` = code/file/screenshot review.

| # | Requirement | Source | Coverage |
|---|---|---|---|
| 1 | All stage-1 requirements still hold (except the response shape gaining `table_ids`) | Part B intro | S: stage_1 suite; P1: all stage-1 probes except `test_create_shape_strict` |
| 2 | Only `stage-2/` changed; `stage-1/` untouched | brief | S: factory-verify scope; R: diff |
| 3 | WO 2.1: first commit is a plain copy of accepted `stage-1/` | WO 2.1 | R: `git diff 71e7483:stage-1 <c>:stage-2` |
| 4 | Must not pass the stage-3 suite | A3 | S: harness probe stage |
| 5 | Screen routes `/`, `/signup`, `/login`, `/lookup` return `text/html; charset=utf-8`; API JSON unchanged | E1 | PA: test_screen_routes_html |
| 6 | No runtime network: every script/style/font served from the image (no external URLs) | contract, E15 | PU: test_no_external_requests; S: isolated mode |
| 7 | Restaurant detail carries `combinable` (default `[]`) | E3 | PA: test_detail_combinable |
| 8 | Reset: `combinable` pairs must be two distinct tables of that restaurant, else 422 | E12 | PA: test_reset_combinable_validation |
| 9 | Reset: seeded `table_ids`, `status` confirmed/cancelled; other status 422; cancelled seed does not occupy | E12 | PA: test_seeded_table_ids_and_status |
| 10 | Availability `available_options`: singles fixture order then pairs combinable order, capacity sum, member order, filter capacity>=party and no overlap on any member | E14 | PA: test_available_options_rules |
| 11 | `available_table_ids` unchanged (singles only) | E14 | PA: test_available_options_rules |
| 12 | Create with `table_ids` pair -> 201, `table_ids` in combinable order, no `table_id` | E10 | S: sample; PA: test_create_pair_shape |
| 13 | Single `table_id` or `table_ids` of one -> both `table_id` and `table_ids` in response | E10 | PA: test_create_single_shapes |
| 14 | E10 precedence: both -> 422; non-array/non-string -> 400; empty/dup -> 422; >2 -> combination_not_allowed; unknown/foreign -> 404; undeclared pair -> combination_not_allowed; then D5 d..h with summed capacity | E10 | PA: test_table_ids_error_matrix, test_table_ids_precedence |
| 15 | Not transitive: undeclared pair refused whatever sizes | Part B | PA: test_table_ids_error_matrix |
| 16 | Pair occupies both tables full duration; overlap on any member 409; singles blocked by pairs and vice versa | Part B | S: sample; PA: test_pair_occupancy |
| 17 | Cancel frees every table of the set | Part B | PA: test_pair_occupancy |
| 18 | PATCH accepts `table_ids` under the same rules; single <-> pair; failure unchanged | E10 | PA: test_patch_table_ids |
| 19 | Moves accept `table_ids`; both fields in a move 422; no table in overlapping results; swaps with pairs | E11 | PA: test_moves_table_ids |
| 20 | Idempotency for pair bookings (replay 200 identical; changed set 409) | §7 | PA: test_pair_idempotency |
| 21 | Concurrent pair vs single bookings sharing a member: exactly one wins | Part B concurrency | PA: test_concurrent_pair_vs_singles |
| 22 | Stage-2 export has a new schema marker and round-trips (pairs, statuses, receipts) | E13 | PA: test_stage2_roundtrip |
| 23 | Stage-1 export imports: logins, tokens, reservations (table_ids of one), references, timestamps, receipts replay original stage-1 response 200, failed keys reusable | E13 | S: sample (accounts); PA: test_upgrade_from_stage1 (real stage-1 container) |
| 24 | Signup/login screens: testids, `auth-error` only when there is one, `current-user` on every screen, logout | Part B | S: several; PU: test_current_user_on_every_route, test_logout_forgets_token |
| 25 | Token kept across navigation of the four routes | E2 | PU: test_current_user_on_every_route |
| 26 | Grid: a cell per table per slot with correct `data-available`; `no-slots` on closed day | Part B | S; PU: test_grid_matches_api |
| 27 | Combination cells for declared pairs with sum >= party; available iff in `available_options`; small pairs absent; human labels | E4 | PU: test_combination_cells |
| 28 | Unavailable click does nothing; signed-out click -> auth-error with /login link, grid kept | E4 | S; PU: test_signed_out_click |
| 29 | Out-of-order search: late response of older search never applied | E5 | PU: test_out_of_order_search |
| 30 | Booking form: summary names every table label + local time; party prefilled | Part B, E8 | S: single; PU: test_pair_booking_flow |
| 31 | Confirmation: reference exact, details with restaurant, table labels, time; confirmation-tables | Part B, E8 | S; PU: test_pair_booking_flow |
| 32 | Unchanged resubmission -> same reference, no error, one booking | Part B, E6 | S; PU: test_resubmit_same_reference |
| 33 | Changed field -> new booking (new key) | Part B, E6 | S; PU: test_changed_field_new_booking |
| 34 | 409 -> booking-error, availability refreshed, form and inputs kept, no confirmation | Part B, E7 | S partial; PU: test_conflict_refreshes_and_keeps_form |
| 35 | Lost response after commit -> booking-uncertain, no error/confirmation; retry same key recovers original reference | Part B, E7 | PU: test_lost_response_recovers |
| 36 | 5xx / timeout -> uncertain, not error | E7 | PU: test_server_error_is_uncertain |
| 37 | Any 4xx -> booking-error, uncertain removed | E7 | PU: test_4xx_after_uncertain |
| 38 | Lookup: signed-in token; detail, status exact, tables, cancel button while confirmed; not found / signed out -> reservation-error; refused cancel -> error; cancel updates without reload | E9 | S partial; PU: test_lookup_matrix |
| 39 | Pending retry identity + form survive export/import between requests | Part B upgrade | PU: test_retry_survives_upgrade |
| 40 | Browser signed in before upgrade stays signed in | Part B upgrade | PU: test_signed_in_survives_upgrade |
| 41 | Data text inserted as text (no HTML injection) | E1 | PU: test_no_html_injection |
| 42 | Visual quality: warm design system, distinct states, labels, focus, contrast | E15 | R: screenshots at 375 and 1280 |
| 43 | No horizontal scroll at 375 px and desktop on all four routes and with a grid | E15 | PU: test_no_horizontal_scroll |
| 44 | Inputs have visible labels; keyboard focus visible | E15 | PU: test_inputs_labelled_and_focus_visible |
| 45 | Concurrent requests serial-equivalent, never 5xx | Part B | S; PA: test_concurrent_pair_vs_singles; P1 concurrency probes |
| 46 | No code written to the checks | mandate | R |
