# AWS S04 — reconciled offline candidate

Prepared for Franklyn Rosario, MBA-ITM, October 11, 2026 Pacific.

OFFLINE REVIEW CANDIDATE. Private review branch preparation is authorized. No merge, paid run, deployment, public release, submission or teammate message is authorized by this candidate. The prior paid approval remains consumed. This version supersedes the earlier packet.

Lakshmi's branch was checked twice: HEAD is eb8a05b7e3c09279c0db4c845256b01dc4665e65. This delta preserves her week-override audit log, wording checks, prompts and viewer. Runner source remains pinned to 0b2a757. Source was obtained using authenticated file reads, not a full Git checkout. Baseline bytes match Git blob hashes.

| Reproduced gap | Candidate behavior |
| --- | --- |
| Wrong-week postcheck leaves draft COMPLETE and confidence medium | Mismatched periods lower cited confidence and force PARTIAL, including uncited bad evidence |
| Missing comparison week accepted on success | Successful/partial results require run and previous comparison week; failed calls may omit comparison week |
| Direct adapter bypasses replay guard | Adapter checks periods before every verifier and removes affected claims |
| Runner wording guard narrower than analyst checks | Tentative causal wording is guarded at runner and adapter boundaries |
| Group containment can pass a custom verifier | Adapter flags containment wording for human review; lexical ambiguity is not proof of contradiction |
| Viewer prints draft without current verification status | Viewer shows current bridge status and actual tool weeks; rejected captures withhold claims |

187 tests passed, plus 43 subtests. Eleven database-dependent tests were deselected, including six viewer data cases in addition to the previous five. Full golden-data integration is NOT RUN here because data/golden.duckdb is unavailable. Lakshmi's stated 102 offline passes are reported in her commit, not represented as reproduced in full here.

Environment: Python 3.12, Strands 1.58.1, boto3/botocore 1.43.109, DuckDB 1.5.6, pytest 9.1.1. New period tests block sockets and use synthetic tools or saved captures. Broader tests are model-free. No new live performance or cost result is claimed.

Reproduce the limited review suite from the private branch (with dependencies installed):

```bash
git switch fix/s04-reconciled-safeguards-20261011
python scripts/verify_offline_review.py
```

The launcher forces local settings before imports, supplies inert AWS credentials, disables pytest plugin autoload, and blocks Python socket connection, DNS and datagram calls for the entire selected suite. It accepts no arguments, so additional tests cannot accidentally enable a live evaluation. This is an in-process guard for this model-free suite, not an operating-system network sandbox for arbitrary subprocesses. The exact excluded test names are recorded in the script. A successful command does not certify the full database suite, scenario acceptance, cost or live model behavior.

The patch was applied to an independently reconstructed exact baseline and compared byte-for-byte with the tested candidate. The private review branch brings together the pinned runner source and its regression fixtures with Lakshmi’s latest analyst revision. A legacy adapter success fixture now explicitly records both weeks; incomplete period metadata never counts as verified completion.

Next: independent teammate review, then full local golden-data and bridge suites using Lakshmi's existing database. A new paid evaluation requires explicit bounded authorization. Publication and submission remain separate gates.

Demo: the earlier S1 capture from 0bed8a6 passed its original scenario grader but remains PARTIAL under current conservative claim verification. The failed af8fca9 S1 is rejected for its 12 off-week results, and the viewer withholds claims. An offline S5-style example is synthetic and does not count as a new official S5 evaluation.

Suggested narration: “This captured run passed its scenario check while the report still requires claim review. A later test exposed a wrong-week failure. We reproduced it offline and enforced the run week at the tools and verification boundary.”

Lexical checks conservatively route uncertain wording for review. They do not verify arbitrary prose or establish causation. Full golden-data integration, independent review and a newly authorized live evaluation remain pending. Nothing certifies READY_TO_SUBMIT or SUBMITTED.
