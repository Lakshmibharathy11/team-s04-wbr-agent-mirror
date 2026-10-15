# Team S04 - ” Weekly Business Review Agent "

AWS Community Day Bay Area Hackathon 2026 Â· Challenge: "Hand it off"

An AI agent that drafts the **weekly business review (WBR)** for an e-commerce operations analyst: it investigates changes in revenue, orders, review scores, product categories and customer states, and possible associations between them, links every finding to the query that produced it, and says so when it does not know. Evidence shows association only; it does not establish cause.

> **Status (October 11, 2026):** prototype. The offline suite passes; corrected live model behavior is **not yet verified** (see [Results and limitations](#results-and-limitations)). All data is public Olist data with labeled, planted **synthetic** test scenarios.

## The person and the task

- **Who:** an e-commerce operations analyst who prepares the weekly business review.
- **The task today:** each week they pull the numbers, compare them with the previous week, find what moved, investigate changes and possible associations, and write the commentary by hand.
- **What it costs them today:** Not yet measured.

## What the agent does

1. **Input:** a reporting week (a Monday, e.g. `2018-08-13`).
2. **Investigates:** a Strands agent on **Amazon Bedrock (Nova Micro)** follows a four-step plan (detect â†’ attribute â†’ drill down â†’ check delivery signals) using three allowlisted, read-only tools: `get_weekly_metric`, `get_breakdown`, `get_delivery_signals`. The tools run fixed SQL templates on **Amazon Athena**; the model only fills in validated parameters and never writes SQL.
3. **Drafts the report:** covers three fixed topics (revenue changes, review-score changes and underperforming categories), reporting observed changes and possible associations; each claim cites the request IDs of its evidence.
4. **Checks itself:** code (not the model) assigns confidence, flags causal or containment wording, binds every query to the reporting week, and rejects evidence from any other period.
5. **Verifies and renders:** a verifier checks claims against captured evidence; the HTML report shows each finding with its evidence, query provenance and a status badge. Failed or missing evidence yields a **PARTIAL** report; wrong-period evidence is **REJECTED**.

## Architecture

```
Reporting week â”€â–º Analyst agent (Strands, Amazon Bedrock Nova Micro)
                    â”‚  allowlisted, read-only tools (week bound to the run)
                    â–¼
                  Amazon Athena â”€â–º AWS Glue Data Catalog â”€â–º Amazon S3 (Parquet)
                    â”‚
                    â–¼  evidence envelopes (request IDs, periods, provenance)
                  Post-checks â”€â–º Verifier â”€â–º HTML report (PARTIAL / REJECTED / findings)
```

**AWS services:** Amazon Bedrock (model inference), Amazon Athena (queries), Amazon S3 (data and query results), AWS Glue Data Catalog (table definitions). Access is least-privilege IAM; the Athena workgroup enforces a per-query scan limit.

**Guardrails:** budget ledger reserving worst-case cost before every paid call; token, model-call, tool-call and time limits; no SDK retries; evidence saved after every run; halts on any accounting uncertainty.

## Quick start (offline, no AWS account needed)

Requires Python 3.12. Nothing in this section calls a model or AWS.

```bash
git clone https://github.com/lakshmibharathy11/team-s04-wbr-agent-mirror.git
cd team-s04-wbr-agent
python3 -m venv .venv
```

macOS / Linux:

```bash
.venv/bin/python -m pip install duckdb pandas boto3 "strands-agents==1.58.1" pydantic "pytest==9.1.1"
.venv/bin/python scripts/verify_offline_review.py
.venv/bin/python scripts/build_offline_demo.py
```

Windows (PowerShell):

```powershell
.venv\Scripts\python -m pip install duckdb pandas boto3 "strands-agents==1.58.1" pydantic "pytest==9.1.1"
.venv\Scripts\python scripts/verify_offline_review.py
.venv\Scripts\python scripts/build_offline_demo.py
```

Then open **`demo/review/index.html`** in a browser. It shows three pages built from saved evidence:

| Page | What it shows |
| --- | --- |
| Saved S1 capture | A saved AWS-backed run (Bedrock + Athena): the planted health_beauty drop located in SP; claims needing review stay flagged; status **PARTIAL** |
| Synthetic tool failure | An offline example where review-score tool results are replaced with errors; affected claims withheld; status **PARTIAL** |
| Wrong-week capture | A real failed run whose tool results were for the wrong week; claims withheld; status **REJECTED** |

The builder reads saved captures only; it does not call a model or AWS.

`verify_offline_review.py` runs the selected offline review suite: **223 tests and 47 subtests**, and it **always deselects ten database-dependent checks**, whether or not `data/golden.duckdb` is present. The full suite (283 tests and 47 subtests) was run separately with the checksum-verified golden database and networking blocked.

## Data

Raw Olist data and the generated databases are **not in Git** (team rule).

- **Source:** the public [Olist Brazilian E-Commerce dataset](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce) (99,441 orders, 2016â€“2018).
- **Golden test database:** `data/golden.duckdb` adds labeled, planted synthetic scenarios to the real weekly data; `scripts/build_golden_dataset.py` builds it from `data/olist.duckdb`. The team's copy (SHA-256 `6b31182863c8b9b080c6995fcb322e3014196494e72414a6192bb075055a3b65`) is not distributed in this repository. The offline quick start and demo above do not require it.

**Planted scenarios** (all synthetic, with answer keys in `tests/golden/answer_keys.json`): category drop (S1), delivery and reviews pattern (S2), unexplained drop (S3), incomplete week (S4), tool failure (S5), garbage data (S6), missing rows (S7), zero denominator (S8), invalid input (S9), insufficient history (S10).

## Live runs on AWS

Live runs call Amazon Bedrock and Athena and incur cost. They require your own AWS account and resources **and a separate, explicit spending approval**, and are not part of the quick start or the demo. No live run instructions are included here.

## Results and limitations

- **Offline:** full Linux offline suite passes **283 tests and 47 subtests** using the checksum-verified golden database. Desktop and mobile presentation checks passed.
- **Live (Bedrock Nova Micro + Athena):** an earlier run located the S1 planted drop (health_beauty â†’ SP) and passed its scenario grader; under the current, stricter verifier it is **PARTIAL** because claims still require human review. A later run failed S1: the model queried the wrong week (2018-08-20 instead of 2018-08-13). That capture is now **REJECTED** at replay, and the tools now bind every query to the reporting week.
- **Data parity:** in a saved comparison on October 9, 2026, the tools returned matching results on DuckDB and Athena in 13 of 13 test cases. This is not a claim of universal equivalence.
- **Not yet verified:** corrected live behavior after the week-binding and wording fixes; whole-report completeness (reports stay PARTIAL until a trusted coverage validator exists); real-user time savings.
- **Known limits:** some planted scenarios overlap each other's baseline windows (documented); the verifier flags all claims for human review; the agent shows association only, never causation.

## What we would build next

A freshly authorized, bounded live validation of the corrected agent; a trusted report-coverage validator; confirmation with a real analyst and measurement of report acceptance and time saved; a weekly schedule (Amazon EventBridge).

## Team

- **Lakshmi Bharathy Kumar** - data, metric definitions, Athena tables, planted scenarios, query tools, analyst agent
- **Franklyn Rosario, MBA-ITM** - product scope, AWS architecture, acceptance testing, runner, verifier, renderer, demo
- **Maxime Gillot** - team member

## License

MIT - see [LICENSE](LICENSE).
