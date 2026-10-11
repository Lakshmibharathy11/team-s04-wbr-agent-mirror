# WBR Agent: Agent Evaluation

**Last updated:** October 7, 2026 · **Author:** Lakshmi Bharathy Kumar (L)

This document explains how the analyst agent is evaluated against the golden scenarios: what is graded, why, how the graders were themselves tested, and how to run it.

## 1. What this evaluation answers

| Check | Question | Where |
|---|---|---|
| Tool checks | Do the tools detect each planted scenario? | `scripts/build_golden_dataset.py` (18 checks) |
| Verifier | Is every claim honest to the evidence? | Franklyn's verifier, every run |
| **Agent evaluation** | **Does the agent reach the right conclusions?** | **`scripts/evaluate_agent.py`** |

The tools can work perfectly and the agent can still miss the answer, for example by never drilling into the right category. This evaluation measures that.

## 2. Design choices

**Grade outcomes, not exact steps.** Each grader checks what the report concludes: the right state and category, the right claim type, the right report status. It does not require a specific sequence of tool calls. **Why:** current industry guidance finds that requiring exact tool sequences is too rigid, because agents often find valid paths designers didn't anticipate.

**Light process checks, reported but not graded:** tool calls used, staying within budget, blocked calls, and which tools were used.

**Several trials per scenario.** A real model doesn't answer identically every run, so one run proves little. Three summary numbers per scenario:

| Measure | Meaning | Question it answers |
|---|---|---|
| **Pass rate** | Share of trials that passed | How often does it succeed? |
| **pass@k** | At least one of k trials passed | Can it do this at all? |
| **pass^k** | All k trials passed | Does it do this reliably? |

**Code-based graders.** Checks are deterministic code, not another model judging. **Why:** fast, free, and reproducible.

**Transcripts kept for failures.** Every failed trial saves the full transcript, so a failure can be read and diagnosed, and an unfair grader can be caught.

## 3. What each scenario checks

| Scenario | Passes when |
|---|---|
| **S1** Category drop | A claim names health_beauty; a claim locates it in SP; that claim cites valid evidence |
| **S2** Delivery and reviews | A Q2 claim names RJ; the link is labeled `observed_pattern`; no causal wording |
| **S3** Unexplained drop | A claim names MG; it is labeled `unexplained` and flagged for human review; no invented delivery pattern |
| **S4** Incomplete week | The report is PARTIAL |
| **S5** Tool failure | The report is PARTIAL; the failure is listed as unavailable; no claim cites the failed call |
| **S7** Missing rows | The report is PARTIAL |

**Every scenario also requires no honesty issues:** no unknown evidence, no claims without evidence, no claims citing a failed call, and no causal wording.

**Name matching:** states can be written as codes or full names (SP or São Paulo); categories match regardless of underscores or spacing (health_beauty or health beauty). The system prompt also asks the agent to use the tools' names.

**Not included:** S6, S8, S9, and S10 test specific tool behaviors (garbage rows, zero denominators, invalid inputs, early weeks) and are already covered by the golden dataset's tool checks.

## 4. Testing the graders

A grader that passes everything, or fails everything, is useless. So the graders were tested before use, with two scripted analysts running through the real agent loop on the golden database:

| Scripted analyst | Behavior | Expected | Result |
|---|---|---|---|
| **Good** | Finds the right answers, labels patterns correctly, never claims a cause | Passes all 6 scenarios | ✓ All passed |
| **Bad** | Wrong category, causal wording, an invented pattern, a claim built on a failed call, a report that never checks the data | Fails all 6 scenarios | ✓ All failed |

## 5. Changes made while building this

| Change | Why |
|---|---|
| A report is now PARTIAL when a result had missing or invalid data | S7 (a state missing from a total) should produce a PARTIAL report, but the old rule only covered failures and the incomplete week |
| The system prompt asks the agent to use the tools' names for states and categories | Consistent names make reports clearer and grading reliable |

## 6. How to run it

**Grader tests (offline, no Bedrock):**

```
python -m pytest tests/ -q
```

All 9 tests should pass: 7 for the agent loop and 2 for the graders.

**Live evaluation on Bedrock** (once access works; needs `data/golden.duckdb`, `WBR_MODEL_ID`, and AWS credentials):

```
python scripts/evaluate_agent.py                     # all scenarios, 3 trials each
python scripts/evaluate_agent.py --trials 5 --scenarios S1 S2
```

**Output:** PASS or FAIL per trial with the failed checks, a summary table, and `runs/eval_<time>.json` with every report and the transcripts of failed trials.

**Cost note:** each trial is a full agent run with many model calls. Start with 3 trials and a cheaper model to check the setup, then run more trials for the final numbers.

## 7. Open items

- [ ] Run the live evaluation once Bedrock access works, and record the results here
- [ ] Read the transcripts of any failures before changing the prompt or the graders
- [ ] Optionally add a model-based grader for statement quality (for example, clarity of actionable questions), calibrated against human review
