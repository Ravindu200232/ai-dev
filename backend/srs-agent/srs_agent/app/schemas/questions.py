"""Requirement-gathering question schemas."""
from __future__ import annotations

from typing import Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from .project import now_iso


COVERAGE_AREAS = [
    "business_type", "main_goal", "users_roles", "auth", "public_pages",
    "app_surfaces", "features_modules", "data_entities", "payments_billing",
    "reports", "notifications", "file_uploads", "integrations",
    "security_privacy", "performance", "devices_mobile", "languages",
    "deployment_stack", "special_rules",
]

CRITICAL_AREAS = {
    "business_type", "main_goal", "users_roles", "features_modules", "data_entities",
}

ANSWER_TYPES = {"single_choice", "multi_choice", "free_text", "number", "yes_no"}


class Question(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    question: str
    why_needed: str = ""
    answer_type: str = "single_choice"
    suggested_options: list[str] = Field(default_factory=list)
    maps_to_srs_fields: list[str] = Field(default_factory=list)
    coverage_areas: list[str] = Field(default_factory=list)
    required: bool = True


class QuestionSession(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    project_id: str
    questions: list[Question] = Field(default_factory=list)
    total: int = 0
    coverage_score: float = 0.0
    complete: bool = False
    created_at: str = Field(default_factory=now_iso)


class Answer(BaseModel):
    question_id: str
    value: Union[str, list[str], int, float, bool, None] = None
    raw_text: Optional[str] = None


class AnswersPayload(BaseModel):
    answers: list[Answer] = Field(default_factory=list)


class InterviewAnswer(BaseModel):
    """One turn of the interview."""

    key: str
    value: Union[str, list[str], int, float, bool, None] = None
    text: Optional[str] = None
    selected: Optional[list[str]] = None
    custom: Optional[str] = None

    attachments: list[str] = Field(default_factory=list)
