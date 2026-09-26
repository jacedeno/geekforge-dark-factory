# Requirements ledger: stage-3 (Tablekeeper stage 3)

Source: `plans/stage-3/brief.md` (F1..F12, Part B) plus stage-1 and stage-2 requirements
(`reviews/stage-1/ledger.md`, `reviews/stage-2/ledger.md`, rerun as regression).
Coverage: `S` = shipped check, `P1`/`P2A`/`P2U` = stage-1 / stage-2 API / stage-2 browser probes
rerun against stage-3, `P3:<test>` = `reviews/stage-3/probes/test_api3.py`, `R` = code read.

| # | Requirement | Source | Coverage |
|---|---|---|---|
| 1 | Stage-1 and stage-2 behaviour unchanged except response shapes gaining `revision`/`accepted_terms` | Part B | S: stage_1, stage_2 suites; P1, P2A, P2U (strict key-set / upgrade-shape probes replaced by P3 versions) |
| 2 | Only `stage-3/` changed; WO 3.1 is a plain copy of accepted `stage-2/`; stage-1/2 dirs untouched | brief | S: scope; R: tree hashes |
| 3 | Must not pass the stage-4 suite | A3 | S |
| 4 | `explain` absent -> no explain key; only `true` accepted; `false`,`1`,`True`,`""` -> 422 | F1 | S: absent; P3: test_explain_values |
| 5 | Explain: every table once, fixture order; both rules in order; available iff both; ids == available_table_ids | Part B 1-3 | S: partial; P3: test_explain_rules_all_reported |
| 6 | Explain: closed day `slots: []`; fully booked slot still has full explain | Part B 4 | P3: test_explain_closed_and_full |
| 7 | Explain `policy_version` = policy selected for the date | F1 | P3: test_explain_policy_version |
| 8 | Policies: 401 -> 404 -> 403 -> key 400 -> key>255 422 -> body 400 -> idempotency -> validation | F3 | P3: test_policy_precedence |
| 9 | Policy validation matrix (types incl. booleans/strings -> 422, ranges, real date, hours rules, empty hours ok, no dup weekday, capacities exact ids 1..100) | F3, Part B | P3: test_policy_validation_matrix |
| 10 | Versions 1.. per restaurant; failed writes and replays allocate none; replay 200 original; reuse 409 | Part B | P3: test_policy_versions_and_replay |
| 11 | 201 echoes six fields + policy_version, no unknown fields; GET policies public, publication order, no policy 0, 404 unknown | F3 | P3: test_policy_listing |
| 12 | Restaurant detail unchanged: original fixture config, no `manager_user_ids` | F4 | P3: test_detail_unchanged |
| 13 | Selection: greatest effective_from <= date, ties greatest version, else policy 0; publication order independent | F2 | S: partial; P3: test_policy_selection |
| 14 | Availability uses the date's policy (grid, duration, hours, capacities, pair capacities) | F2 | P3: test_availability_uses_policy |
| 15 | Bookings/amendments validated against the resulting start date's policy | F2 | P3: test_booking_uses_policy, test_patch_adopts_new_policy |
| 16 | Every reservation response has `revision` (1) and `accepted_terms` (6 keys, no effective_from); ends_at from accepted duration | F6 | S: partial; P3: test_terms_shape |
| 17 | Publication never changes existing bookings (terms, end, history, revision) | Part B | P3: test_publication_does_not_touch_bookings |
| 18 | Cancel and amendment use accepted cutoff (not the new policy's) | Part B, F7 | P3: test_accepted_cutoff_rules |
| 19 | Real amendment: revision +1 once, terms and end replaced; no-op: unchanged, no history; cancel +1 once, repeat no change | F7 | P3: test_revision_rules |
| 20 | `expected_revision`: invalid (bool, string, 0, negative, float) 422; mismatch 409 stale_revision before cancelled/cutoff/validation; 404 before it | F7 | P3: test_expected_revision_matrix |
| 21 | Concurrent PATCHes with one expected_revision: at most one real change | F7 | P3: test_concurrent_expected_revision |
| 22 | Old idempotency responses never rewritten (original revision/terms) | F6 | P3: test_replay_keeps_original_terms |
| 23 | History: owner only; other user, no token, bad token, unknown -> 404 | F8 | P3: test_history_decision_404s |
| 24 | History rules 1-5 (seq, created 3 fields null, changed only changed fields in order, no-op no entry, cancelled empty last, replay nothing) | Part B | S: partial; P3: test_history_rules |
| 25 | History `at` in restaurant offset, non-decreasing; each entry has revision + terms as after that event | F8 | P3: test_history_terms_snapshots |
| 26 | Combined-table history: pair creation uses table_ids; changes involving a pair use table_ids full lists in combinable order; reversed pair is a no-op | Part B | P3: test_pair_history |
| 27 | Decision: `{reference, revision, accepted_terms}` current, incl. after cancel; 404 rules | Part B | S: partial; P3: test_decision |
| 28 | Seeded bookings: revision 1, policy-0 terms, history synthesised (created at created_at, + cancelled) | F8 | P3: test_seeded_history |
| 29 | Series validation order and codes (401, key, body, idempotency, field 422 incl. booleans, 404, cancelled, already_in_series, cutoff) | F9 | P3: test_series_precedence |
| 30 | Series success: count occurrences, index order, distinct refs, occurrence 0 == anchor untouched, generated revision 1 + created history, occupy tables, appear in lists | Part B, F9 | S: partial; P3: test_series_success |
| 31 | Per-occurrence policy (duration, capacity, hours); first failing occurrence decides; nothing created on failure | F9 | P3: test_series_per_occurrence_policy_and_atomicity |
| 32 | Series DST: nonexistent -> invalid_local_time whole adoption; repeated -> first occurrence | Part B | P3: test_series_dst |
| 33 | Series occupancy failure against existing bookings -> table_unavailable, all-or-nothing, key reusable | F9 | P3: test_series_per_occurrence_policy_and_atomicity |
| 34 | GET /series owner only (other, no token -> 404); current states; replay returns original series response | F10 | P3: test_series_read_and_replay |
| 35 | Series revision/exception rules (PATCH real +1 exception; no-op/failure nothing; cancel +1 no exception; repeat cancel nothing; anchor cancel keeps siblings) | F10 | P3: test_series_revision_rules |
| 36 | Pair anchor: occurrences use the pair | Part B | P3: test_series_pair_anchor |
| 37 | Moves under policies: per-move expected_revision (422/409), accepted cutoff, new date policy, revision +1 + one history per changed booking, no-op unchanged, failure unchanged, series once per batch + exceptions | F11 | P3: test_moves_under_policies |
| 38 | Stage-3 export/import round trip preserves policies, managers, revisions, terms, history, series, receipts, version counter | F12 | P3: test_stage3_roundtrip |
| 39 | Upgrade from stage-1 and stage-2 exports: logins, tokens, replays of original bodies, history synthesised, decision, series adoption on imported bookings | F12 | S: accounts; P3: test_upgrade_from_stage1, test_upgrade_from_stage2 |
| 40 | Concurrency serial-equivalent, never 5xx (policies publication races, series races) | Part B | P3: test_concurrent_policy_publication; P1/P2A concurrency |
| 41 | No new screens; UI still works with new response fields | A3 | P2U |
| 42 | No code written to the checks | mandate | R |
