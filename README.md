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
