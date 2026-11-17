"""Write a signal -> plan pipeline result to the platform tables.

The agents never write to the database. This is our code storing the result
process_email() returned, after every model output in it has already been
schema-validated and every transfer checked:

    supply_event   what the notice said (the validated assessment)
    plan           the planner's answer to that event
    plan_action    one row per proposed move or action, marked verified or not
    audit_log      one entry for the run, whatever the outcome

All rows are written in one transaction. Nothing is approved here: plans are
stored as pending_approval (or proposed when no option passed the checks).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
import uuid

from sqlalchemy import text
from sqlalchemy.engine import Engine

PLANNED = {"plan_generated", "no_verified_plan"}
ASSESSED = PLANNED | {"no_shortage_detected"}


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _actions(plan_id: str, result: dict) -> list[dict]:
    verified = result.get("verified_options", [])
    rejected = {int(k): v for k, v in result.get("rejected_options", {}).items() if str(k).isdigit()}
    rows = []

    for index, option in enumerate(result.get("proposed_options", [])):
        common = {
            "plan_id": plan_id,
            "option_index": index + 1,
            "type": option["type"],
            "description": option["description"],
            "reasoning": option.get("reasoning"),
            "verified": option in verified,
            "problems": json.dumps(rejected[index]) if index in rejected else None,
        }
        # Transfers have moves (from -> to); orders have order lines (supplier -> site).
        moves = option.get("moves") or [
            {"from_site": None, "to_site": line["site_id"], "units": line["units"], "supplier_id": line["supplier_id"]}
            for line in option.get("orders") or []
        ] or [None]
        for move_index, move in enumerate(moves):
            rows.append({
                **common,
                "action_id": f"{plan_id}-O{index + 1}" + (f"-M{move_index + 1}" if len(moves) > 1 else ""),
                "from_site": move["from_site"] if move else None,
                "to_site": move["to_site"] if move else None,
                "units": move["units"] if move else None,
                "supplier_id": move.get("supplier_id") if move else None,
            })
    return rows


def open_event_for(engine: Engine, source_ref: str) -> str | None:
    """The supply event still open (or monitoring) for this source, if any.

    Used to avoid planning the same notice twice while it is being handled.
    Once its event is mitigated or closed, the same source may be processed again.
    """

    with engine.connect() as conn:
        return conn.execute(text("""
            SELECT event_id FROM supply_event
            WHERE source_ref = :ref AND status IN ('open', 'monitoring')
            ORDER BY created_at DESC LIMIT 1
        """), {"ref": source_ref}).scalar_one_or_none()


def save_pipeline_result(
    engine: Engine,
    result: dict,
    source_ref: str,
    actor: str = "signal-agent",
    source_type: str = "supplier_email",
    kind: str = "delay",
    site_id: str | None = None,
    event_id: str | None = None,
) -> dict:
    """Store one process_email() / process_event() result. Returns the IDs that were written.

    source_type and kind describe the signal: a supplier email reporting a
    delay (default), or a Health Canada shortage report (shortage_report /
    shortage).

    event_id: an existing alert for the same drug ("one living alert per drug").
    The new signal is added to its evidence and the new plan replaces the alert's
    earlier plans that are still waiting (they are marked superseded, never
    deleted). Approved plans are left alone.
    """

    status = result["status"]
    assessment = result.get("assessment") or {}
    saved = {"event_id": None, "plan_id": None, "action_ids": []}
    evidence = {"source": source_type, "ref": source_ref,
                "summary": assessment.get("reason") or (result.get("reason") if isinstance(result.get("reason"), str) else ""),
                "at": datetime.now(timezone.utc).isoformat()}

    with engine.begin() as conn:
        if event_id and status in ASSESSED:
            saved["event_id"] = event_id
            conn.execute(text("""
                UPDATE supply_event
                SET detail = jsonb_set(COALESCE(detail, '{}'::jsonb), '{evidence}',
                                       COALESCE(detail->'evidence', '[]'::jsonb) || CAST(:item AS jsonb)),
                    status = CASE WHEN :at_risk THEN 'open' ELSE status END
                WHERE event_id = :event_id
            """), {"event_id": event_id, "item": json.dumps([evidence]), "at_risk": bool(result.get("risk_sites"))})
            if status in PLANNED:
                conn.execute(text("""
                    UPDATE plan SET status = 'superseded'
                    WHERE event_id = :event_id AND status IN ('pending_approval', 'proposed', 'broken')
                """), {"event_id": event_id})
        elif status in ASSESSED and assessment.get("drug_din"):
            risk_sites = result.get("risk_sites") or []
            saved["event_id"] = _new_id("SE")
            conn.execute(
                text(
                    """
                    INSERT INTO supply_event (event_id, din, site_id, kind, source_type, source_ref,
                                              delay_days, confidence, status, detail)
                    VALUES (:event_id, :din, :site_id, :kind, :source_type, :source_ref,
                            :delay_days, :confidence, :status, CAST(:detail AS jsonb))
                    """
                ),
                {
                    "event_id": saved["event_id"],
                    "din": assessment["drug_din"],
                    # One affected site is recorded on the event; several stay in the plan.
                    "site_id": site_id or (risk_sites[0] if len(risk_sites) == 1 else None),
                    "source_ref": source_ref,
                    "kind": kind,
                    "source_type": source_type,
                    "delay_days": result.get("delay_days"),
                    "confidence": assessment["confidence"],
                    "status": "open" if risk_sites else "monitoring",
                    "detail": json.dumps({**assessment, "evidence": [evidence]}),
                },
            )

        if status in PLANNED and saved["event_id"]:
            saved["plan_id"] = _new_id("P")
            conn.execute(
                text(
                    """
                    INSERT INTO plan (plan_id, event_id, status, no_perfect_plan, source, detail)
                    VALUES (:plan_id, :event_id, :status, :no_perfect_plan, 'planning_agent',
                            CAST(:detail AS jsonb))
                    """
                ),
                {
                    "plan_id": saved["plan_id"],
                    "event_id": saved["event_id"],
                    "status": "pending_approval" if result.get("verified_options") else "proposed",
                    "no_perfect_plan": bool(result.get("no_perfect_plan")),
                    # What the options were checked against; approval re-checks with current stock.
                    "detail": json.dumps({key: result.get(key) for key in
                                          ("shortfall", "cover_days", "delay_days", "risk_sites")}),
                },
            )
            for action in _actions(saved["plan_id"], result):
                conn.execute(
                    text(
                        """
                        INSERT INTO plan_action (action_id, plan_id, option_index, type, description, reasoning,
                                                 from_site, to_site, units, supplier_id, verified, problems)
                        VALUES (:action_id, :plan_id, :option_index, :type, :description, :reasoning,
                                :from_site, :to_site, :units, :supplier_id, :verified, CAST(:problems AS jsonb))
                        """
                    ),
                    action,
                )
                saved["action_ids"].append(action["action_id"])

        refs = [source_ref] + [ref for ref in (saved["event_id"], saved["plan_id"]) if ref]
        saved["audit_id"] = conn.execute(
            text(
                """
                INSERT INTO audit_log (actor, actor_type, action, object, detail, refs)
                VALUES (:actor, :actor_type, :action, :object, CAST(:detail AS jsonb), :refs)
                RETURNING audit_id
                """
            ),
            {
                "actor": actor,
                # Rejected before any model ran (security screen, size limit) -> a rule decided.
                "actor_type": "agent" if assessment else "rule",
                "action": f"Signal pipeline: {status}",
                "object": saved["plan_id"] or saved["event_id"] or source_ref,
                "detail": json.dumps(result, default=str),
                "refs": refs,
            },
        ).scalar_one()

    return saved
