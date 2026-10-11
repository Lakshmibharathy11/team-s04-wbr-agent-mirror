# WBR Agent: Golden Dataset and Answer Keys

**Last updated:** October 7, 2026 · **Author:** Lakshmi Bharathy Kumar (L)

This document explains the golden dataset: test scenarios with known right answers, used to check that the tools and the agent find what they should and handle failures safely.

## Contents

1. [What it is and why](#1-what-it-is-and-why)
2. [How it is built](#2-how-it-is-built)
3. [Choosing calm weeks](#3-choosing-calm-weeks)
4. [The scenarios](#4-the-scenarios)
5. [Verifier test cases](#5-verifier-test-cases)
6. [Latest results on real data](#6-latest-results-on-real-data)
7. [Challenges and how we fixed them](#7-challenges-and-how-we-fixed-them)
8. [Limitations](#8-limitations)
9. [How to run it](#9-how-to-run-it)

---

## 1. What it is and why

On a normal week, nobody knows the "right answer" in advance, so you can't measure whether the agent is correct. The golden dataset solves that: we **plant** known problems into a copy of the data, so we know exactly what a correct agent should report.

It tests three things the hackathon judges check:

| Failure type | Covered by |
|---|---|
| A tool **fails** | S5 (timeout) |
| A tool returns **garbage** | S6 (impossible data), S7 (missing data) |
| The model **disagrees** with the data | Verifier test cases V1–V4 |

## 2. How it is built

`scripts/build_golden_dataset.py` does four things:

1. **Copies** the two summary tables (`weekly_metrics`, `weekly_category_metrics`) from `data/olist.duckdb` into `data/golden.duckdb`. The original is opened read-only and never changes.
2. **Plants** each scenario by changing specific rows in the copy.
3. **Writes the answer keys** to `tests/golden/answer_keys.json`, outside the database.
4. **Checks** that the tools detect every scenario, printing PASS or FAIL.

**Why the answer keys live outside the database:** if the answers were stored next to the data, the agent could read them, and the test would be meaningless. This is called **data leakage**.

**How planting stays realistic:** when a category cell is reduced, the same amounts are subtracted from that state's row in the state table, so both tables still agree, as real data would.

`golden.duckdb` is rebuilt from scratch on every run and is never committed to Git.

## 3. Choosing calm weeks

**The problem:** real data has its own unusual movements. If a scenario is planted in a week where something real also happened, the two compete, and the answer becomes unclear.

**The fix:** before planting, the script scans the **original** data and, for each of S1–S3, picks the most recent week where the slices it will be checked on are quiet:

| Scenario | Must be calm in the original data | Extra rule |
|---|---|---|
| S1 | No category, and no state within the planted category, with |z| ≥ 2.5 | The planted cell had at least 30 orders the week before |
| S2 | RJ's review score and late % with |z| < 1.5 | — |
| S3 | No state, and no category within MG, with |z| ≥ 2.5; no unusual worsening in MG late deliveries | — |

**Other rules:** only weeks with a full 8-week baseline; Black Friday and the week after are skipped; S1–S3 are at least 11 weeks apart and away from the fixed scenarios.

**Why the margins (2.5 instead of 3, 1.5 instead of 2):** a real movement just under the threshold could still compete with the planted one.

**Why 11 weeks apart:** so one planted week never falls inside another scenario's 8-week baseline, where it would distort what "normal" means.

The weeks chosen are recorded in `answer_keys.json`.

## 4. The scenarios

| ID | Type | Week | What is planted | What a correct agent reports |
|---|---|---|---|---|
| **S1** | Category drop | auto | health_beauty in SP falls 85% | health_beauty revenue fell, concentrated in SP |
| **S2** | Delivery and reviews | auto | RJ: late deliveries rise to 30%; reviews fall 0.8 points; revenue unchanged | An **observed pattern**, not a proven cause |
| **S3** | Unexplained drop | auto | MG falls 50% evenly across all categories; delivery rates unchanged | "Unexplained, needs analyst review" |
| **S4** | Incomplete week | 2018-08-20 | Nothing; real incomplete data | PARTIAL report, no conclusions |
| **S5** | Tool failure | 2018-07-02 | The review query is made to fail | Review unavailable; revenue and orders reported |
| **S6** | Garbage data | 2018-01-15 | PR reports more late orders than deliveries (impossible) | PR excluded; other states still reported |
| **S7** | Missing rows | 2018-04-23 | BA's row is deleted | BA value empty, not zero; total not reported as a drop |
| **S8** | Zero denominator | 2018-02-19 | SC has zero deliveries | Late % empty, never a made-up number |
| **S9** | Invalid agent input | — | Nothing; bad requests (unknown metric, Tuesday, outside window) | Validation error; the agent corrects itself |
| **S10** | Insufficient history | 2017-02-13 | Nothing; an early week | Change reported without calling it unusual |

## 5. Verifier test cases

For Franklyn's verifier: claims that look plausible but must be caught.

| ID | Bad claim | Expected outcome | Why |
|---|---|---|---|
| V1 | "Reviews fell in RJ **because** late deliveries rose." | Downgrade or flag | Causal wording; the data shows association only |
| V2 | "health_beauty revenue fell **95%** in SP." | Remove | The evidence shows about 85–89%; 95% appears nowhere |
| V3 | "Review score was stable." (when the review tool failed) | Remove | No evidence exists for any review claim |
| V4 | "Revenue fell **because** reviews fell." | Downgrade or flag | Both declined; no causal link established |

## 6. Latest results on real data

**18 of 18 checks passed.**

| Scenario | Check | Result |
|---|---|---|
| S1 | health_beauty ranks first among categories | −49.6%, z −2.67 ✓ |
| S1 | health_beauty drop unusual (single metric) | z −2.67 vs. threshold 2 ✓ |
| S1 | SP ranks first within health_beauty, largest share of its drop | −89.1%, 80% of the drop ✓ |
| S2 | RJ review drop unusual | −0.83 points, z −6.29 ✓ |
| S2 | RJ late delivery rise unusual | +26 points, z 5.8 ✓ |
| S3 | MG ranks first among states, largest share of the change | −61.3%, 79% of the change ✓ |
| S3 | MG drop unusual (single metric) | z −2.04 vs. threshold 2 ✓ (barely) |
| S3 | Drop is broad across MG categories | 11 categories fell more than 20% ✓ |
| S3 | No unusual delivery worsening in MG | z 1.06 ✓ |
| S4–S10 | Failure and edge cases | All ✓ |

**Reading a z-score:** the sign shows the direction (negative = drop); the size shows how unusual. A change is unusual when the size, ignoring the sign, is at least the threshold. So −2.04 is unusual (2.04 ≥ 2), but only just.

## 7. Challenges and how we fixed them

This took several rounds. Each failure taught us something about the data.

| Round | What failed | Cause | Fix |
|---|---|---|---|
| 1 | S1: housewares came out on top, not health_beauty | A **real** 43% housewares drop in the week of 2018-03-12 | Moved S1 to a calm week |
| 1 | S3: unusual delivery signal in MG | A **real** improvement in MG late deliveries in the week of 2018-08-13 | Check now looks only for delivery **worsening**, since an improvement can't explain a drop |
| 2 | S5: revenue came back partial | S7's missing row was in the week just before S5 | Moved S7 to 2018-04-23 |
| 3 | S1: SP's −79% drop not unusual (z −1.41) | Small cells swing wildly; a few wild weeks inflated the standard deviation | Switched to the **robust** z-score (median and MAD) |
| 4 | S1: SP's z −2.93 missed the new breakdown threshold of 3 | The stricter threshold for breakdowns | Planted drop increased from 70% to 85% |
| 5 | S3: "Other" states came out on top | A **real** 39% drop in the smaller states in the week of 2018-08-13 | — |
| 5 | Weeks reset to defaults | Replacing the script file overwrote hand-picked weeks | **Automatic calm-week selection** on the original data |
| 6 | S1 and S3: top groups correct, but z below 3 | Group revenue is very volatile; even a 61% drop reached only z −2.04 | Checks now match how the agent investigates: **detect** with a single-metric test (threshold 2), **attribute** by rank and share |

**Lessons:**
- Always check the **original** data before planting. Real data is rarely quiet.
- Testing many groups at once creates false alarms by chance (the multiple comparisons problem): a calm-week scan found an "unusual" group in 7 of 12 weeks.
- Revenue for a single state or category is volatile, so only large changes stand out there.

## 8. Limitations

- **Synthetic at the summary level:** planted numbers exist only in the weekly tables. They don't trace back to real orders in `order_facts`.
- **S3 passes narrowly** (z −2.04 vs. 2). A small change in data or settings could flip it.
- **Volatile group revenue:** the agent reliably detects only large drops in a single state or category. Smaller ones are better caught at the national level or with order counts.
- **Real anomalies found while scanning** are genuine findings, not test scenarios. Those seen so far in earlier runs: housewares −43% (2018-03-12), "Other" states −39% (2018-08-13), and an unusual improvement in MG late deliveries (2018-08-13). Add the list printed by the latest run.

## 9. How to run it

From the project root, with the virtual environment active and **no notebook connected to the database**:

```
python scripts/build_golden_dataset.py
```

**Output:** the calm weeks chosen, any real anomalies found while scanning, and PASS/FAIL for each check. It exits with an error code if any check fails, so it can be used in automated tests.

**Troubleshooting:**

| Message | Fix |
|---|---|
| "used by another process" | Close notebook connections (`con.close()` or restart the kernel) |
| "no calm week found" | Lower `MIN_CELL_ORDERS` (S1) or raise `BREAKDOWN_CALM` slightly |
| A check fails on new data | Read its detail line: usually a real movement or a too-small planted change |
