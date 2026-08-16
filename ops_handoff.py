"""
ops_handoff pipeline (Tier 2) — implements the workflow documented in
ops/ops-operations.md.

  deal event (deal_id, customer_name, plan, deal_status)
      -> validation.parse_and_validate (required fields)
      -> gate: auto-provision OR route to ops review
      -> automation.execute_action (provision account or create review ticket)

Gate mirrors the restriction-first rail used by support_triage and
entitlement-role-sync-policy.md: only a clean, unambiguous "closed_won"
status auto-provisions. Anything else (pending, at-risk, unrecognized
status) routes to a human — the pipeline never guesses.
"""
from __future__ import annotations
from typing import Any

from tools.registry import registry
from schemas.pipeline_schemas import PipelineStep, StepStatus, PipelineRunResult
from tracing.audit_log import log_event


REQUIRED_FIELDS = ["deal_id", "customer_name", "plan", "deal_status"]

# ops-operations.md §2 — only these plans are eligible for auto-provisioning;
# custom/enterprise plans always need an ops review (pricing, contract terms
# vary too much to safely automate).
_AUTO_ELIGIBLE_PLANS = {"starter", "pro"}


def _should_auto_provision(deal_status: str, plan: str) -> bool:
    if deal_status != "closed_won":
        return False
    if plan not in _AUTO_ELIGIBLE_PLANS:
        return False
    return True


def run(
    session_id: str,
    trace_id: str,
    org_id: str,
    deal_id: str = "",
    customer_name: str = "",
    plan: str = "",
    deal_status: str = "",
    extra_fields: dict[str, Any] | None = None,
) -> PipelineRunResult:
    steps: list[PipelineStep] = []
    extra_fields = extra_fields or {}
    raw = {
        "deal_id": deal_id, "customer_name": customer_name,
        "plan": plan, "deal_status": deal_status, **extra_fields,
    }

    # Step 1: validate required handoff fields
    validate_result = registry.invoke(
        "validation.parse_and_validate",
        org_id=org_id,
        raw_input={"raw_input": raw, "target_schema": "ops_handoff_fields"},
    )
    steps.append(PipelineStep(
        name="validate_handoff_fields",
        status=StepStatus.DONE if validate_result.valid else StepStatus.FAILED,
        output=validate_result.model_dump(),
    ))
    log_event(trace_id, "pipeline.step_completed", {"step": "validate_handoff_fields"})

    if not validate_result.valid or validate_result.parsed is None:
        return PipelineRunResult(
            session_id=session_id, trace_id=trace_id, pipeline_name="ops_handoff",
            steps=steps, final_output=None, status=StepStatus.FAILED,
        )

    parsed = validate_result.parsed
    missing = [f for f in REQUIRED_FIELDS if not parsed.get(f)]
    if missing:
        steps.append(PipelineStep(name="await_missing_fields", status=StepStatus.AWAITING_INPUT))
        return PipelineRunResult(
            session_id=session_id, trace_id=trace_id, pipeline_name="ops_handoff",
            steps=steps, final_output=None, status=StepStatus.AWAITING_INPUT, missing_fields=missing,
        )

    # Step 2: gate — auto-provision or route to ops review
    auto_provision = _should_auto_provision(parsed["deal_status"], parsed["plan"])
    action_id = "provision_account" if auto_provision else "create_ops_review_ticket"

    automation_result = registry.invoke(
        "automation.execute_action",
        org_id=org_id,
        raw_input={
            "platform": "n8n",
            "action_id": action_id,
            "params": {
                "deal_id": parsed["deal_id"], "customer_name": parsed["customer_name"],
                "plan": parsed["plan"], "deal_status": parsed["deal_status"],
            },
            "org_id": org_id,
        },
    )
    steps.append(PipelineStep(
        name="auto_provision" if auto_provision else "route_to_ops_review",
        status=StepStatus.DONE if automation_result.status == "success" else StepStatus.FAILED,
        output=automation_result.model_dump(),
    ))
    log_event(trace_id, "pipeline.step_completed", {"step": "gate_decision", "auto_provision": auto_provision})

    final_output = {
        "deal_id": parsed["deal_id"],
        "customer_name": parsed["customer_name"],
        "plan": parsed["plan"],
        "action_taken": "auto_provisioned" if auto_provision else "routed_to_ops_review",
        "onboarding_ref": automation_result.external_ref,
    }

    return PipelineRunResult(
        session_id=session_id, trace_id=trace_id, pipeline_name="ops_handoff",
        steps=steps, final_output=final_output,
        status=StepStatus.DONE if automation_result.status == "success" else StepStatus.FAILED,
    )
