# Requirements ledger: stage-1 (Tablekeeper stage 1)

Source: `plans/stage-1/brief.md` (Part A decisions D1..D17, Part B spec sections).
Coverage: `S:<file>::<test>` = shipped check (harness `tablekeeper/test/stage_1/`),
`P:<test>` = own probe in `reviews/stage-1/probes/test_probes.py` (run by `probes/run.sh`),
`R` = code/file read, `-` = not yet covered.

| # | Requirement | Source | Coverage |
|---|---|---|---|
| 1 | Dockerfile builds; RUN.md gives one command that builds and runs with `-e PORT` + mapping | §2, WO1.1 | S: harness build; R: RUN.md |
| 2 | No outbound network at run time (tz data baked in) | §2, contract | S: `--mode isolated` run |
| 3 | Listens on 0.0.0.0:$PORT; default 8080 when PORT unset | §3.1 | P: run.sh starts with PORT=9123 and a second container without PORT (8080) |
| 4 | `GET /health` 200 `{"status":"ok"}` within 60 s, under 2 vCPU/2 GiB | §3.2 | S: test_health_reports_ok; P: run.sh measures time to healthy |
| 5 | Reset 204, replaces all state, repeated resets | §3.3 | S: test_reset_is_synchronous_and_replaces_all_state |
| 6 | Reset: missing top-level arrays default to `[]` | D14 | P: test_reset_missing_arrays_default_empty |
| 7 | Reset: non-object / invalid data -> 422, state unchanged; bad JSON -> 400 | D14 | P: test_reset_invalid_leaves_state; test_reset_unparseable_400; S: ids>64, bad references |
| 8 | Reset: unknown weekday -> 422 | D14 | P: test_reset_invalid_leaves_state |
| 9 | Seeded reservation: confirmed, created_at from fixture else reset time | D14 | P: test_seeded_created_at |
| 10 | Seeded users log in immediately | §4 | S: test_seeded_user_can_log_in_immediately |
| 11 | Content-Type `application/json; charset=utf-8` on JSON responses | §3.4 | P: test_content_type_on_success_and_error |
| 12 | Unknown body fields and query params ignored | §3.4 | S: test_unknown_body_fields_are_ignored, test_unknown_query_parameters_are_ignored |
| 13 | Every 4xx has `{"error":{"code","message"}}` | §5 | P: every probe error goes through `err()` which checks envelope |
| 14 | Unknown route 404 `not_found`; wrong method 405 `method_not_allowed` (any method, incl. TRACE/unknown) | D15, §5 | P: test_unknown_route_404, test_wrong_method_405, test_unusual_http_method_is_not_5xx |
| 15 | No 5xx under odd input (bad JSON, arrays, nulls, huge values, unicode) | §5 | P: test_garbage_inputs_never_5xx |
| 16 | `GET /restaurants` shape `{id,name,timezone}` | §8 | S: test_restaurant_list_shape |
| 17 | `GET /restaurants/{id}` fixture shape incl. tables, opening_hours; 404 unknown; public | §8 | S: test_restaurant_detail_carries_the_fixture_shape, 404, public |
| 18 | Signup 201 `{user_id,display_name,token}`; login 200 same shape | §6 | S: sample; P: test_auth_shapes |
| 19 | 409 email_taken, case-insensitive | §6, D13 | S: exact-case; P: test_email_case_insensitive |
| 20 | 422: password < 8 (7 fails, 8 passes), bad email forms, missing field, empty display_name | §6, D13 | S: partial; P: test_signup_validation_matrix |
| 21 | 400 wrong JSON type in signup/login; 422 before 409 | D13 | S: email=17; P: test_signup_validation_before_taken, test_login_wrong_type_400 |
| 22 | Login 401 wrong password / unknown email; case-insensitive email login | §6, D13 | S; P: test_email_case_insensitive |
| 23 | Passwords hashed (scrypt/bcrypt/Argon2), not plaintext; export holds no plaintext | §6 | P: test_export_has_no_plaintext_password; R: code |
| 24 | Tokens never expire; multiple tokens per account all valid | §6 | P: test_multiple_tokens_valid |
| 25 | 401 for missing, malformed (`Basic`, `Bearer` no token, lowercase bearer), unknown token on every protected endpoint | §6, D13 | S: partial; P: test_protected_endpoints_401_matrix |
| 26 | 50 concurrent logins complete < 5 s each | D1, §2 | P: test_fifty_concurrent_logins |
| 27 | Availability shape; slot grid; capacity filter; fixture order; empty lists kept | §8 | S: sample tests |
| 28 | Closed day -> `slots: []` | §8 | S: test_closed_day_returns_no_slots |
| 29 | Availability: missing param 422; bad date 422; party_size non-digits/0 -> 422; unknown restaurant 404 | D9 | S: partial; P: test_availability_param_matrix |
| 30 | Availability: slot where end == closes kept; one beyond dropped; slot_minutes not dividing | §8, D10 | P: test_slot_boundary_and_odd_grid |
| 31 | DST spring: skipped times never listed (Berlin, NY) | §9 | P: test_spring_forward_slots |
| 32 | DST fall: repeated time once, first occurrence, offsets | §9 | S: Berlin; P: NY fall test_ny_fall_back |
| 33 | Listed slots == bookable slots on DST days (both zones, both transitions) | D10 | P: test_every_listed_slot_is_bookable_on_transition_days |
| 34 | Duration absolute across DST; closes check in absolute time | §9, D10 | S: Berlin fall; P: test_spring_forward_duration_absolute |
| 35 | Create: response shape exact, reference 6..12 `A-Z0-9`, reservation_id <= 64, created_at `+00:00` seconds | §8, D11, D12 | S: shape; P: test_create_shape_strict |
| 36 | Create D4 order: 401 -> 400 missing key -> 422 key > 255 -> 400 body -> idempotency -> validation | D4 | P: test_create_precedence_order |
| 37 | Key of exactly 255 accepted, 256 -> 422; empty header -> 400 | §5, §7 | P: test_idempotency_key_length |
| 38 | Body not an object (array, string) -> 400 | D4 | P: test_create_body_not_object |
| 39 | D5 a: missing required field -> 422 | D5 | P: test_create_missing_field |
| 40 | D5 b: non-string restaurant_id/table_id/starts_at_local -> 400; party_size bool/float/string/0 -> 422 | D5, §5 | S: partial; P: test_create_wrong_types |
| 41 | starts_at_local strict format and real date (2026-02-30, 24:00, 19:60, T19:0) -> 422 | D5 | S: partial; P: test_starts_at_local_formats |
| 42 | D5 c..h ordering (e.g. 404 before DST, hours before grid, grid before capacity, capacity before overlap) | D5 | P: test_create_error_precedence |
| 43 | Overlap half-open; overlap only against confirmed | §1 | S: adjacent, overlap; P: cancelled does not block (S sample) |
| 44 | Past starts allowed | §4 | P: test_past_booking_allowed |
| 45 | Idempotency: 201 then 200 identical; replay after cancel; 409 different body even if invalid | §7 | S: partial; P: test_reuse_with_invalid_body_is_409 |
| 46 | Key reusable after 4xx | §7 | P: test_key_reusable_after_4xx |
| 47 | Same key different path independent; per-user scope | §7 | S: per-user; P: test_same_key_other_path |
| 48 | Body equality is JSON-value equality (key order/whitespace) | §7 | P: test_replay_key_order_whitespace |
| 49 | Replay makes no state change (no second booking) | §7 | P: test_replay_no_state_change |
| 50 | 50 concurrent identical requests -> one 201, rest 200 same body, one booking | §7 | P: test_concurrent_identical_key |
| 51 | 50 concurrent competing bookings -> one 201, rest 409, no 5xx | §1 | S: 10 clients; P: test_fifty_compete_one_slot |
| 52 | Concurrent cancel-and-rebook / PATCH onto one free table -> one winner | §1 | P: test_concurrent_patch_onto_one_table |
| 53 | GET /reservations: caller's only, starts_at desc, both statuses, empty list | §8 | S; P: empty list in test_empty_reservation_list |
| 54 | GET /reservations/{ref} 404 if not caller's | §8 | S |
| 55 | Cancel: 200 cancelled; twice 200; 404 other's; cutoff 409 incl. boundary instant | §8, D6, D7 | S: partial; P: test_cutoff_boundary |
| 56 | Cancel order: already cancelled returns 200 even inside cutoff | D7 | P: test_cancel_already_cancelled_inside_cutoff |
| 57 | PATCH: subset of fields; validation as create; 409 cancelled; cutoff vs current start | §8, D8 | S: partial; P: test_patch_matrix |
| 58 | PATCH: atomic, failure leaves booking unchanged; overlap excludes self (shift by one slot) | §8, D8 | P: test_patch_self_overlap_and_failure_unchanged |
| 59 | PATCH: no-op / identical values 200 unchanged; restaurant_id ignored; bad body 400 | D8 | P: test_patch_noop_and_bad_body |
| 60 | Export 200 `{track, format_version:1, state}` with schema marker | §10, D3 | S: sample; P: test_export_shape |
| 61 | Import 204 replaces atomically; importing twice duplicates nothing | §10 | P: test_import_twice_no_duplicates |
| 62 | After import: login with password, old tokens, references, statuses, timestamps, ids identical | §10 | P: test_export_import_roundtrip_full |
| 63 | After import: replays 200 original; failed keys reusable; different body 409 | §10 | P: test_export_import_roundtrip_full |
| 64 | Import into a fresh container works (no dependency on source process) | §10 | P: test_import_into_fresh_container (second container) |
| 64b | Import accepts the unchanged export of any state reset accepted | §10 | P: test_export_of_any_accepted_fixture_is_importable |
| 65 | Invalid import (wrong track/version, missing state, non-object state, bad state) -> 422 unchanged; bad JSON 400 | §10, D17 | P: test_invalid_imports |
| 66 | Import removes previous destination data and tokens; reset clears imported state | §10 | P: test_import_replaces_and_reset_clears |
| 67 | Export is a snapshot: later writes do not change it | §10 | P: test_export_is_snapshot |
| 68 | Moves: auth 401, key required, 201 input order incl. unchanged | §11 | S: batch of one; P: test_moves_basic |
| 69 | Moves shape errors -> 422 (not array, 0, 9, non-object, missing/non-string ref, dup, wrong type) | D16 | P: test_moves_shape_errors |
| 70 | Moves: 404 other's/unknown; 422 different restaurant; 409 cancelled; 409 cutoff; precedence in input order | D16 | P: test_moves_error_precedence |
| 71 | Moves: swap succeeds; overlap among results or with unlisted -> 409; all-or-nothing | §11 | P: test_moves_swap, test_moves_all_or_nothing |
| 72 | Moves: replay 200 original after later changes; receipts survive export/import | §11 | P: test_moves_replay_and_export |
| 73 | Nothing from stage 2 (combinable, table_ids, available_options, explain, HTML) | Part A3 | S: harness probe of stage-2 suite; R: code |
| 74 | Nothing outside `stage-1/` changed | brief | S: factory-verify scope |
| 75 | No code written to the checks | mandate | R: code read |
