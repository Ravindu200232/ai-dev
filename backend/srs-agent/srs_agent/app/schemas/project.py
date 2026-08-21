"""Project, domain, and event schemas (API + persistence shapes)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DomainClassification(BaseModel):
    model_config = ConfigDict(extra="allow")
    detected_domain: str
    domain_key: str
    app_type: str
    confidence: float = 0.5
    similar_patterns: int = 0
    reasoning: Optional[str] = None


class ComplexityEstimate(BaseModel):
    overall: str = "Medium"
    backend: str = "Medium"
    frontend: str = "Medium"


class SuggestedStack(BaseModel):
    frontend: str = "React + Tailwind"
    backend: str = "Express / Node"
    database: str = "MongoDB"
    architecture: str = "Microservices"
    locked: bool = True


class Project(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    title: str
    raw_idea: str
    detected_domain: str = "Custom"
    domain_key: str = "custom"
    status: str = "intake"
    current_version: str = "0.0.0"
    language: str = "English"
    classification: Optional[DomainClassification] = None
    complexity: Optional[ComplexityEstimate] = None
    suggested_stack: Optional[SuggestedStack] = None
    coverage_score: float = 0.0
    needs_clarification: bool = False
    clarification_reason: Optional[str] = None
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)


class CreateProjectRequest(BaseModel):
    idea: str = Field(..., min_length=1)
    language: Optional[str] = None


class AddInputRequest(BaseModel):
    mode: str = "text"
    text: Optional[str] = None


class UploadRequest(AddInputRequest):
    """An attachment carried as base64 inside a JSON body."""

    filename: Optional[str] = None
    content_type: Optional[str] = None
    data_base64: Optional[str] = None
    # Purpose supplied for this file.
    purpose: Optional[str] = None


class CustomizeRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
