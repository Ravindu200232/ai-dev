"""Pydantic schema for the SRS document — the strict validation target."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Loose(BaseModel):
    model_config = ConfigDict(extra="allow")


class AppSummary(_Loose):
    app_name: str
    short_description: str
    business_goal: str
    target_users: list[str] = Field(default_factory=list)


class AppType(_Loose):
    primary_type: str
    supported_types: list[str] = Field(default_factory=list)
    frontend_type: Optional[str] = None
    backend_type: Optional[str] = None
    database_type: Optional[str] = None
    example_stack: dict[str, Any] = Field(default_factory=dict)


class Role(_Loose):
    role_key: str
    role_name: str
    description: str


class RoleAccessEntry(_Loose):
    role: str
    allowed_pages: list[str] = Field(default_factory=list)
    restricted_pages: list[str] = Field(default_factory=list)
    allowed_functions: list[str] = Field(default_factory=list)
    restricted_functions: list[str] = Field(default_factory=list)


class PageDef(_Loose):
    page_name: str
    route: str
    page_type: Optional[str] = None
    login_required: Optional[bool] = None
    allowed_roles: list[str] = Field(default_factory=list)
    sections: list[str] = Field(default_factory=list)
    functions: list[str] = Field(default_factory=list)


class TableField(_Loose):
    name: str
    type: str
    primary_key: Optional[bool] = None
    nullable: Optional[bool] = None
    unique: Optional[bool] = None
    default: Any = None
    values: list[Any] = Field(default_factory=list)
    references: Optional[str] = None


class DbTable(_Loose):
    table_name: str
    description: Optional[str] = None
    fields: list[TableField] = Field(default_factory=list)


class DbRelationship(_Loose):
    from_: str = Field(alias="from")
    to: str
    type: str
    via: Optional[str] = None
    description: Optional[str] = None

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class DatabaseDesign(_Loose):
    data_type_standards: dict[str, str] = Field(default_factory=dict)
    tables: list[DbTable] = Field(default_factory=list)
    relationships: list[DbRelationship] = Field(default_factory=list)


class ApiEndpoint(_Loose):
    method: str
    path: str
    description: Optional[str] = None
    auth_required: Optional[bool] = None
    allowed_roles: list[str] = Field(default_factory=list)


class FunctionalRequirement(_Loose):
    id: str
    module: str
    requirement: str
    priority: str = "medium"
    allowed_roles: list[str] = Field(default_factory=list)


class NonFunctionalRequirement(_Loose):
    id: str
    category: str
    requirement: str


class BusinessWorkflow(_Loose):
    workflow_name: str
    who: Optional[str] = None
    steps: list[str] = Field(default_factory=list)


class ValidationRule(_Loose):
    field: str
    rule: str


class NotificationRule(_Loose):
    event: str
    recipients: list[str] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)


class ReportingRequirement(_Loose):
    report_name: str
    filters: list[str] = Field(default_factory=list)
    exports: list[str] = Field(default_factory=list)


class IntegrationRequirement(_Loose):
    name: str
    type: str
    description: str
    required: Optional[bool] = None


class Ambiguity(_Loose):
    id: str
    area: str
    description: str
    assumption_made: str
    needs_clarification: bool = True


class RiskPriority(_Loose):
    id: str
    area: str
    risk: str
    severity: str
    reason: str
    mitigation: str


class AcceptanceCriterion(_Loose):
    criterion: str
    id: Optional[str] = None


class TraceabilityEntry(_Loose):
    requirement_id: str
    module: str
    pages: list[str] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list)
    test_case: Optional[str] = None


class AuthenticationRequirement(_Loose):
    login_required: bool = False
    sign_in_route: Optional[str] = None
    identity_fields: list[str] = Field(default_factory=list)
    registration_fields: list[str] = Field(default_factory=list)
    registration_mode: str = "none"
    self_registration: bool = False
    sign_up_route: Optional[str] = None
    registration_role: Optional[str] = None
    registration_roles: list[str] = Field(default_factory=list)
    provisioning_role: Optional[str] = None
    account_management_route: Optional[str] = None
    invitation_management_route: Optional[str] = None
    invitation_accept_route: Optional[str] = None
    request_access_route: Optional[str] = None
    access_review_route: Optional[str] = None
    password_reset_required: bool = False
    signed_out_protected_access: Optional[str] = None
    wrong_role_access: Optional[str] = None
    auth_transport: str = "provider_managed"


class DiagramArtifact(_Loose):
    id: str
    kind: str
    title: str
    format: str = "mermaid"
    source: str
    standard: Optional[str] = None
    canonical_rendering: Optional[str] = None
    rendered_by: Optional[str] = None
    mmd_path: Optional[str] = None
    svg_path: Optional[str] = None
    png_path: Optional[str] = None


class SrsDocument(_Loose):
    document_title: str = "Software Requirements Specification"
    project_name: str
    version: str = "1.0.0"
    document_language: str = "English"
    system_category: str
    prepared_for: str = "Full App Build Requirement"
    standards_profile: dict[str, Any] = Field(default_factory=dict)
    document_control: dict[str, Any] = Field(default_factory=dict)
    requirements_quality_review: dict[str, Any] = Field(default_factory=dict)

    app_summary: AppSummary
    app_type: AppType
    authentication_requirement: AuthenticationRequirement = Field(default_factory=AuthenticationRequirement)
    roles: list[Role] = Field(default_factory=list)
    role_access_matrix: list[RoleAccessEntry] = Field(default_factory=list)
    public_pages: list[PageDef] = Field(default_factory=list)
    protected_pages: list[PageDef] = Field(default_factory=list)
    main_modules: list[str] = Field(default_factory=list)
    database_design: DatabaseDesign
    api_design: list[ApiEndpoint] = Field(default_factory=list)
    functional_requirements: list[FunctionalRequirement] = Field(default_factory=list)
    non_functional_requirements: list[NonFunctionalRequirement] = Field(default_factory=list)
    business_workflows: list[BusinessWorkflow] = Field(default_factory=list)
    validation_rules: list[ValidationRule] = Field(default_factory=list)
    notification_rules: list[NotificationRule] = Field(default_factory=list)
    security_requirements: list[str] = Field(default_factory=list)
    ui_ux_requirements: dict[str, Any] = Field(default_factory=dict)
    reporting_requirements: list[ReportingRequirement] = Field(default_factory=list)
    integration_requirements: list[IntegrationRequirement] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    ambiguities: list[Ambiguity] = Field(default_factory=list)
    risk_priority: list[RiskPriority] = Field(default_factory=list)
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list)
    requirement_traceability_matrix: list[TraceabilityEntry] = Field(default_factory=list)
    diagrams: list[DiagramArtifact] = Field(default_factory=list)

    @field_validator("functional_requirements")
    @classmethod
    def _fr_not_empty(cls, v):
        if len(v) < 3:
            raise ValueError("functional_requirements must contain at least 3 entries")
        return v

    @field_validator("non_functional_requirements")
    @classmethod
    def _nfr_not_empty(cls, v):
        if len(v) < 3:
            raise ValueError("non_functional_requirements must contain at least 3 entries")
        return v

    @field_validator("roles")
    @classmethod
    def _roles_not_empty(cls, v):
        if len(v) < 1:
            raise ValueError("roles must contain at least 1 entry")
        return v


class SrsEnvelope(BaseModel):
    model_config = ConfigDict(extra="allow")
    srs_document: SrsDocument


def validate_srs(data: dict) -> SrsEnvelope:
    """Validate a raw dict into an `SrsEnvelope` (raises ValidationError)."""
    return SrsEnvelope.model_validate(data)


def summarize_srs(srs: dict) -> dict:
    """Compute the inspector summary counts from a raw SRS dict."""
    doc = srs.get("srs_document", srs)
    db = doc.get("database_design", {}) or {}
    ambiguities = doc.get("ambiguities", []) or []
    return {
        "functional": len(doc.get("functional_requirements", []) or []),
        "non_functional": len(doc.get("non_functional_requirements", []) or []),
        "use_cases": len(doc.get("business_workflows", []) or []),
        "open_ambiguities": sum(1 for a in ambiguities if a.get("needs_clarification")),
        "tables": len(db.get("tables", []) or []),
        "roles": len(doc.get("roles", []) or []),
        "modules": len(doc.get("main_modules", []) or []),
        "diagrams": len(doc.get("diagrams", []) or []),
    }
