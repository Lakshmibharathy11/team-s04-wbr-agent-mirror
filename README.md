# Team S04 — Weekly Business Review Agent

AWS Community Day Bay Area Hackathon 2026

## Goal
Build a trustworthy agent that turns Olist e-commerce data into a completed weekly business review for an operations analyst.

## V1
Compare two complete weeks across:
- Revenue
- Order count
- Average review score
- Category breakdown
- Customer-state breakdown

The final report must include supporting evidence, caveats, and actionable questions.

## Trust principles
1. Every number and conclusion is traceable to evidence, query, period, and filters.
2. Observations are separated from causes; correlation is not presented as causation.
3. Bad data or tool failure produces an honest partial report, never fabricated completion.

## Working ownership
- Franklyn Rosario, MBA-ITM — product scope, acceptance testing, demo story, AWS architecture, runner, Bedrock integration, verifier
- Lakshmi Bharathy Kumar — Olist exploration, metric definitions, Athena queries, weekly_metrics, planted scenarios, S3/Athena data setup, tool functions, analyst loop
- Maxime Gillot — implementation ownership pending confirmation

## Development workflow
- One branch per task
- Pull requests into `main`
- Another teammate reviews before merge
- Squash merge preferred
- No raw Olist data, AWS credentials, API keys, `.env` files, or secrets in Git

## AWS foundation
Region: `us-east-2`
- Source bucket: `aizoya-s04-wbr-source-704554708405`
- Athena results bucket: `aizoya-s04-wbr-athena-results-704554708405`
- Glue database: `s04_wbr`
- Athena workgroup: `S04-WBR`

## License
MIT

## Status
Private development repository. Public release and final submission are separate approval gates.


## Offline review and report demo

The private integration candidate includes the analyst-to-verifier bridge and report renderer. Use Python 3 in an isolated environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install duckdb pandas boto3 "strands-agents==1.58.1" pydantic "pytest==9.1.1"
.venv/bin/python scripts/verify_offline_review.py
.venv/bin/python scripts/build_offline_demo.py
```

Open `demo/review/index.html` locally. The builder reads saved captures and derives an explicitly synthetic failure example; it does not call a model or AWS. The report shows current verifier status, review findings and linked query evidence. Wrong-period captures withhold all claims. This is a review/demo preparation path, not a live evaluation.

The revised selected engineering suite passes 223 tests and 47 subtests. Exactly ten database-dependent checks are excluded until `data/golden.duckdb` is supplied. The founder also reproduced the earlier 187-test suite on his Mac. Desktop/mobile Chromium layout and link checks passed. Claude returned GO for offline integration of a071406. Full golden-data acceptance, corrected live validation and final factual/release review remain pending. See [the demo handoff](docs/DEMO_HANDOFF_2026-10-11.md) for recording instructions and current limits.

The database files are excluded from Git. Obtain the existing team's database with its checksum and build provenance before full data acceptance; this quickstart never downloads or invents it.

Independent Claude review found incomplete-report certification and an incorrectly excluded non-database test. The revised candidate keeps whole-report status PARTIAL until a trusted coverage validator exists; verified individual claims do not establish report completion. The proposed numeric policy remains disabled. See docs/VERIFIER_COMPLETION_REVIEW_2026-10-11.md.
