# Verifier completion review

The current offline S1 report is PARTIAL because its three prose findings remain flagged. This is separate from database acceptance. A corrected live run with the current default verifier can still be partial even with correct tool periods and accurate numbers.

## Confirmed behavior

The fallback in src/wbr_runner.py never keeps a claim solely because citations exist. src/numeric_verifier.py checks a deliberately narrow additive-metric percentage grammar. Matching numbers remain flagged by default. Its allow_verified_facts opt-in is a proposed policy tested against synthetic evidence; it is not enabled by this change.

Replaying the unmodified earlier S1 capture with either the default verifier or the proposed numeric opt-in returns PARTIAL and three flag outcomes. Two claims require human review; one returns semantic verification unavailable. None matches the supported atomic fact grammar. Recorded delivery and category/state slices also contain small-sample warnings.

## Prompt preparation change

The analyst is instructed to keep revenue, orders and items percentage facts atomic and separate from association or interpretation. The grammar uses an optional exact category prefix, lowercase metric, fell/rose, reported percentage, and optional exact state suffix. Each fact must cite its own metric/week/filter evidence. Example values are explicitly illustrative, not defaults.

Warnings take precedence over compact wording. Review-score changes, unusualness, change shares and delivery associations are not silently recast as supported facts or discarded to get a complete badge. Their semantic review remains necessary.

This change does not alter confidence, quality thresholds, verifier defaults, human-review flags or historical captures. It prepares output for existing bounded verification; it does not establish live prompt compliance or authorize a new run.

## Independent review and acceptance needed

Review the exact numeric policy before using it outside its synthetic tests. Evaluate whether its arithmetic rounding intervals, all-citations scope, quality thresholds and confidence rule are appropriate for real golden-data outputs. Reviewers must reconcile postcheck confidence with verifier confidence and preserve every mandatory coverage requirement.

Full golden-data acceptance, independent code/policy review, browser layout review and freshly authorized corrected live validation remain outstanding. A COMPLETE numeric report would certify only its bounded selected facts under the configured policy; it would not prove causes, all prose or end-to-end usefulness to a real analyst.

Do not edit the saved S1 claims or suppress their review flags to fabricate a successful demo. No paid run, merge, public release or submission is authorized by this preparation.
