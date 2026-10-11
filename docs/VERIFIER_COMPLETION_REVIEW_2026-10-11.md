# Verifier completion review — revised after independent Claude challenge

The user supplied Claude's review of candidate 65a6abc. Its verdict was MODIFY for offline integration and STOP for the proposed numeric policy. Claude reported running 132 tests and 43 subtests plus direct probes; it could not reproduce the pytest launcher or independently authenticate the commit. The supplied archive was independently assembled here against GitHub's exact tree: all 106 Git blob hashes matched. This establishes archive provenance, not independent approval.

## Reproduced defects and revised behavior

The old launcher excluded a non-database postcheck test using broad name filters. Its expected issue list was stale: correct behavior emitted both evidence_wrong_week and evidence_week_mismatch. The assertion is corrected and runs. The launcher now selects exactly ten database-dependent node IDs and fails if any expected exclusion is absent. Regressions check exact deselection and fail-closed manifest drift. The earlier claim that all eleven exclusions required a database was incorrect; the old 206 count does not certify this revised candidate.

Whole-report COMPLETE certification is now withheld in both the shared runner and HTML renderer. Verifying individual claims does not establish Q1-Q3 coverage, named states/categories, delivery interpretation or two/three actionable questions. There is no trusted independently reviewed coverage validator yet. A future validator is outstanding; this change does not pretend to implement one. A custom permissive verifier can preserve a selected claim but cannot certify the report. The runner exposes selected_claims_verified separately and it is false for any accumulated warning or mandatory human-review flag. Tool warnings and unavailable evidence appear next to the report badge as well as in the report.

Nested drafts must explicitly supply both run week and previous comparison week; missing periods fail before a delegated verifier executes. Legacy lists remain unable to establish report completion.

The proposed numeric policy remains disabled by default and under STOP. Additional restrictions remove zero-magnitude rise/fall assertions, flag ambiguous category-orders wording (physical metric orders_with_category), and reject mismatched or extra declared filters as support. Thresholds use tools.config.MIN_ORDERS and BASELINE_WEEKS. Rounding uses shared NUMERIC_PRECISION_DIGITS, also used by the tool builder and explicitly declared in new tool parameters. Opt-in keep requires matching declared precision; preserved historical captures lack that declaration and now remain flagged even for generated atomic diagnostics. They are never retroactively edited or stamped with precision metadata.

## Scope and verification

Regression coverage includes zero percentages and point changes, category orders, filter mismatch, undeclared precision, shared threshold changes, missing draft periods, one-fact completion, uncited warnings, renderer errors and exclusion accounting. The revised network-blocked suite passes 219 tests and 43 subtests; exactly ten database-dependent checks remain excluded.

The original saved S1 remains PARTIAL; the wrong-week capture remains REJECTED. New tool precision metadata has only offline coverage here, not a newly executed Athena query or model run. No corrected live success, full golden-data acceptance, browser layout acceptance or independent approval of this revision is claimed.

## Remaining decisions

Re-review this revised candidate. Keep allow_verified_facts disabled. Review a real coverage validator separately before permitting whole-report COMPLETE. Full golden database acceptance, browser review and a freshly authorized bounded live test remain outstanding. No spending, merge, publication or submission is authorized by the review or this remediation.
