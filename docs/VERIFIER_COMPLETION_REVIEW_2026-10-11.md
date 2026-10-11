# Verifier completion review

The current offline S1 report is PARTIAL because its three prose findings remain flagged. This is separate from database acceptance. A corrected live run with the current default verifier can still be partial even with correct tool periods and accurate numbers.

## Confirmed behavior

The fallback in src/wbr_runner.py never keeps a claim solely because citations exist. src/numeric_verifier.py checks a deliberately narrow additive-metric percentage grammar. Matching numbers remain flagged by default. Its allow_verified_facts opt-in is a proposed policy tested against synthetic evidence; it is not enabled by this change.

The candidate now also checks a narrow average review-score point-change grammar. It reconciles the rounded current/previous means and reported difference, uses only the exact cited metric/week/slice, limits scores to the valid 1–5 range, and preserves the same quality/confidence and explicit opt-in requirements. It verifies neither unusualness nor causation. Eight focused regressions cover opt-in behavior, wrong direction/amount, percent and prose rejection, invalid/inconsistent evidence, quality/review flags, wrong scope/period, excessive precision and conflicting captures.

Replaying the unmodified earlier S1 capture with either the default verifier or the proposed numeric opt-in returns PARTIAL and three flag outcomes. Two claims require human review; one returns semantic verification unavailable. None matches the supported atomic fact grammar. Recorded delivery and category/state slices also contain small-sample warnings.

## Prompt preparation change

The analyst is instructed to keep revenue, orders and items percentage facts atomic and separate from association or interpretation. The grammar uses an optional exact category prefix, lowercase metric, fell/rose, reported percentage, and optional exact state suffix. Each fact must cite its own metric/week/filter evidence. Example values are explicitly illustrative, not defaults.

Warnings take precedence over compact wording. Review-score changes, unusualness, change shares and delivery associations are not silently recast as supported facts or discarded to get a complete badge. Their semantic review remains necessary.

This change does not alter confidence, quality thresholds, verifier defaults, human-review flags or historical captures. It prepares output for existing bounded verification; it does not establish live prompt compliance or authorize a new run.

## Independent review and acceptance needed

An offline policy diagnostic against the original saved tool envelopes supports three generated atomic assertions: revenue fell 15.32%; orders fell 9.89%; health_beauty revenue fell 49.6%. Each cites only its matching unfiltered-state metric result, and postchecks derive medium confidence with no human-review requirement. The proposed opt-in keeps those bounded assertions; the default still flags them. A regression verifies the postcheck/policy agreement, preserves a mandatory human-review flag when present, and checks that capture bytes and envelopes remain unchanged.

These generated assertions are not the original model claims, a newly executed query, a new complete model report or proof of Q2/overall task coverage. The historical report remains PARTIAL. This positive diagnostic reduces uncertainty about the bounded policy's use on these particular captures; independent approval of the policy remains outstanding.

The recorded-evidence regression additionally checks the generated Q2 assertion “average review score rose 0.15 points.” Postchecks derive medium confidence without a human-review requirement and the proposed point verifier keeps it. This verifies a bounded Q2 difference only, not the review-state/delivery interpretation or complete Q2 investigation. The latest network-blocked suite passes 206 tests and 43 subtests; 11 golden-database checks remain excluded. Saved captures and demo pages are unchanged.

Review the exact numeric policy before using it outside its synthetic tests. Evaluate whether its arithmetic rounding intervals, all-citations scope, quality thresholds and confidence rule are appropriate for real golden-data outputs. Reviewers must reconcile postcheck confidence with verifier confidence and preserve every mandatory coverage requirement.

Full golden-data acceptance, independent code/policy review, browser layout review and freshly authorized corrected live validation remain outstanding. A COMPLETE numeric report would certify only its bounded selected facts under the configured policy; it would not prove causes, all prose or end-to-end usefulness to a real analyst.

Do not edit the saved S1 claims or suppress their review flags to fabricate a successful demo. No paid run, merge, public release or submission is authorized by this preparation.
