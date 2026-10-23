# Team S04 — Weekly Business Review Agent

AWS Community Day Bay Area Hackathon 2026 · Challenge: "Hand it off"

An AI agent that drafts the **weekly business review (WBR)** for an e-commerce operations analyst. It looks at what changed in revenue, orders, review scores, product categories and customer states, and at possible links between those changes. Every finding cites the query that produced it, and the agent says so when it does not know. The evidence shows only that changes happened together; it never shows that one caused another.

> **Status (code as of commit `ef19ac9`, October 11, 2026; checks re-run October 23, 2026):** prototype. The offline suite passes on Linux and Windows. Live model behavior after the latest fixes is **not yet verified**. All data is public Olist data, plus clearly labeled **synthetic** test scenarios we planted in it.

## The person and the task

- **Who:** an e-commerce operations analyst who prepares the weekly business review.
- **The task today:** each week they pull the numbers, compare them with the previous week, find what moved, look for possible links between the changes, and write the commentary by hand.
- **What it costs them today:** Not yet measured.

## What the agent does

1. **Input:** a reporting week, given as its Monday (for example `2018-08-13`). It is compared with the previous week (`2018-08-06`).
2. **Investigates:** a [Strands](https://strandsagents.com) agent on **Amazon Bedrock** (tested with Amazon Nova Micro, `us.amazon.nova-micro-v1:0`, in `us-east-2`; the model is set by `WBR_MODEL_ID`) follows a fixed four-turn plan (detect → attribute → drill down → check delivery signals). It can use only three read-only tools: `get_weekly_metric`, `get_breakdown` and `get_delivery_signals`. Each tool runs a fixed SQL template on **Amazon Athena** (or on local DuckDB for tests). The model only supplies parameters, which are checked before use. It never writes SQL.
3. **Drafts the report:** answers three fixed questions: why revenue changed, why review scores changed, and which product categories underperformed. Each claim cites the request IDs of the tool results it relies on.
4. **Checks itself:** code, not the model, assigns confidence and flags wording that suggests cause or containment. It also locks every query to the reporting week and rejects evidence from any other period.
5. **Verifies and renders:** a verifier checks each claim against the saved evidence. The HTML report shows every finding with its evidence, where the query came from, and a status badge. Reports are never marked COMPLETE: any successful run is **PARTIAL** (claims still need human review), and a run with evidence from the wrong period is **REJECTED**.

## Architecture

```
Reporting week ─► Analyst agent (Strands, Amazon Bedrock Nova Micro)
                    │  three read-only tools (week locked to the run)
                    ▼
                  Amazon Athena ─► AWS Glue Data Catalog ─► Amazon S3 (Parquet)
                    │
                    ▼  evidence envelopes (request IDs, periods, provenance)
                  Post-checks ─► Verifier ─► HTML report (PARTIAL / REJECTED)
```

**AWS services:** Amazon Bedrock (model inference), Amazon Athena (queries), Amazon S3 (data and query results), AWS Glue Data Catalog (table definitions). Access uses least-privilege IAM. The Athena workgroup stops any query that would scan more than 100 MiB.

**Cost and safety limits:** a budget ledger reserves the worst-case cost before every paid call. Each run also has limits on tokens, model calls, tool calls and time. The SDK never retries a call on its own. Evidence is saved after every run, and the run stops if its cost can't be accounted for.

## Quick start (offline, no AWS account needed)

Requires Python 3.12. Nothing in this section calls a model or AWS.

```bash
git clone https://github.com/aizoya/team-s04-wbr-agent.git
cd team-s04-wbr-agent
```

macOS / Linux:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install "duckdb==1.5.6" "pandas==3.0.6" "boto3==1.43.109" "strands-agents==1.58.1" "pydantic==2.13.5" "pytest==9.1.1"
.venv/bin/python scripts/verify_offline_review.py
.venv/bin/python scripts/build_offline_demo.py
```

Windows (PowerShell):

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install "duckdb==1.5.6" "pandas==3.0.6" "boto3==1.43.109" "strands-agents==1.58.1" "pydantic==2.13.5" "pytest==9.1.1"
.venv\Scripts\python scripts\verify_offline_review.py
.venv\Scripts\python scripts\build_offline_demo.py
```

These are the versions pinned in `requirements-lock.txt`, which also lists the notebook tools that this quick start does not need.

**`verify_offline_review.py`** blocks Python network calls and runs the offline review suite: **223 tests and 47 subtests** across 17 test files. It always skips the same **10 checks that need the golden database**, whether or not `data/golden.duckdb` is present, and it stops with an error if that list of skipped checks changes. Expected result: `223 passed, 10 deselected, 47 subtests passed`. Connections to your own machine (loopback) are allowed, because on Windows Python's asyncio needs one internally. All other connections are blocked.

**`build_offline_demo.py`** writes to `demo/review/` by default (you can change this with `--output DIR`). Open **`demo/review/index.html`** in a browser. It links three pages built from saved evidence:

| Page | What it shows |
| --- | --- |
| `captured-s1.html` | A saved AWS-backed run (Bedrock + Athena, `runs/eval_20261010_115418.json`). It finds the planted health_beauty drop in SP. Claims that need review stay flagged. Status **PARTIAL**. |
| `synthetic-failure.html` | The same run, but the review-score tool results are replaced with errors offline. The affected claims are held back. Status **PARTIAL**. This is an illustration, not an official S5 run. |
| `rejected-week.html` | A real failed run (`runs/eval_20261010_212231.json`) with 12 tool results for the wrong week. Its claims are held back. Status **REJECTED**. |

The builder only reads saved runs. It does not call a model or AWS.

## Data

The raw Olist data and the generated databases are **not in Git** (team rule).

- **Source:** the public [Olist Brazilian E-Commerce dataset](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce) (99,441 orders, 2016–2018).
- **Golden test database:** `data/golden.duckdb` adds clearly labeled synthetic scenarios to the real weekly data. `scripts/build_golden_dataset.py` builds it from `data/olist.duckdb`. The team's copy (SHA-256 `6b31182863c8b9b080c6995fcb322e3014196494e72414a6192bb075055a3b65`) is not included in this repository. The offline quick start and the demo don't need it.
- **Full suite:** if you have the golden database, run all 283 tests with `python -m pytest tests -q` and `WBR_BACKEND=duckdb` set.

**Planted scenarios:** all ten are synthetic, and their answer keys are in `tests/golden/answer_keys.json`:

| ID | Scenario | ID | Scenario |
| --- | --- | --- | --- |
| S1 | Category drop | S6 | Garbage data |
| S2 | Delivery and reviews | S7 | Missing rows |
| S3 | Unexplained drop | S8 | Zero denominator |
| S4 | Incomplete week | S9 | Invalid agent input |
| S5 | Tool failure | S10 | Insufficient history |

## Live runs on AWS

Live runs call Amazon Bedrock and Athena, which costs money. They need your own AWS account and resources, **plus a separate, explicit approval to spend**. They are not part of the quick start or the demo, so this README gives no instructions for them.

## Results and limitations

- **Offline:** the full suite passes **283 tests and 47 subtests**, run against the golden database (checksum verified) with networking blocked. This was confirmed on Linux and on Windows (Python 3.12.7, October 23, 2026). Desktop and mobile layout checks passed.
- **Live (Bedrock Nova Micro + Athena):** an earlier run found the planted S1 drop (health_beauty → SP) and passed its scenario check. Under the current, stricter verifier it is **PARTIAL**, because its claims still need human review. A later run failed S1: the model queried the wrong week (2018-08-20 instead of 2018-08-13). That saved run is now **REJECTED** when replayed, and the tools now lock every query to the reporting week. No live run has been made since these fixes. A six-scenario batch has been planned (upper bound about $0.40) but not run.
- **Data parity:** in a saved comparison on October 9, 2026 (`runs/athena_parity_20261009_103840.json`), the tools returned matching results on DuckDB and Athena in 13 of 13 test cases. That comparison ran 11 Athena queries that scanned about 0.6 MB in total. It doesn't show that the two backends always agree.
- **Not yet verified:** how the agent behaves live after the week-locking and wording fixes; whether a whole report covers everything it should (reports stay PARTIAL until a trusted coverage check exists); how much time it saves a real analyst.
- **Known limits:** some planted scenarios overlap with the comparison weeks of other scenarios (documented). The verifier flags every claim for human review. The agent reports only changes that happen together, never causes.

## What we would build next

A newly approved, cost-limited live test of the corrected agent; a trusted check that each report covers everything it should; testing with a real analyst to measure how often they accept the report and how much time it saves; and running it automatically every week with Amazon EventBridge.

## Team

- **Lakshmi Bharathy Kumar**: data, metric definitions, Athena tables, planted scenarios, query tools, analyst agent
- **Franklyn Rosario, MBA-ITM**: product scope, AWS architecture, acceptance testing, runner, verifier, renderer, demo
- **Maxime Gillot**: team member

## License

MIT — see [LICENSE](LICENSE).
