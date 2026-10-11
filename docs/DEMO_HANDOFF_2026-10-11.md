# S04 renderer and demo handoff — October 11, 2026

Private review candidate. No public release, merge, paid evaluation, video upload, roster change or final submission was performed.

## Coverage and current evidence

Maxime's renderer work is being covered by Franklyn's integration workstream. At the October 11 check, Slack showed no reply to Franklyn's October 10 handoff messages; Maxime's only visible message after October 1 was October 4. No PR was authored by MaximeGillot5 and no commits attributed to that account were found across the seven visible repository branches. This does not establish that he has no unpublished work, and does not change team membership or contribution credit. Preserve any work he later provides.

`src/report_renderer.py` takes the shared runner's verifier output. Confirmed findings need a keep outcome, usable evidence, the correct period and no human-review flag. Flagged/downgraded findings stay visibly unconfirmed. Missing or failed citations withhold claim text. Any off-period evidence rejects presentation. Removed claim text, raw draft and transcript are never copied into the HTML. Dynamic text is escaped; no remote assets or JavaScript are included.

The renderer presents recorded metric values and query provenance. It does not independently establish query authenticity, arbitrary prose truth or causation. Whole-report COMPLETE is deliberately withheld. Kept individual claims do not establish mandatory report coverage or general model reliability.

## Run offline

From the project root, after dependencies are installed:

```bash
.venv/bin/python scripts/verify_offline_review.py
.venv/bin/python scripts/build_offline_demo.py
```

Open `demo/review/index.html` in a browser. The builder only reads committed captures and writes HTML; it does not invoke the analyst model, Athena, Bedrock or data queries. Use a fresh output folder for each review if preserving prior generated files. Generated pages are local preparation artifacts, not submission evidence of a new run.

| Page | Meaning | Expected badge |
| --- | --- | --- |
| captured-s1.html | Earlier 0bed8a6 S1 capture passed its original grader; rechecked with the current conservative bridge | PARTIAL |
| synthetic-failure.html | Derived example changes recorded review-score tool results to errors; not an official S5 evaluation | PARTIAL |
| rejected-week.html | Original failed S1 retains its 12 off-week tool results; all claims withheld | REJECTED |

No fully verified captured happy path is available here. The synthetic failure example demonstrates the presentation/verification failure path only; it must not be described as a new live tool failure. Keep the source version and execution mode visible during recording.

## Three-minute recording plan

| Time | Screen | Narration |
| --- | --- | --- |
| 0:00–0:25 | Title / target role | “Our weekly business review agent is built for an operations analyst reviewing Olist e-commerce results: revenue, orders, review scores, categories and customer states.” Use an actual confirmed user/workflow in the final writeup; no measured savings are claimed. |
| 0:25–0:50 | Architecture | “The analyst plans an investigation and calls allowlisted Athena tools. The tools bind every query to the requested week. The verifier checks captured evidence before the report renderer displays findings.” |
| 0:50–1:35 | Earlier S1 page | “This is a saved AWS-backed capture using a planted scenario. It passed the original scenario grader. The current bridge still marks the report partial where claims need review. Follow a finding to its request, week, filters and query provenance.” |
| 1:35–2:10 | Synthetic failure page | “This offline example deliberately replaces review-score results with tool failures. It is not a new paid S5 run. Missing evidence withholds affected claims and keeps the report partial.” |
| 2:10–2:40 | Rejected page | “A later run exposed a week mismatch: the report requested August 13, but all 12 results described August 20. The corrected boundary rejects the saved capture and withholds its claims.” |
| 2:40–3:00 | Limits / next step | “197 model-free tests and 43 subtests pass in the engineering environment. Eleven database-dependent checks remain unrun here. The founder's earlier 187-test suite also passed on his Mac. Full golden-data acceptance and a freshly authorized live test are next.” |

Rehearse this as an honest engineering demo; do not call it submission-ready until required acceptance, review and release gates are resolved. Recording and uploading the actual video remain outstanding.

## Submission requirements checked against primary organizer pages

Checked October 11, 2026. Participant guide: https://www.aws-aicd.com/hackathon/ ; event listing: https://luma.com/acba26102 . Guide says October 11, 11:59 PM PT; Luma still says 11:59 AM PT. Keep the earlier internal target until the discrepancy is resolved. Do not silently extend the deadline.

| Required item | Current preparation | Remaining action |
| --- | --- | --- |
| Specific real role/task and current manual cost | Operations analyst role is scoped; measured time/cost and actual user identity are not verified | Confirm role/user and present unmeasured cost honestly |
| External tool plus at least two AWS services, one AI | Saved captures record Bedrock/Nova and Athena; S3/Glue architecture is documented | Validate corrected live version after independent review and fresh bounded run authorization |
| Public GitHub repo, MIT or Apache 2.0, runnable README | Repo has MIT license and private review branch | Review stack, data/security/history and setup; public release requires founder authorization; verify logged-out access afterward |
| Three-minute video with failure path, unlisted YouTube or Loom | Recording plan and local report pages prepared | Record, review and authorize upload; synthetic example must stay labeled |
| One-page writeup using organizer Word template, exported PDF | Content draft in SUBMISSION_CONTENT_DRAFT.md | Obtain exact template, fill all sections, render and review final PDF |
| Roster with names and emails; lead submits | Names known; email addresses/lead assignment not verified here | Confirm active roster, addresses and submission lead; do not remove Maxime merely from inactivity |

The guide also requires AWS Community Day registration and in-person attendance for finalists. Attendance/registration is not verified here.

## Review gates

197 tests and 43 subtests pass using the network-blocked launcher. Eleven golden-database tests remain excluded. Browser visual QA is NOT RUN: the environment lacks Chromium, and the browser download returned an invalid archive. HTML content, escape behavior, citation links and failure states are tested; desktop/mobile layout still needs a browser check. Independent teammate review, full golden-data/bridge integration and corrected live evaluation remain pending. No claim of READY_TO_SUBMIT is made.

If Maxime returns, useful remaining bounded work includes independent renderer review, browser/device QA, recording rehearsal, or writeup/template formatting. Any code he brings should be reviewed against the current candidate rather than discarded.

## Final preparation checkpoint

The executable source at a071406 received Claude GO for offline integration only. The selected network-blocked suite was reproduced: 223 tests and 47 subtests passed, with exactly ten database-dependent checks excluded. Chromium 153 checks at 1440×900 and 390×844 loaded all four pages without horizontal overflow or broken anchors; index navigation, claim evidence links and query-provenance expanders worked at both widths. Warning headers are readable but lengthy on mobile; scroll to the review findings during recording. No verifier policy or executable source was changed by this documentation checkpoint.

Lakshmi has been asked to own both video recording and final submission, with explicit acceptance and ETAs required. Her response and golden.duckdb handoff remain pending. The exact checklist and provisional recording package are prepared. Real-user/manual-cost facts, release and submission approvals, the golden checks and corrected live validation remain open. A delegated submission owner must reconcile the organizer requirement that the team lead submit.
