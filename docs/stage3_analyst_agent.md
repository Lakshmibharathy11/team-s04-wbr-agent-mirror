# WBR Agent: Stage 3 — Analyst Agent Loop

**Last updated:** October 7, 2026 · **Author:** Lakshmi Bharathy Kumar (L)

This document explains the analyst agent: how its loop works, how it was designed against current industry practice, what it returns, how it is tested without AWS, and the challenges we hit.

## Contents

1. [What the analyst agent does](#1-what-the-analyst-agent-does)
2. [Design principles](#2-design-principles)
3. [The loop, step by step](#3-the-loop-step-by-step)
4. [Files](#4-files)
5. [What it returns](#5-what-it-returns)
6. [Safety: steering, budget, and post-checks](#6-safety-steering-budget-and-post-checks)
7. [Testing without AWS](#7-testing-without-aws)
8. [Challenges and how we fixed them](#8-challenges-and-how-we-fixed-them)
9. [How to run it](#9-how-to-run-it)
10. [Open items](#10-open-items)

---

## 1. What the analyst agent does

Given a week, the agent investigates three fixed questions and writes a report draft:

- **Q1.** Why did revenue change?
- **Q2.** Why did review scores change?
- **Q3.** Which product categories underperformed, and why?

It is built with **Strands Agents** on **Amazon Bedrock**. The model decides which tool to call next; the tools do all calculations; code checks the result.

**In one line:** the model **decides**, the tools **calculate**, the evidence log **remembers**, and code **checks**.

## 2. Design principles

The design follows current guidance from Anthropic's engineering team and AWS (Strands, AgentCore):

| Principle | What it means | How we apply it |
|---|---|---|
| **Simplest design that works** | Use fixed code where the path is known; use an agent only where the path depends on the data | The runner does fixed steps (data check, verification, rendering); the agent does only the investigation |
| **Context engineering** | Give the model the smallest set of useful information | The model sees a concise view of each tool result; the full result goes to the evidence log |
| **Tools designed for agents** | Clear descriptions, structured output, actionable errors | Tool docstrings explain when to use each; errors list allowed values |
| **Steering** | Just-in-time guidance at decision points instead of a long prompt | Hooks enforce the budget and block repeated calls; tool results carry guidance when partial or failed |
| **Budgets** | Cap work per run | 25 tool calls and 40 model turns per run |
| **Code over self-assessment** | The model does not rate its own confidence | Confidence and causal permissions are set by code from the evidence |
| **Outcome-based evaluation** | Grade what the agent concludes, not an exact sequence of calls | Golden answer keys check findings; process checks stay light |

## 3. The loop, step by step

1. **The runner starts the agent** with a week.
2. **The model reads its instructions:** the three questions, the method, and the rules.
3. **The model picks a tool,** for example total revenue for that week.
4. **The tool runs.** The full result is saved in the evidence log; the model receives a concise view.
5. **The model reads the result and decides the next step,** for example breaking the change down by state.
6. **Steps 3–5 repeat.** Each answer shapes the next question. This repetition is the loop.
7. **The loop stops** when the model has answered all three questions, or the budget runs out.
8. **The model writes the report draft** in a fixed structure (structured output).
9. **Code checks the draft** and adds the remaining contract fields.
10. **The result returns to the runner,** which sends it to the verifier.

**The investigation method in the prompt:**

| Step | What the agent does | Tool |
|---|---|---|
| **Detect** | Is the headline change unusual? (single metric, |z| ≥ 2). For revenue: fewer orders or smaller orders? | `get_weekly_metric` |
| **Attribute** | Where did it happen? Rank and share of change, by state and category, then drill into the top group | `get_breakdown` |
| **Check signals** | What else changed in the same place? | `get_delivery_signals`, review metrics |

If a change is not unusual, it is reported as within the normal range. If no group stands out, the change is broad. If nothing lines up with a located change, it is `unexplained`.

## 4. Files

| File | Job |
|---|---|
| `agent/analyst.py` | `run_analyst(week)`: builds the agent, runs the loop, returns the result |
| `agent/analyst_tools.py` | Strands `@tool` wrappers around the three query tools |
| `agent/run_context.py` | Per-run evidence log, call history, and budget |
| `agent/views.py` | Concise views of tool results for the model, with guidance |
| `agent/steering.py` | Steering hooks: budget and repeated-call blocking |
| `agent/prompts.py` | The short system prompt |
| `agent/schema.py` | The structure the model must return (Pydantic) |
| `agent/postchecks.py` | Code-side checks and contract fields |
| `agent/testing.py` | A scripted stand-in model for offline tests |
| `scripts/run_analyst.py` | Run one week on Bedrock and save the result |
| `tests/test_agent_offline.py` | Seven offline tests |

## 5. What it returns

```json
{
  "run": {"status": "ok", "tool_calls": 9, "budget": 25, "blocked_calls": [], "seconds": 41.2, "backend": "duckdb"},
  "report_draft": {
    "week": "2018-06-04",
    "compare_week": "2018-05-28",
    "claims": [ ... ],
    "actionable_questions": [ ... ],
    "unavailable": [ ... ],
    "draft_status": "COMPLETE",
    "precheck_issues": [ ... ]
  },
  "evidence_log": {"req-…": { full tool envelope }, ...},
  "transcript": [ ... every model step and tool call, in order ... ],
  "calls": [ ... the tool calls with status and warnings ... ]
}
```

**Each claim** follows the V1 contract:

| Field | Set by | Meaning |
|---|---|---|
| `claim_id` | Code | `c1`, `c2`, … |
| `question_id` | Model | Q1, Q2, or Q3 |
| `statement` | Model | The sentence for the report |
| `claim_type` | Model | `fact`, `observed_pattern`, or `unexplained` |
| `supporting_evidence` | Model | The `request_id`s it relies on |
| `confidence` | **Code** | `low`, `medium`, or `high` (rule below) |
| `causal_language_allowed` | **Code** | Always `false` for now |
| `requires_human_review` | **Code** | True for patterns, unexplained changes, low confidence, or any pre-check issue |

**Confidence rule (preliminary, to confirm with the team):**
- **low:** any cited result has a data-quality problem (error, partial, small sample, insufficient history, invalid or missing data, incomplete week). For a breakdown, only the top group's own warnings count.
- **high:** the strongest cited signal has |z| ≥ 3.
- **medium:** otherwise.

**Other fields:**
- **`unavailable`:** what could not be checked. Code adds every failed tool call, even if the model forgot.
- **`draft_status`:** `PARTIAL` if anything is unavailable, the week is incomplete, or a result had missing or invalid data (for example, a state missing from a total); otherwise `COMPLETE`.
- **`precheck_issues`:** problems found by code, for the verifier. Code flags but does not remove claims; removal is the verifier's decision.
- **`transcript`:** the full sequence of steps, for debugging and for reading failed evaluations.

## 6. Safety: steering, budget, and post-checks

**Steering hooks** (run before each tool call):

| Situation | What happens |
|---|---|
| The budget (25 calls) is used up | The call is cancelled with: "Tool budget reached… write the report with the evidence you already have." |
| An identical call is repeated | The call is cancelled and the agent is pointed to the earlier `request_id` |

**Guidance inside tool results:**
- **error:** "This call failed. Do not report any value from it. Add it to 'unavailable'."
- **partial:** "Some values are missing or not final. Say so in any claim that uses this result."

**Turn cap:** at most 40 model turns per run (Strands `Limits`). If the cap is reached before a report, tools are locked and the model is asked to write the report with what it has.

**Post-checks in code:**

| Check | Issue code |
|---|---|
| A cited `request_id` does not exist | `unknown_evidence` |
| A claim cites no valid evidence | `no_valid_evidence` |
| A claim cites a failed call | `cites_failed_call` |
| Causal words ("because", "due to", "led to", "drove", …) | `causal_language` |

**Never crash:** an invalid week, a model error, or a network error returns `run.status: "error"` with a message, never an exception to the runner.

## 7. Testing without AWS

Bedrock access was not ready yet, so the loop is tested with a **scripted stand-in model**: it plays a fixed sequence of tool calls and a final report through the **real** Strands loop, the **real** tools, and the **golden** database. Steps can cite the `request_id`s returned by earlier calls, like a real model would.

| Test | What it proves |
|---|---|
| Investigation path and report | Tool calls run in order, evidence is logged, claims get contract fields |
| Unknown evidence and causal language | Fake `request_id`s and "because" are flagged; confidence drops to low |
| Repeated call | The second identical call is blocked |
| Tool budget | Calls beyond the budget are blocked |
| Tool failure | The failed call is added to `unavailable`; the draft is `PARTIAL`; citing it is flagged |
| Invalid week | A clean error, no crash |
| Model failure | A clean error, no crash |

**Result: 7 of 7 tests pass.**

**What these tests do not prove:** how well a real model investigates. That needs live Bedrock runs on the golden scenarios (see Open items).

## 8. Challenges and how we fixed them

| Challenge | What happened | Fix |
|---|---|---|
| Agent vs. workflow | A fully autonomous agent would be less predictable; a fixed pipeline would be a "prompt wrapper" | Hybrid: fixed steps in the runner, agent only for the data-dependent investigation |
| Noisy tool results | Full envelopes include parameters, baselines, and evidence references the model doesn't need | Concise views for the model; full envelopes in the evidence log |
| Long prompts become brittle | Every new rule would make the prompt longer | Steering hooks and in-result guidance fire only when needed |
| Endless loops and repeated calls | Agents can repeat themselves or keep calling tools | Tool budget, repeated-call blocking, and a turn cap |
| Unreliable self-rated confidence | Models are poor judges of their own certainty | Confidence set by code from z-scores and warnings |
| No Bedrock access yet | The loop could not run end to end | A scripted stand-in model drives the real loop in tests |
| Breakdown warnings lowered confidence unfairly | Found by inspecting what the model sees: warnings from small, unrelated groups were applied to the whole result, so every breakdown claim was "low" even with z = −5.87 for the top group | Warnings now stay on their own group; confidence uses only the top group's warnings |
| Earlier advice to check exact tool sequences | Industry guidance found this too rigid, since agents find valid alternative paths | Evaluation grades outcomes; process checks stay light |

## 9. How to run it

**Offline tests** (from the project root; needs `data/golden.duckdb`):

```
python -m pytest tests/test_agent_offline.py -q
```

**Live run on Bedrock** (once access works):

```
set WBR_MODEL_ID=<model ID enabled in us-east-2>        (Windows)
python scripts/run_analyst.py --week 2018-06-04
python scripts/run_analyst.py --week <S1 week from answer_keys.json> --golden
```

The result is saved to `runs/<week>_<time>.json`. Keep `runs/` out of Git.

**Failure demo:** set `WBR_SIMULATE_FAILURE=avg_review_score` before running.

**Packages:** `strands-agents` (tested with 1.58), `pydantic`, `pytest`. Add them to `requirements.txt`.

## 10. Open items

- [ ] **Model choice:** which Bedrock models are enabled in us-east-2 (asked Franklyn)
- [ ] **Runner interface:** confirm `run_analyst(week)` and the returned fields with Franklyn
- [ ] **Live runs:** run each golden scenario 3–5 times on Bedrock and report how often it succeeds (agents are not deterministic)
- [x] **Agent-level grader:** see `docs/agent_evaluation.md`
- [ ] **Confidence rule:** confirm with the team
- [ ] **Observability:** optionally send traces to AgentCore Observability (OpenTelemetry)
- [ ] **`runs/` folder:** add to `.gitignore`
