"""Requirement-gathering question schemas."""
from __future__ import annotations

from typing import Optional, Union

from pydantic import BaseModel, Field

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
class InterviewAnswer(BaseModel):
    """One turn of the interview."""

    key: str
    value: Union[str, list[str], int, float, bool, None] = None
    text: Optional[str] = None
    selected: Optional[list[str]] = None
    custom: Optional[str] = None

    attachments: list[str] = Field(default_factory=list)
