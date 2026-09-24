"""Metric-facing evaluation API (ADR-0008 D-7/D-10/D-13/D-14/D-16).

A **local reader**: the Metric plane opens the DB read-only and reads the shadow
tape + label child table. No HTTP route is added (D-10). Nothing here mutates
state — labels are written by the labeller/gate, not by evaluation.

Two honesty rules are encoded here rather than left to the report author:

* **STEER is DEFERRED in P3** (D-16): off-track is its only label source and it
  is dropped, so STEER is reported as DEFERRED with reintroduction trigger
  ``payload.goal_ref`` — never as a passing ``0/N-A``.
* Rows whose ``provider == "mock"`` are flagged **mock-only, non-evidential**
  (D-14), so a green metric cannot be mistaken for production evidence.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterator, List, Optional, Tuple

from jcc_core import audit as audit_kernel
from jcc_core import decision
from jcc_core.schemas import DirectiveAction

from . import shadow_store

MOCK_PROVIDER = "mock"
STEER_TRIGGER = "payload.goal_ref"


def _as_mapping(value: Any) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, (str, bytes)):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _signals_from_record(record: Dict[str, Any]) -> decision.Signals:
    flags = _as_mapping(record.get("assessment_flags")) or {}
    kwargs = {k: flags.get(k) for k in (
        "meaningful_progress", "worker_stuck", "work_off_track", "requirements_satisfied",
        "needs_verification", "ready_to_finish", "needs_human", "band", "score",
        "provider_verified", "evidence_complete",
    )}
    return decision.Signals(**kwargs)


def _thresholds_from_record(record: Dict[str, Any]) -> decision.Thresholds:
    snap = _as_mapping(record.get("thresholds_snapshot")) or {}
    allowed = {k: v for k, v in snap.items() if k in decision.KNOWN_THRESHOLD_KEYS}
    try:
        return decision.Thresholds(**allowed)
    except (TypeError, ValueError):
        return decision.Thresholds()


def recompute_decision(record: Dict[str, Any]) -> Tuple[DirectiveAction, str]:
    """Re-run the kernel from the stored inputs; return ``(action, trace_hash)``.

    This is the falsifier for "deterministic": if the stored action/trace_hash
    differ from the recomputation, arbitration was not a pure function of the
    recorded inputs.
    """
    signals = _signals_from_record(record)
    thresholds = _thresholds_from_record(record)
    result = decision.arbitrate(signals, thresholds)
    return result.action, decision.trace_hash(signals, thresholds, result)


def verify_decision(record: Dict[str, Any]) -> bool:
    """True iff the recomputed ``(action, trace_hash)`` matches what was stored."""
    action, trace = recompute_decision(record)
    stored_action = record.get("action")
    stored_action = stored_action.value if isinstance(stored_action, DirectiveAction) else stored_action
    return action.value == stored_action and trace == record.get("trace_hash")


def iter_decisions(conn, *, session_id: Optional[str] = None, limit: int = 500,
                   with_labels: bool = True) -> Iterator[Dict[str, Any]]:
    yield from shadow_store.read_decisions(
        conn, session_id=session_id, limit=limit, with_labels=with_labels
    )


def _is_mock(record: Dict[str, Any]) -> bool:
    return str(record.get("provider") or "").lower() == MOCK_PROVIDER


def calibration_report(conn, *, window: int = 10000) -> Dict[str, Any]:
    """Counts by ``(band x label_value)``, plus per-action coverage and the
    mock-only flag (D-14), and an explicit DEFERRED entry for STEER (D-16)."""
    records = shadow_store.read_decisions(conn, limit=window, with_labels=True)
    by_band: Dict[str, Dict[str, int]] = {}
    by_action: Dict[str, int] = {}
    mock_only = 0
    labeled = 0

    for rec in records:
        band = str(rec.get("assessment_band") or "unknown")
        action = str(rec.get("action") or "unknown")
        by_action[action] = by_action.get(action, 0) + 1
        if _is_mock(rec):
            mock_only += 1
        labels = rec.get("labels") or {}
        oracle = labels.get("oracle_next_event")
        if oracle:
            labeled += 1
            row = by_band.setdefault(band, {})
            value = str(oracle.get("label_value"))
            row[value] = row.get(value, 0) + 1

    total = len(records)
    report = {
        "total": total,
        "labeled": labeled,
        "coverage": (labeled / total) if total else 0.0,
        "by_band_label": by_band,
        "by_action": by_action,
        "mock_only_non_evidential": mock_only,
        "steer_status": {
            "state": "DEFERRED",
            "reason": "off-track (STEER's only label source) dropped in P3",
            "reintroduction_trigger": STEER_TRIGGER,
        },
    }
    return report


def oracle_precision_recall(conn, *, window_events: int) -> Dict[str, Any]:
    """Compare ``oracle_next_event`` vs ``human`` labels on the SAME decision_id.

    Uses the label child table's multi-source key (D-13). Only decisions with
    BOTH sources are counted, so the comparison is like-for-like.
    """
    records = shadow_store.read_decisions(conn, limit=10000, with_labels=True)
    tp = fp = fn = tn = 0
    compared = 0
    for rec in records:
        labels = rec.get("labels") or {}
        oracle = labels.get("oracle_next_event")
        human = labels.get("human")
        if not oracle or not human:
            continue
        oracle_pos = str(oracle.get("label_value")) not in ("", "none", "continue")
        human_pos = str(human.get("label_value")) not in ("", "none", "continue")
        compared += 1
        if oracle_pos and human_pos:
            tp += 1
        elif oracle_pos and not human_pos:
            fp += 1
        elif not oracle_pos and human_pos:
            fn += 1
        else:
            tn += 1

    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    return {
        "window_events": window_events,
        "compared": compared,
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": precision,
        "recall": recall,
    }


def integrity_report(conn) -> Dict[str, Any]:
    """``{'audit_ok','shadow_ok','broken_at'}`` — both chains, one call."""
    audit_ok, audit_bad = audit_kernel.verify_chain(conn)
    shadow_ok, shadow_bad = shadow_store.verify_shadow_chain(conn)
    broken_at: Optional[int] = None
    if not audit_ok:
        broken_at = audit_bad
    elif not shadow_ok:
        broken_at = shadow_bad
    return {"audit_ok": audit_ok, "shadow_ok": shadow_ok, "broken_at": broken_at}
