# WBR Agent: Stage 2 — Query Tools

**Last updated:** October 7, 2026 · **Author:** Lakshmi Bharathy Kumar (L)

This document explains the three query tools the analyst agent uses: what they do, how they work, how they handle problems, and the challenges we hit while building them.

## Contents

1. [What the tools do](#1-what-the-tools-do)
2. [Files](#2-files)
3. [The three tools](#3-the-three-tools)
4. [What every tool returns](#4-what-every-tool-returns)
5. [How "unusual" is decided](#5-how-unusual-is-decided)
6. [Safety and error handling](#6-safety-and-error-handling)
7. [Challenges and how we fixed them](#7-challenges-and-how-we-fixed-them)
8. [How to use the tools](#8-how-to-use-the-tools)
9. [Open items](#9-open-items)

---

## 1. What the tools do

The analyst agent never queries the database itself and never does math. It calls three tools. Each tool runs a fixed, pre-written SQL query, calculates the numbers in Python, and returns the result in a standard format.

**Why:** AI models can make arithmetic mistakes or invent numbers. Keeping every calculation in the tools means every number in the report comes from a real query.

## 2. Files

| File | Job |
|---|---|
| `tools/config.py` | All settings: allowed metrics, states, categories, analysis window, thresholds |
| `tools/envelope.py` | Builds the standard output format and enforces its rules |
| `tools/backend.py` | Runs SQL on DuckDB (local) or Athena (AWS), with retry and timeout |
| `tools/queries.py` | The fixed SQL templates |
| `tools/wbr_tools.py` | The three tools |

**Why split it this way:** each file has one job, so it is easy to test and review. Switching from DuckDB to Athena changes only one setting.

## 3. The three tools

| Tool | Question it answers | Inputs |
|---|---|---|
| `get_weekly_metric` | **What** changed, and is it unusual? | metric, week, optional state or category |
| `get_breakdown` | **Where** did it change? | metric, week, dimension (state or category), optional filter |
| `get_delivery_signals` | **What else** changed in the same place? | week, optional state and/or category |

**How they work together, an example:**

1. `get_weekly_metric("product_revenue", week)` → revenue fell 12%, unusual.
2. `get_breakdown("product_revenue", week, "state")` → SP accounts for most of the drop.
3. `get_breakdown("product_revenue", week, "category", state="SP")` → within SP, one category fell sharply.
4. `get_delivery_signals(week, state="SP")` → late deliveries rose in SP the same week.

The agent reports this as an **observed pattern**, never as a proven cause.

**Metrics available:** `product_revenue`, `orders`, `items`, `avg_order_value`, `items_per_order`, `avg_item_price`, `avg_review_score`, `one_star_pct`, `late_delivery_pct`, `avg_delivery_days`, and `cancellation_rate` (totals and states only).

**Breakdown extra:** for revenue, orders, and items, each group also gets `share_of_total_change_pct`: how much of the total change it accounts for. Shares can go above 100% when other groups move the opposite way.

## 4. What every tool returns

Every tool returns the same 9 fields, as agreed in the V1 integration contract:

| Field | Meaning |
|---|---|
| `tool_name` | Which tool |
| `request_id` | Unique ID for this call; report claims cite it as evidence |
| `status` | `ok`, `partial` (some values missing or not final), or `error` (nothing usable) |
| `generated_at` | When the result was produced |
| `parameters` | The inputs used, including the weeks compared, filters, and grain |
| `data` | The numbers |
| `evidence_refs` | Where the numbers came from: query ID, backend, table, row count, time (plus the Athena query ID on AWS) |
| `warnings` | Problems that don't stop the result, each with a code |
| `error` | What went wrong, when `status` is `error` |

**Rules enforced in code:** numbers stay numeric; an error never carries data; a successful result always carries evidence.

**Inside `data` for each series:**

| Field | Meaning |
|---|---|
| `current`, `previous` | This week's and last week's value |
| `change`, `change_type` | Percent change (`pct`) or difference in points (`points`) |
| `current_base`, `previous_base` | The count behind the value (orders, reviews, or deliveries) |
| `baseline` | What "normal" looks like: `weeks`, `method`, `center_change`, `spread_change` |
| `z_score`, `z_threshold`, `is_unusual` | How unusual the change is, and the bar it was judged against |

**Warning codes:**

| Code | Meaning |
|---|---|
| `SMALL_SAMPLE` | Fewer than 30 orders behind the number, or fewer than 10 events for a rate |
| `ZERO_DENOMINATOR` | A ratio could not be calculated (for example, zero deliveries) |
| `INCOMPLETE_WEEK` | The week is known to be incomplete (2018-08-20) |
| `NO_DATA` | Rows are missing for a compared week, or a state is missing from a total |
| `INSUFFICIENT_HISTORY` | Fewer than 8 earlier weeks, so no z-score |
| `INVALID_DATA` | Rows failed sanity checks and were excluded |

**Error types:** `validation` (bad input), `timeout`, `query_failed`, `malformed_data` (bad data).

## 5. How "unusual" is decided

### The weekly change

- **Percent change** for revenue, orders, items, and averages: (this week − last week) ÷ last week × 100.
- **Point change** for scores and rates: this week − last week. **Why:** late deliveries going from 6% to 15% is "+9 points," which is clearer than "+150%."

### The baseline

The 8 previous weekly changes for the same metric and group, inside the analysis window. **Why 8:** about two months, long enough to measure normal variation and recent enough to reflect current behavior.

### The robust z-score

z = (this week's change − median of the baseline) ÷ (1.4826 × MAD)

- **Median:** the middle value of the baseline.
- **MAD:** the median distance from the median.
- **1.4826:** makes MAD comparable to a standard deviation, so the usual thresholds keep their meaning.

**Simple meaning:** how surprising this week's change is, compared with how much this number normally moves. The sign shows the direction; the size shows how unusual.

**Why robust instead of the standard mean and standard deviation:** one or two wild weeks (common in small groups, or after Black Friday) inflate the standard deviation so much that a real drop looks normal. In one example, a 79% drop scored z = −1.5 with the standard method and z = −5.6 with the robust method.

### Two thresholds

| Where | Threshold | Why |
|---|---|---|
| Single metric (`get_weekly_metric`, `get_delivery_signals`) | |z| ≥ 2 | One test; about a 5% chance of a false alarm |
| Breakdown (`get_breakdown`) | |z| ≥ 3 | About 22 groups tested at once; with |z| ≥ 2, at least one would be flagged by chance about 68% of the time. 3 is roughly the Bonferroni-adjusted value |

### Detection vs. attribution

- **Detection** (did something unusual happen?) uses a single-metric z-score, threshold 2.
- **Attribution** (where did it come from?) uses the breakdown's **ranking** and **share of change**, not each group's flag.
- The breakdown threshold of 3 is for flagging groups as anomalies **on their own**, when scanning without a detected change.

**Why:** state and category revenue is very volatile week to week. In testing, a 61% drop in one state reached only z = −2.04. Requiring every group to clear 3 would hide real, large changes.

## 6. Safety and error handling

| Protection | How |
|---|---|
| No AI-written SQL | The agent only picks a tool and fills in inputs |
| Allowed lists | Every metric, state, category, and dimension is checked against `config.py`; anything else is rejected |
| Parameterized queries | Values are passed separately from the SQL, which blocks SQL injection |
| Read-only data | DuckDB is opened read-only; on AWS, a read-only IAM role |
| Valid weeks only | Mondays inside the window (2017-02-06 to 2018-08-13), plus the known incomplete week |
| Retry once | A failed query is retried once, then reported as an error |
| Timeout | 30 seconds per query |
| Sanity checks | Rows with negative counts, more late orders than deliveries, more 1-star reviews than reviews, or impossible review averages are excluded |
| Completeness | A total is not calculated if any state is missing that week |
| Never crash | Any unexpected problem becomes `status: error` |
| Small queries | Only the summary tables, only needed columns, only 10 weeks: far under Athena's 100 MB limit |

**Failure simulation for the demo:** set `WBR_SIMULATE_FAILURE=avg_review_score` and any query for that metric fails with a timeout. Nothing in the code needs to change.

## 7. Challenges and how we fixed them

| Challenge | What happened | Fix |
|---|---|---|
| Review coverage came back empty | The query didn't fetch the orders column | Review metrics now also fetch orders |
| A zero showed as "-0.0" | A rounding quirk | Zero is normalized to 0.0 |
| Misleading "not enough history" warning | Fired when a metric simply didn't vary | Only fires when there really are fewer than 8 earlier weeks |
| Two meanings of "evidence" | `get_evidence` sounded like `evidence_refs` | Renamed to `get_delivery_signals` |
| A tool could crash on bad data | Text where a number should be raised an error | Every tool catches it and returns `malformed_data` |
| Bad rows could hide inside totals | A corrupted state row would be summed with good ones | Each row is checked before summing |
| A missing state looked like a drop | The total quietly lost that state's numbers | Totals check that all 8 state groups reported |
| Real drops looked normal in small groups | Wild weeks inflated the standard deviation | Switched to the robust z-score |
| Too many false alarms in breakdowns | Testing about 22 groups made "unusual" common by chance | Separate threshold of 3 for breakdowns |
| Large real drops failed the strict threshold | Group revenue is volatile | Detection uses the single-metric test; attribution uses rank and share |
| "Cannot import" errors in the notebook | The notebook kept old versions of edited files | Restart the kernel, or use `%autoreload 2` |
| "Can't open a connection… different configuration" | The notebook had the database open for writing while the tools opened it read-only | Close the notebook connection, or open it with `read_only=True` |
| "Used by another process" | A notebook held the database while a script ran | Close notebook connections before running scripts |
| A new, empty database appeared | A path typo (`"data / raw "`) pointed to a different file | Separate paths for CSVs (`data/raw/`) and the database (`data/olist.duckdb`); stray copy deleted |

## 8. How to use the tools

From a notebook in `notebooks/` (connected read-only, or with no connection open):

```python
import sys, json
sys.path.insert(0, "..")
from tools.wbr_tools import get_weekly_metric, get_breakdown, get_delivery_signals

print(json.dumps(get_weekly_metric("product_revenue", "2018-06-04", state="SP"), indent=2))

r = get_breakdown("product_revenue", "2018-06-04", "state")
for g in r["data"]["groups"]:
    print(g["group"], g["change"], g["z_score"], g["share_of_total_change_pct"])

print(json.dumps(get_delivery_signals("2018-06-04", state="SP"), indent=2))
```

**Verified on real data:** `get_weekly_metric("product_revenue", "2018-06-04", state="SP")` returned 69,744.94 → 88,154.94 BRL and 492 → 655 orders, matching a direct SQL query exactly.

**Switching to Athena:** set `WBR_BACKEND=athena` once AWS access is set up. The Athena code is written but not yet tested.

## 9. Open items

- [ ] Test the Athena backend once AWS access works
- [ ] Check whether the week of 2018-08-13 is complete for smaller states (the "Other" group fell 39% while the total barely moved); if not, end the window at 2018-08-06
- [ ] Share real outputs, warning codes, error types, and `data` shapes with Franklyn
- [ ] Decide how `confidence` is set (could be based on |z| and sample size)
- [ ] Build the analyst agent loop: detect, then attribute
