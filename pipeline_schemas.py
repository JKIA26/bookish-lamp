"""
Pydantic schemas for pipeline (Tier 2) input/output and step tracking.
"""
from __future__ import annotations
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    AWAITING_INPUT = "awaiting_input"


class PipelineStep(BaseModel):
    name: str
    status: StepStatus
    output: Optional[dict[str, Any]] = None
    error: Optional[str] = None


class PipelineRunRequest(BaseModel):
    pipeline_name: str
    input: dict[str, Any]
    org_id: str
    user_id: str
    session_id: Optional[str] = None


class PipelineRunResult(BaseModel):
    session_id: str
    trace_id: str
    pipeline_name: str
    steps: list[PipelineStep]
    final_output: Optional[dict[str, Any]] = None
    status: StepStatus
    missing_fields: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Invoice processing — concrete pipeline I/O (matches example G)
# ---------------------------------------------------------------------------
class InvoiceProcessingInput(BaseModel):
    file_content_hash: str
    org_id: str


class InvoiceFields(BaseModel):
    vendor: Optional[str] = None
    amount: Optional[float] = None
    due_date: Optional[str] = None
    po_number: Optional[str] = None


class InvoiceProcessingOutput(BaseModel):
    vendor: str
    amount: float
    status: str
    external_ref: Optional[str] = None


# ---------------------------------------------------------------------------
# Support triage — concrete pipeline I/O
# See general/support-operations.md §1-3 for the actual business rules
# this implements (auto-send gate, categories, urgency).
# ---------------------------------------------------------------------------
class SupportTicketFields(BaseModel):
    subject: Optional[str] = None
    body: Optional[str] = None


class SupportTriageOutput(BaseModel):
    category: str
    urgency: str
    confidence: float
    draft_reply: str
    action_taken: str  # "auto_sent" | "routed_to_human"
    external_ref: Optional[str] = None


# ---------------------------------------------------------------------------
# Ops handoff — concrete pipeline I/O
# See ops/ops-operations.md for the business rules (auto-provision gate).
# ---------------------------------------------------------------------------
class OpsHandoffFields(BaseModel):
    deal_id: Optional[str] = None
    customer_name: Optional[str] = None
    plan: Optional[str] = None
    deal_status: Optional[str] = None


class OpsHandoffOutput(BaseModel):
    deal_id: str
    customer_name: str
    plan: str
    action_taken: str  # "auto_provisioned" | "routed_to_ops_review"
    onboarding_ref: Optional[str] = None


# ---------------------------------------------------------------------------
# Lead follow-up — concrete pipeline I/O
# See sales/sales-operations.md for the business rules (auto-send gate).
# ---------------------------------------------------------------------------
class LeadFields(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    source: Optional[str] = None
    company_size: Optional[str] = None  # "small" | "mid" | "enterprise"


class LeadFollowupOutput(BaseModel):
    name: str
    lead_score: float
    tier: str
    draft_followup: str
    action_taken: str  # "auto_sent" | "routed_to_rep"
    external_ref: Optional[str] = None
