"""What the model writes. Code adds the remaining contract fields afterwards
(claim_id, confidence, causal_language_allowed, requires_human_review)."""

from typing import Literal

from pydantic import BaseModel, Field


class DraftClaim(BaseModel):
    question_id: Literal["Q1", "Q2", "Q3"] = Field(
        description="Q1 revenue change, Q2 review score change, Q3 underperforming categories")
    statement: str = Field(description="One sentence for the report. Numbers must come from tool results.")
    claim_type: Literal["fact", "observed_pattern", "unexplained"] = Field(
        description="fact: a number from one result; observed_pattern: two things moving together "
                    "(never a cause); unexplained: a change with no supporting signal")
    supporting_evidence: list[str] = Field(
        description="request_id values of every tool result this claim relies on")


class DraftReport(BaseModel):
    """The analyst's weekly business review draft."""
    claims: list[DraftClaim]
    actionable_questions: list[str] = Field(
        description="Follow-up questions for the analyst, phrased as questions")
    unavailable: list[str] = Field(
        description="Anything that could not be checked, e.g. a failed or incomplete result")
