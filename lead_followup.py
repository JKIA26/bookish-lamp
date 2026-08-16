"""
lead_followup pipeline (Tier 2) — implements the workflow documented in
sales/sales-operations.md.

  lead (name, email, source, company_size)
      -> validation.parse_and_validate (required fields)
      -> score lead
      -> rag.retrieve (personalized content for follow-up)
      -> draft follow-up
      -> gate: auto-send OR route to a human rep
      -> automation.execute_action (send email or route to rep)

Gate mirrors the same restriction-first pattern as support_triage and
ops_handoff: enterprise-tier leads always go to a human — deal size and
relationship complexity make those unsafe to fully automate.
"""
from __future__ import annotations
from typing import Any

from tools.registry import registry
from schemas.pipeline_schemas import PipelineStep, StepStatus, PipelineRunResult
from tracing.audit_log import log_event


REQUIRED_FIELDS = ["name", "email", "source"]

# sales-operations.md §2 — lead source scoring weights (stub; swap for a
# real scoring model before this handles real pipeline volume).
_SOURCE_SCORES = {
    "demo_request": 0.9,
    "content_download": 0.6,
    "newsletter_signup": 0.3,
    "cold_list": 0.2,
}


def _score_lead(source: str, company_size: str) -> float:
    base = _SOURCE_SCORES.get(source, 0.4)
    if company_size == "enterprise":
        base += 0.05  # higher intent signal, but NOT enough alone to auto-send (see gate)
    return min(base, 0.95)


def _should_auto_send(score: float, company_size: str) -> bool:
    # sales-operations.md §3 — enterprise leads always route to a rep,
    # regardless of score. Only small/mid leads with strong intent auto-send.
    if company_size == "enterprise":
        return False
    return score >= 0.7


def run(
    session_id: str,
    trace_id: str,
    org_id: str,
    name: str = "",
    email: str = "",
    source: str = "",
    extra_fields: dict[str, Any] | None = None,
) -> PipelineRunResult:
    steps: list[PipelineStep] = []
    extra_fields = extra_fields or {}
    raw = {"name": name, "email": email, "source": source, **extra_fields}

    # Step 1: validate required lead fields
    validate_result = registry.invoke(
        "validation.parse_and_validate",
        org_id=org_id,
        raw_input={"raw_input": raw, "target_schema": "lead_fields"},
    )
    steps.append(PipelineStep(
        name="validate_lead_fields",
        status=StepStatus.DONE if validate_result.valid else StepStatus.FAILED,
        output=validate_result.model_dump(),
    ))
    log_event(trace_id, "pipeline.step_completed", {"step": "validate_lead_fields"})

    if not validate_result.valid or validate_result.parsed is None:
        return PipelineRunResult(
            session_id=session_id, trace_id=trace_id, pipeline_name="lead_followup",
            steps=steps, final_output=None, status=StepStatus.FAILED,
        )

    parsed = validate_result.parsed
    missing = [f for f in REQUIRED_FIELDS if not parsed.get(f)]
    if missing:
        steps.append(PipelineStep(name="await_missing_fields", status=StepStatus.AWAITING_INPUT))
        return PipelineRunResult(
            session_id=session_id, trace_id=trace_id, pipeline_name="lead_followup",
            steps=steps, final_output=None, status=StepStatus.AWAITING_INPUT, missing_fields=missing,
        )

    company_size = parsed.get("company_size") or "small"

    # Step 2: score the lead
    score = _score_lead(parsed["source"], company_size)
    tier = "enterprise" if company_size == "enterprise" else ("mid" if company_size == "mid" else "small")
    steps.append(PipelineStep(
        name="score_lead", status=StepStatus.DONE,
        output={"score": score, "tier": tier},
    ))
    log_event(trace_id, "pipeline.step_completed", {"step": "score_lead"})

    # Step 3: retrieve personalized content for the follow-up
    retrieve_result = registry.invoke(
        "rag.retrieve",
        org_id=org_id,
        raw_input={"query": parsed["source"], "index_id": f"sales_content_{org_id}", "top_k": 2, "filters": {}},
    )
    steps.append(PipelineStep(
        name="retrieve_content", status=StepStatus.DONE, output=retrieve_result.model_dump(),
    ))

    # Step 4: draft follow-up
    draft = (
        f"Hi {parsed['name']}, thanks for your interest via {parsed['source']}! "
        f"{retrieve_result.chunks[0].text if retrieve_result.chunks else 'Here is more info on getting started.'}"
    )
    steps.append(PipelineStep(name="draft_followup", status=StepStatus.DONE, output={"draft_followup": draft}))

    # Step 5: gate — auto-send or route to a human rep
    auto_send = _should_auto_send(score, company_size)
    action_id = "send_followup_email" if auto_send else "route_lead_to_rep"

    automation_result = registry.invoke(
        "automation.execute_action",
        org_id=org_id,
        raw_input={
            "platform": "n8n",
            "action_id": action_id,
            "params": {"name": parsed["name"], "email": parsed["email"], "draft_followup": draft},
            "org_id": org_id,
        },
    )
    steps.append(PipelineStep(
        name="auto_send" if auto_send else "route_to_rep",
        status=StepStatus.DONE if automation_result.status == "success" else StepStatus.FAILED,
        output=automation_result.model_dump(),
    ))
    log_event(trace_id, "pipeline.step_completed", {"step": "gate_decision", "auto_send": auto_send})

    final_output = {
        "name": parsed["name"],
        "lead_score": score,
        "tier": tier,
        "draft_followup": draft,
        "action_taken": "auto_sent" if auto_send else "routed_to_rep",
        "external_ref": automation_result.external_ref,
    }

    return PipelineRunResult(
        session_id=session_id, trace_id=trace_id, pipeline_name="lead_followup",
        steps=steps, final_output=final_output,
        status=StepStatus.DONE if automation_result.status == "success" else StepStatus.FAILED,
    )
