#!/usr/bin/env python3
"""
Priority Alpha — Operational State Memory Engine
System: rackmain1-wall-ui / rh_tracker API

Purpose:
- Create lightweight JSON state files
- Safely read/write operational memory
- Provide reusable helpers for future Alpha intelligence layers
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional


BASE_DIR = Path("/home/ap3x/scripts/rh_tracker")
STATE_DIR = BASE_DIR / "state"
SNAPSHOT_DIR = STATE_DIR / "snapshots"
ARCHIVE_DIR = STATE_DIR / "archive"

SCHEMA_VERSION = "0.1"
SYSTEM_ID = "rackmain1-wall-ui"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def ensure_dirs() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)


def safe_load_json(path: Path, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if default is None:
        default = {}

    try:
        if not path.exists():
            return default

        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    except json.JSONDecodeError:
        corrupt_path = path.with_suffix(path.suffix + f".corrupt-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
        try:
            path.rename(corrupt_path)
        except OSError:
            pass
        return default

    except OSError:
        return default


def safe_write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            json.dump(data, tmp, indent=2, sort_keys=False)
            tmp.write("\n")

        os.replace(tmp_name, path)

    finally:
        if os.path.exists(tmp_name):
            try:
                os.remove(tmp_name)
            except OSError:
                pass


def default_current_state() -> Dict[str, Any]:
    ts = now_iso()
    return {
        "schema_version": SCHEMA_VERSION,
        "system_id": SYSTEM_ID,
        "generated_at": ts,
        "health": {
            "overall_state": "STABLE",
            "threat_level": "GREEN",
            "score": 0,
            "summary": "Operational state initialized. No active intelligence yet.",
        },
        "last_snapshot_at": ts,
        "last_transition_at": None,
        "active_conditions": [],
        "active_events": [],
        "recent_recoveries": [],
        "data_quality": {
            "telemetry_available": False,
            "weather_available": False,
            "finance_available": False,
            "latency_available": False,
            "service_status_available": False,
            "errors": [],
        },
    }


def default_threat_state() -> Dict[str, Any]:
    ts = now_iso()
    return {
        "schema_version": SCHEMA_VERSION,
        "current": {
            "level": "GREEN",
            "score": 0,
            "state": "STABLE",
            "summary": "No active operational threat.",
            "reasons": [],
            "updated_at": ts,
        },
        "previous": {
            "level": None,
            "score": None,
            "state": None,
            "updated_at": None,
        },
        "transition": {
            "type": "NO_CHANGE",
            "from_level": None,
            "to_level": "GREEN",
            "from_score": None,
            "to_score": 0,
            "cause": [],
            "occurred_at": ts,
        },
        "history": [
            {
                "timestamp": ts,
                "level": "GREEN",
                "score": 0,
                "state": "STABLE",
                "reasons": [],
            }
        ],
    }


def default_service_state() -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "services": {},
    }


def default_latency_state() -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "endpoints": {},
    }


def default_resource_state() -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "resources": {},
    }


def default_event_log() -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "events": [],
    }


def default_baselines() -> Dict[str, Any]:
    ts = now_iso()
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": ts,
        "baseline_policy": {
            "minimum_samples_required": 20,
            "rolling_window_hours": 24,
            "auto_adjust_enabled": True,
            "ignore_outliers": True,
        },
        "resources": {},
        "latency": {},
    }


def default_snapshot_latest() -> Dict[str, Any]:
    ts = now_iso()
    return {
        "schema_version": SCHEMA_VERSION,
        "timestamp": ts,
        "summary": {
            "overall_state": "STABLE",
            "threat_level": "GREEN",
            "active_alerts": 0,
            "active_watch_items": 0,
            "recent_recoveries": 0,
        },
        "resources": {},
        "services": {},
        "latency": {},
        "weather": {},
    }


STATE_FILES = {
    "current_state.json": default_current_state,
    "threat_state.json": default_threat_state,
    "service_state.json": default_service_state,
    "latency_state.json": default_latency_state,
    "resource_state.json": default_resource_state,
    "event_log.json": default_event_log,
    "baselines.json": default_baselines,
    "snapshots/snapshot_latest.json": default_snapshot_latest,
}


def initialize_state_files(overwrite: bool = False) -> None:
    ensure_dirs()

    for relative_path, factory in STATE_FILES.items():
        path = STATE_DIR / relative_path

        if path.exists() and not overwrite:
            continue

        safe_write_json(path, factory())


def get_state_path(filename: str) -> Path:
    return STATE_DIR / filename


def load_state(filename: str, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return safe_load_json(get_state_path(filename), default)


def write_state(filename: str, data: Dict[str, Any]) -> None:
    safe_write_json(get_state_path(filename), data)


def update_generated_timestamp(filename: str) -> Dict[str, Any]:
    data = load_state(filename)
    data["generated_at"] = now_iso()
    write_state(filename, data)
    return data

def priority_from_threat_level(level: str, transition_type: str = "NO_CHANGE") -> str:
    """
    Convert threat level + transition type into event priority.
    """

    if transition_type == "RECOVERY":
        return "INFO"

    if level == "GREEN":
        return "INFO"

    if level == "YELLOW":
        return "WATCH"

    if level == "ORANGE":
        return "ALERT"

    if level == "RED":
        return "CRITICAL"

    return "INFO"


def event_type_from_transition(transition_type: str, to_level: str) -> str:
    """
    Convert threat transition into an operational event type.
    """

    if transition_type == "DOWNGRADE" and to_level == "GREEN":
        return "RECOVERY"

    if transition_type == "ESCALATION":
        return "ESCALATION"

    if transition_type == "DOWNGRADE":
        return "DOWNGRADE"

    return "NO_CHANGE"


def build_event_id(category: str, source: str, event_type: str, timestamp: str) -> str:
    """
    Build stable-ish event ID using category/source/type/time.
    """

    safe_ts = (
        timestamp
        .replace("-", "")
        .replace(":", "")
        .replace("+", "")
        .replace(".", "")
    )

    return f"{category}-{source}-{event_type}-{safe_ts}".lower()


def append_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Append an operational event into event_log.json with simple dedupe behavior.

    Dedupe rules:
    - If an active event has the same dedupe_key and the same priority, do not append.
    - If severity changes, append the new event.
    - Recovery events are always allowed.
    """

    event_log = load_state("event_log.json", default_event_log())
    events = event_log.setdefault("events", [])

    dedupe_key = event.get("dedupe_key")
    priority = event.get("priority")
    event_type = event.get("type")

    if event_type != "RECOVERY":
        for existing in reversed(events):
            if existing.get("dedupe_key") != dedupe_key:
                continue

            if existing.get("active") is True and existing.get("recovered") is not True:
                if existing.get("priority") == priority:
                    return existing

    events.append(event)

    # Keep event log lightweight for now.
    # Phase 2 can add archival/pruning policy if needed.
    event_log["events"] = events[-500:]
    write_state("event_log.json", event_log)

    return event


def create_threat_transition_event(
    transition_type: str,
    from_level: Optional[str],
    to_level: str,
    from_score: Optional[int],
    to_score: int,
    reasons: list[str],
    timestamp: str,
) -> Optional[Dict[str, Any]]:
    """
    Create an event for meaningful threat transitions.

    Does not create events for NO_CHANGE.
    """

    event_type = event_type_from_transition(transition_type, to_level)

    if event_type == "NO_CHANGE":
        return None

    priority = priority_from_threat_level(to_level, event_type)

    if event_type == "RECOVERY":
        title = "Threat level recovered"
        message = f"Threat level reduced {from_level} → {to_level}."
        active = False
        recovered = True
    elif event_type == "ESCALATION":
        title = "Threat level escalated"
        message = f"Threat level escalated {from_level} → {to_level}."
        active = True
        recovered = False
    else:
        title = "Threat level downgraded"
        message = f"Threat level downgraded {from_level} → {to_level}."
        active = True
        recovered = False

    if reasons:
        message = f"{message} Cause: {'; '.join(reasons)}"

    event = {
        "event_id": build_event_id("threat", "global", event_type, timestamp),
        "timestamp": timestamp,
        "priority": priority,
        "category": "threat",
        "source": "global",
        "type": event_type,
        "title": title,
        "message": message,
        "state_before": from_level,
        "state_after": to_level,
        "score_before": from_score,
        "score_after": to_score,
        "dedupe_key": f"threat:global:{event_type.lower()}:{to_level.lower()}",
        "dedupe_ttl_minutes": 30,
        "active": active,
        "acknowledged": False,
        "recovered": recovered,
        "recovered_at": timestamp if recovered else None,
        "metadata": {
            "from_level": from_level,
            "to_level": to_level,
            "from_score": from_score,
            "to_score": to_score,
            "reasons": reasons,
        },
    }

    return append_event(event)

def update_threat_state_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Phase 1 Bravo extension:
    Mirror the existing /api/threat payload into Alpha threat_state.json.

    This prevents Alpha from reporting GREEN while the existing UI threat
    system reports YELLOW/ORANGE/RED.
    """

    threat_state = load_state("threat_state.json", default_threat_state())
    ts = now_iso()

    current = threat_state.get("current", {})
    previous_level = current.get("level")
    previous_score = current.get("score")
    previous_state = current.get("state")

    level = payload.get("level", "UNKNOWN")
    score = payload.get("score", 0)
    reasons = payload.get("reasons", [])

    if level == "GREEN":
        op_state = "STABLE"
    elif level == "YELLOW":
        op_state = "WATCH"
    elif level == "ORANGE":
        op_state = "DEGRADING"
    elif level == "RED":
        op_state = "CRITICAL"
    else:
        op_state = "UNKNOWN"

    if previous_level is None or previous_level == level:
        transition_type = "NO_CHANGE"
    else:
        severity_order = {
            "GREEN": 0,
            "YELLOW": 1,
            "ORANGE": 2,
            "RED": 3,
            "UNKNOWN": -1,
        }

        previous_rank = severity_order.get(previous_level, -1)
        current_rank = severity_order.get(level, -1)

        if current_rank > previous_rank:
            transition_type = "ESCALATION"
        elif current_rank < previous_rank:
            transition_type = "DOWNGRADE"
        else:
            transition_type = "NO_CHANGE"

    threat_state["previous"] = {
        "level": previous_level,
        "score": previous_score,
        "state": previous_state,
        "updated_at": current.get("updated_at"),
    }

    threat_state["current"] = {
        "level": level,
        "score": score,
        "state": op_state,
        "summary": payload.get("summary", payload.get("description", "Threat state updated from existing engine.")),
        "reasons": reasons,
        "updated_at": ts,
    }

    # Mirror threat readiness into current_state.json so /api/ops/state
    # does not report STABLE while threat_level is YELLOW/ORANGE/RED.
    current_state = load_state("current_state.json", default_current_state())

    current_state.setdefault("health", {})
    current_state["health"]["overall_state"] = op_state
    current_state["health"]["threat_level"] = level
    current_state["health"]["score"] = score

    if reasons:
        current_state["health"]["summary"] = "; ".join(reasons)
    else:
        current_state["health"]["summary"] = threat_state["current"]["summary"]

    current_state["generated_at"] = ts
    current_state["last_transition_at"] = ts if transition_type != "NO_CHANGE" else current_state.get("last_transition_at")

    write_state("current_state.json", current_state)

    threat_state["transition"] = {
        "type": transition_type,
        "from_level": previous_level,
        "to_level": level,
        "from_score": previous_score,
        "to_score": score,
        "cause": reasons,
        "occurred_at": ts,
    }

    history = threat_state.setdefault("history", [])
    history.append({
        "timestamp": ts,
        "level": level,
        "score": score,
        "state": op_state,
        "reasons": reasons,
    })

    threat_state["history"] = history[-288:]

    # Always keep active_conditions synchronized with current threat state,
    # even when there is no new transition event.
    current_state = load_state("current_state.json", default_current_state())
    active_conditions = current_state.setdefault("active_conditions", [])

    active_conditions = [
        item for item in active_conditions
        if item.get("source") != "threat"
    ]

    if level != "GREEN":
        active_conditions.append({
            "source": "threat",
            "level": level,
            "state": op_state,
            "score": score,
            "reasons": reasons,
            "updated_at": ts,
        })

    current_state["active_conditions"] = active_conditions
    write_state("current_state.json", current_state)

    if transition_type != "NO_CHANGE":
        created_event = create_threat_transition_event(
            transition_type=transition_type,
            from_level=previous_level,
            to_level=level,
            from_score=previous_score,
            to_score=score,
            reasons=reasons,
            timestamp=ts,
        )

        if created_event:
            current_state = load_state("current_state.json", default_current_state())

            if created_event.get("type") == "RECOVERY":
                recent_recoveries = current_state.setdefault("recent_recoveries", [])
                recent_recoveries.append(created_event)
                current_state["recent_recoveries"] = recent_recoveries[-10:]
            else:
                active_events = current_state.setdefault("active_events", [])
                active_events.append(created_event)
                current_state["active_events"] = active_events[-10:]

            write_state("current_state.json", current_state)

    write_state("threat_state.json", threat_state)
    return threat_state

def update_data_quality(
    telemetry_available: Optional[bool] = None,
    weather_available: Optional[bool] = None,
    finance_available: Optional[bool] = None,
    latency_available: Optional[bool] = None,
    service_status_available: Optional[bool] = None,
    errors: Optional[list[str]] = None,
) -> Dict[str, Any]:
    """
    Update current_state.json data_quality without touching the rest of the state.
    This is Phase 1 Bravo: truth-wiring existing system availability.
    """

    current = load_state("current_state.json", default_current_state())

    dq = current.setdefault("data_quality", {
        "telemetry_available": False,
        "weather_available": False,
        "finance_available": False,
        "latency_available": False,
        "service_status_available": False,
        "errors": [],
    })

    if telemetry_available is not None:
        dq["telemetry_available"] = bool(telemetry_available)

    if weather_available is not None:
        dq["weather_available"] = bool(weather_available)

    if finance_available is not None:
        dq["finance_available"] = bool(finance_available)

    if latency_available is not None:
        dq["latency_available"] = bool(latency_available)

    if service_status_available is not None:
        dq["service_status_available"] = bool(service_status_available)

    if errors is not None:
        dq["errors"] = errors

    current["generated_at"] = now_iso()
    current["last_snapshot_at"] = now_iso()

    current_health = current.setdefault("health", {})
    current_state = current_health.get("overall_state", "UNKNOWN")
    current_threat = current_health.get("threat_level", "UNKNOWN")

    quality_summary = build_quality_summary(dq)

    if current_state in ("STABLE", "UNKNOWN") and current_threat in ("GREEN", "UNKNOWN"):
        current_health["summary"] = quality_summary
    else:
        threat_state = load_state("threat_state.json", default_threat_state())
        reasons = threat_state.get("current", {}).get("reasons", [])

        if reasons:
            current_health["summary"] = "; ".join(reasons)
        else:
            current_health["summary"] = quality_summary

    write_state("current_state.json", current)
    return current

def normalize_service_status(raw_status):
    """
    Normalize service status into a small known set.
    """
    if raw_status is None:
        return "UNKNOWN"

    status = str(raw_status).strip().upper()

    if status in ("UP", "ONLINE", "OK", "HEALTHY", "RUNNING", "ACTIVE", "TRUE"):
        return "ONLINE"

    if status in ("DOWN", "OFFLINE", "FAIL", "FAILED", "UNHEALTHY", "INACTIVE", "FALSE"):
        return "OFFLINE"

    if status in ("DEGRADED", "WARNING", "WARN"):
        return "DEGRADED"

    return status or "UNKNOWN"


def extract_services_from_status_payload(status_payload):
    """
    Best-effort extractor for existing status() payload shapes.

    Supports common shapes:
    - {"services": {...}}
    - {"services": [...]}
    - {"containers": {...}}
    - {"containers": [...]}
    - {"checks": {...}}
    - {"checks": [...]}

    Returns:
    [
      {"name": "...", "status": "ONLINE", "raw": {...}}
    ]
    """
    candidates = []

    for key in ("services", "containers", "checks"):
        value = status_payload.get(key)
        if value:
            candidates = value
            break

    extracted = []

    if isinstance(candidates, dict):
        for name, details in candidates.items():
            if isinstance(details, dict):
                raw_status = (
                    details.get("status")
                    or details.get("state")
                    or details.get("health")
                    or details.get("online")
                    or details.get("running")
                )
                extracted.append({
                    "name": str(name),
                    "status": normalize_service_status(raw_status),
                    "raw": details
                })
            else:
                extracted.append({
                    "name": str(name),
                    "status": normalize_service_status(details),
                    "raw": details
                })

    elif isinstance(candidates, list):
        for item in candidates:
            if not isinstance(item, dict):
                continue

            name = (
                item.get("name")
                or item.get("service")
                or item.get("container")
                or item.get("id")
                or item.get("target")
                or "unknown_service"
            )

            raw_status = (
                item.get("status")
                or item.get("state")
                or item.get("health")
                or item.get("online")
                or item.get("running")
            )

            extracted.append({
                "name": str(name),
                "status": normalize_service_status(raw_status),
                "raw": item
            })

    return extracted

def update_latency_state_from_measurement(name, latency_ms, source="internal"):
    """
    Phase 5A:
    Persist lightweight latency baseline intelligence.

    This enriches the latency probe with rolling averages, baseline,
    spike counts, jitter, volatility, and trend classification.

    This does not create events.
    This does not alert.
    """
    from datetime import datetime, timedelta

    latency_state_payload = load_state(
        "latency_state.json",
        default_latency_state()
    )

    now = now_iso()

    probes = latency_state_payload.get("probes", {})
    if not isinstance(probes, dict):
        probes = {}

    previous = probes.get(name, {})
    if not isinstance(previous, dict):
        previous = {}

    samples = previous.get("samples", [])
    if not isinstance(samples, list):
        samples = []

    latest_latency = round(float(latency_ms), 2)

    sample = {
        "timestamp": now,
        "latency_ms": latest_latency,
        "source": source
    }

    samples.append(sample)

    # Keep the local model lightweight. 50 samples is the locked existing behavior.
    samples = samples[-50:]

    def _sample_value(item):
        try:
            return float(item.get("latency_ms", 0) or 0)
        except Exception:
            return 0.0

    def _parse_sample_time(item):
        raw = item.get("timestamp")
        if not raw:
            return None
        try:
            return datetime.fromisoformat(str(raw))
        except Exception:
            return None

    values = [
        _sample_value(item)
        for item in samples
        if _sample_value(item) > 0
    ]

    avg_latency = round(sum(values) / len(values), 2) if values else 0
    max_latency = round(max(values), 2) if values else 0
    min_latency = round(min(values), 2) if values else 0

    now_dt = datetime.fromisoformat(now)

    def _values_since(minutes):
        cutoff = now_dt - timedelta(minutes=minutes)
        scoped = []
        for item in samples:
            item_dt = _parse_sample_time(item)
            if item_dt is None:
                continue
            if item_dt >= cutoff:
                value = _sample_value(item)
                if value > 0:
                    scoped.append(value)
        return scoped

    values_5m = _values_since(5)
    values_15m = _values_since(15)

    rolling_avg_5m = round(sum(values_5m) / len(values_5m), 2) if values_5m else avg_latency
    rolling_avg_15m = round(sum(values_15m) / len(values_15m), 2) if values_15m else avg_latency

    # Phase 5B:
    # Baseline is now a slower-moving reference instead of simply mirroring the
    # current retained sample window. This gives rolling averages something useful
    # to compare against without adding a database or external dependency.
    previous_baseline = previous.get("baseline_avg_latency_ms")
    try:
        previous_baseline = float(previous_baseline)
    except Exception:
        previous_baseline = 0.0

    current_window_avg = round(sum(values) / len(values), 2) if values else avg_latency

    if previous_baseline > 0 and len(values) >= 20:
        # Slow exponential smoothing. 90% old baseline, 10% current window.
        baseline_avg_latency_ms = round((previous_baseline * 0.90) + (current_window_avg * 0.10), 2)
    else:
        baseline_avg_latency_ms = current_window_avg

    baseline_min_latency_ms = min_latency
    baseline_max_latency_ms = max_latency

    # A spike is a meaningful jump above baseline, with a hard minimum to avoid
    # tiny natural wobble being treated as intelligence. Civilised, for once.
    spike_threshold = max(baseline_avg_latency_ms * 1.25, baseline_avg_latency_ms + 250)

    spike_count_5m = len([
        value for value in values_5m
        if value >= spike_threshold
    ])

    spike_count_15m = len([
        value for value in values_15m
        if value >= spike_threshold
    ])

    jitter_ms = round(max(values) - min(values), 2) if values else 0

    if jitter_ms >= 750 or spike_count_5m >= 3:
        volatility = "HIGH"
    elif jitter_ms >= 350 or spike_count_15m >= 3:
        volatility = "MEDIUM"
    else:
        volatility = "LOW"

    # Existing hard posture thresholds remain unchanged for contract safety.
    if avg_latency >= 3000:
        posture = "DEGRADED"
    elif avg_latency >= 1500:
        posture = "WATCH"
    else:
        posture = "STABLE"

    previous_trend = str(previous.get("trend") or "STABLE").upper()

    baseline_delta = round(rolling_avg_5m - baseline_avg_latency_ms, 2)
    short_vs_medium_delta = round(rolling_avg_5m - rolling_avg_15m, 2)

    if posture == "DEGRADED":
        trend = "DEGRADED"
    elif volatility == "HIGH":
        trend = "VOLATILE"
    elif baseline_delta >= 250 and short_vs_medium_delta >= 100 and spike_count_5m >= 2:
        trend = "WORSENING"
    elif baseline_delta >= 175 and short_vs_medium_delta >= 75:
        trend = "WORSENING"
    elif baseline_delta >= 125 or short_vs_medium_delta >= 100 or spike_count_15m >= 2:
        trend = "ELEVATED"
    elif previous_trend in ("DEGRADED", "WORSENING", "VOLATILE", "ELEVATED") and baseline_delta <= 75 and short_vs_medium_delta <= 25:
        trend = "RECOVERING"
    else:
        trend = "STABLE"

    previous_posture = str(previous.get("posture") or "UNKNOWN").upper()
    previous_stable_samples = int(previous.get("stable_samples_since_degradation") or 0)

    last_degraded_at = previous.get("last_degraded_at")
    last_recovered_at = previous.get("last_recovered_at")

    if posture in ("WATCH", "DEGRADED"):
        stable_samples_since_degradation = 0
        if previous_posture not in ("WATCH", "DEGRADED"):
            last_degraded_at = now
        recovery_confidence = "LOW"
    else:
        stable_samples_since_degradation = previous_stable_samples + 1
        if previous_posture in ("WATCH", "DEGRADED"):
            last_recovered_at = now

        # Phase 5D:
        # Confidence must account for trend quality, not just stable sample count.
        # A stable posture with worsening/elevated trend is not a fully confident
        # recovery. That would be optimism, and optimism is how dashboards lie.
        if trend in ("WORSENING", "VOLATILE"):
            recovery_confidence = "LOW"
        elif trend == "ELEVATED":
            recovery_confidence = "MEDIUM" if stable_samples_since_degradation >= 3 else "LOW"
        elif stable_samples_since_degradation >= 8:
            recovery_confidence = "HIGH"
        elif stable_samples_since_degradation >= 3:
            recovery_confidence = "MEDIUM"
        else:
            recovery_confidence = "LOW"

    probes[name] = {
        "name": name,
        "source": source,
        "latest_latency_ms": latest_latency,
        "avg_latency_ms": avg_latency,
        "min_latency_ms": min_latency,
        "max_latency_ms": max_latency,
        "sample_count": len(samples),
        "posture": posture,
        "trend": trend,
        "rolling_avg_5m": rolling_avg_5m,
        "rolling_avg_15m": rolling_avg_15m,
        "baseline_avg_latency_ms": baseline_avg_latency_ms,
        "baseline_min_latency_ms": baseline_min_latency_ms,
        "baseline_max_latency_ms": baseline_max_latency_ms,
        "baseline_delta_ms": baseline_delta,
        "short_vs_medium_delta_ms": short_vs_medium_delta,
        "baseline_generated_at": previous.get("baseline_generated_at") or now,
        "spike_threshold_ms": round(spike_threshold, 2),
        "spike_count_5m": spike_count_5m,
        "spike_count_15m": spike_count_15m,
        "jitter_ms": jitter_ms,
        "volatility": volatility,
        "recovery_confidence": recovery_confidence,
        "stable_samples_since_degradation": stable_samples_since_degradation,
        "last_degraded_at": last_degraded_at,
        "last_recovered_at": last_recovered_at,
        "last_seen_at": now,
        "samples": samples
    }

    latency_state_payload["schema_version"] = "0.2"
    latency_state_payload["system_id"] = latency_state_payload.get("system_id", "rackmain1-wall-ui")
    latency_state_payload["generated_at"] = now
    latency_state_payload["last_updated_at"] = now
    latency_state_payload["probes"] = probes
    latency_state_payload["observed_probe_count"] = len(probes)

    write_state("latency_state.json", latency_state_payload)

    return latency_state_payload

def classify_percent_metric(value, watch_threshold, degraded_threshold, critical_threshold=None):
    """
    Classify a percentage-style metric into Alpha posture.
    """
    try:
        value = float(value)
    except Exception:
        return "UNKNOWN"

    if critical_threshold is not None and value >= critical_threshold:
        return "CRITICAL"

    if value >= degraded_threshold:
        return "DEGRADED"

    if value >= watch_threshold:
        return "WATCH"

    return "STABLE"


def resource_posture_to_score(posture):
    """
    Lightweight resource score.
    """
    posture = str(posture or "UNKNOWN").upper()

    if posture == "CRITICAL":
        return 8

    if posture == "DEGRADED":
        return 6

    if posture == "WATCH":
        return 3

    return 0


def worst_resource_posture(postures):
    """
    Return worst posture from a list.
    """
    severity = {
        "UNKNOWN": 0,
        "STABLE": 1,
        "WATCH": 2,
        "DEGRADED": 3,
        "CRITICAL": 4
    }

    clean = [
        str(posture or "UNKNOWN").upper()
        for posture in postures
    ]

    if not clean:
        return "UNKNOWN"

    return max(clean, key=lambda item: severity.get(item, 0))


def update_resource_state_from_status_payload(status_payload):
    """
    Phase 2E:
    Persist lightweight resource baseline from status().system.

    Does not create events.
    Does not alert.
    """
    resource_state_payload = load_state(
        "resource_state.json",
        default_resource_state()
    )

    now = now_iso()

    system_payload = {}
    if isinstance(status_payload, dict):
        system_payload = status_payload.get("system", {}) or {}

    cpu_value = system_payload.get("cpu")
    memory_value = system_payload.get("memory")
    root_disk_value = system_payload.get("root_disk")
    media_disk_value = system_payload.get("media_disk")
    uptime_hours = system_payload.get("uptime_hours")
    load_payload = system_payload.get("load", {}) or {}

    resources = {
        "cpu": {
            "name": "cpu",
            "value": cpu_value,
            "unit": "percent",
            "posture": classify_percent_metric(cpu_value, 75, 90),
            "watch_threshold": 75,
            "degraded_threshold": 90,
            "critical_threshold": None
        },
        "memory": {
            "name": "memory",
            "value": memory_value,
            "unit": "percent",
            "posture": classify_percent_metric(memory_value, 75, 90),
            "watch_threshold": 75,
            "degraded_threshold": 90,
            "critical_threshold": None
        },
        "root_disk": {
            "name": "root_disk",
            "value": root_disk_value,
            "unit": "percent",
            "posture": classify_percent_metric(root_disk_value, 80, 90, 95),
            "watch_threshold": 80,
            "degraded_threshold": 90,
            "critical_threshold": 95
        },
        "media_disk": {
            "name": "media_disk",
            "value": media_disk_value,
            "unit": "percent",
            "posture": classify_percent_metric(media_disk_value, 85, 92, 97),
            "watch_threshold": 85,
            "degraded_threshold": 92,
            "critical_threshold": 97
        }
    }

    load = {
        "1m": load_payload.get("1m"),
        "5m": load_payload.get("5m"),
        "15m": load_payload.get("15m")
    }

    postures = [
        metric.get("posture")
        for metric in resources.values()
    ]

    overall_posture = worst_resource_posture(postures)

    resource_state_payload["schema_version"] = "0.1"
    resource_state_payload["system_id"] = resource_state_payload.get("system_id", "rackmain1-wall-ui")
    resource_state_payload["generated_at"] = now
    resource_state_payload["last_updated_at"] = now
    resource_state_payload["posture"] = overall_posture
    resource_state_payload["resources"] = resources
    resource_state_payload["load"] = load
    resource_state_payload["uptime_hours"] = uptime_hours

    write_state("resource_state.json", resource_state_payload)

    return resource_state_payload

def build_alpha_overview():
    """
    Phase 2F:
    Unified Alpha overview endpoint payload.

    This is intended to become the primary Wall-UI data feed.
    It does not create events.
    It does not mutate lifecycle state directly.
    It only reads already-refreshed Alpha memory summaries.
    """
    state_summary = build_ops_summary()
    history_summary = build_history_summary()
    trend_summary = build_trend_summary()
    event_summary = build_event_summary()
    service_summary = build_service_summary()
    latency_summary = build_latency_summary()
    resource_summary = build_resource_summary()

    current_state = state_summary.get("overall_state", "UNKNOWN")
    threat_level = state_summary.get("threat_level", "UNKNOWN")
    score = state_summary.get("score", 0)

    trend_direction = trend_summary.get("trend_direction", "UNKNOWN")
    volatility = trend_summary.get("volatility", "UNKNOWN")

    service_posture = service_summary.get("posture", "UNKNOWN")
    latency_posture = latency_summary.get("posture", "UNKNOWN")
    resource_posture = resource_summary.get("posture", "UNKNOWN")

    active_events_count = event_summary.get("counts", {}).get("active_events", 0)

    operator_summary_parts = []

    operator_summary_parts.append(
        f"Alpha posture is {current_state} with threat level {threat_level} and score {score}."
    )

    if trend_direction != "UNKNOWN":
        operator_summary_parts.append(
            f"Trend is {trend_direction} with {volatility} volatility."
        )

    operator_summary_parts.append(
        f"Services are {service_posture}, latency is {latency_posture}, and resources are {resource_posture}."
    )

    if active_events_count:
        operator_summary_parts.append(
            f"{active_events_count} active event(s) require attention."
        )
    else:
        operator_summary_parts.append(
            "No active operational events are currently logged."
        )

    operator_summary = " ".join(operator_summary_parts)

    return {
        "schema_version": "0.1",
        "system_id": state_summary.get("system_id", "rackmain1-wall-ui"),
        "generated_at": now_iso(),
        "operator_summary": operator_summary,
        "state": state_summary,
        "history": history_summary.get("history", {}),
        "trends": trend_summary,
        "events": event_summary,
        "services": service_summary,
        "latency": latency_summary,
        "resources": resource_summary,
        "weather": state_summary.get("weather", {})
    }



# GHOST-THREAT-TRANSITION-PREVENTION:
# Recover obsolete active threat transition notices when Alpha is GREEN.
#
# Compatibility note:
# The function name is retained because tracker_api and older callers may
# already import it. Its behavior now covers both ESCALATION and DOWNGRADE
# transition notices.
def recover_stale_threat_escalation_events() -> int:
    from datetime import datetime

    current_state = load_state(
        "current_state.json",
        default_current_state()
    )
    event_log = load_state(
        "event_log.json",
        default_event_log()
    )

    health = current_state.get("health", {}) or {}
    level = str(
        health.get("threat_level", "GREEN")
    ).upper()
    score = int(health.get("score", 0) or 0)

    if not (level == "GREEN" and score == 0):
        return 0

    now = (
        datetime.now()
        .astimezone()
        .isoformat(timespec="seconds")
    )

    transition_types = {
        "ESCALATION",
        "DOWNGRADE",
    }

    event_log_changed = 0
    current_state_changed = 0

    def patch_events(events):
        if not isinstance(events, list):
            return 0

        changed = 0

        for event in events:
            if not isinstance(event, dict):
                continue

            category = str(
                event.get("category")
                or event.get("details", {}).get("category")
                or ""
            ).lower()

            event_type = str(
                event.get("type")
                or event.get("details", {}).get("type")
                or ""
            ).upper()

            if category != "threat":
                continue

            if event_type not in transition_types:
                continue

            if (
                event.get("active") is not True
                or event.get("recovered") is True
            ):
                continue

            event["active"] = False
            event["recovered"] = True
            event["acknowledged"] = True
            event["recovered_at"] = now
            event["status"] = "recovered"

            metadata = event.setdefault("metadata", {})
            metadata["auto_recovered_reason"] = (
                "ghost threat-transition prevention: "
                "Alpha is GREEN / score 0"
            )
            metadata["auto_recovered_event_type"] = (
                event_type
            )

            event["resolution"] = (
                f"Auto-recovered obsolete threat "
                f"{event_type.lower()} transition because "
                "Alpha is GREEN / score 0."
            )

            changed += 1

        return changed

    event_log_changed = patch_events(
        event_log.get("events", [])
    )

    current_state_changed = patch_events(
        current_state.get("active_events", [])
    )

    if event_log_changed:
        event_log["generated_at"] = now
        event_log["last_lifecycle_run_at"] = now
        write_state("event_log.json", event_log)

    if current_state_changed:
        current_state["generated_at"] = now
        current_state["last_stale_threat_cleanup_at"] = now
        current_state[
            "last_stale_threat_cleanup_reason"
        ] = (
            "ghost threat-transition prevention: "
            "Alpha is GREEN / score 0"
        )
        write_state(
            "current_state.json",
            current_state
        )

    return event_log_changed + current_state_changed

def build_alpha_brief():
    recover_stale_threat_escalation_events()
    """
    Phase 2H:
    Compact Wall-UI payload.

    This is intentionally much smaller than /api/ops/overview.
    It is designed for frequent UI polling.
    """
    state_summary = build_ops_summary()
    trend_summary = build_trend_summary()
    event_summary = build_event_summary()
    service_summary = build_service_summary()
    latency_summary = build_latency_summary()
    resource_summary = build_resource_summary()

    events_counts = event_summary.get("counts", {})
    service_counts = service_summary.get("counts", {})
    latency_counts = latency_summary.get("counts", {})
    resource_counts = resource_summary.get("counts", {})

    latency_probes = latency_summary.get("probes", [])
    status_engine_latency = None

    for probe in latency_probes:
        if probe.get("name") == "status_engine":
            status_engine_latency = {
                "latest_latency_ms": probe.get("latest_latency_ms"),
                "avg_latency_ms": probe.get("avg_latency_ms"),
                "posture": probe.get("posture"),
                "trend": probe.get("trend"),
                "volatility": probe.get("volatility"),
                "recovery_confidence": probe.get("recovery_confidence"),
                "rolling_avg_5m": probe.get("rolling_avg_5m"),
                "rolling_avg_15m": probe.get("rolling_avg_15m"),
                "baseline_avg_latency_ms": probe.get("baseline_avg_latency_ms"),
                "baseline_delta_ms": probe.get("baseline_delta_ms"),
                "short_vs_medium_delta_ms": probe.get("short_vs_medium_delta_ms"),
                "spike_threshold_ms": probe.get("spike_threshold_ms"),
                "spike_count_5m": probe.get("spike_count_5m"),
                "spike_count_15m": probe.get("spike_count_15m"),
                "jitter_ms": probe.get("jitter_ms"),
                "stable_samples_since_degradation": probe.get("stable_samples_since_degradation"),
                "last_degraded_at": probe.get("last_degraded_at"),
                "last_recovered_at": probe.get("last_recovered_at"),
                "sample_count": probe.get("sample_count"),
                "last_seen_at": probe.get("last_seen_at")
            }
            break

    resource_metrics = {}

    for metric in resource_summary.get("resources", []):
        name = metric.get("name")
        if not name:
            continue

        resource_metrics[name] = {
            "value": metric.get("value"),
            "unit": metric.get("unit"),
            "posture": metric.get("posture")
        }

    operator_summary = (
        f"Alpha posture is {state_summary.get('overall_state', 'UNKNOWN')} "
        f"with threat level {state_summary.get('threat_level', 'UNKNOWN')} "
        f"and score {state_summary.get('score', 0)}. "
        f"Trend is {trend_summary.get('trend_direction', 'UNKNOWN')} "
        f"with {trend_summary.get('volatility', 'UNKNOWN')} volatility. "
        f"Services are {service_summary.get('posture', 'UNKNOWN')}, "
        f"latency is {latency_summary.get('posture', 'UNKNOWN')}, "
        f"and resources are {resource_summary.get('posture', 'UNKNOWN')}."
    )

    if events_counts.get("active_events", 0):
        operator_summary += f" {events_counts.get('active_events')} active event(s) require attention."
    else:
        operator_summary += " No active operational events are currently logged."

    return {
        "schema_version": "0.1",
        "system_id": state_summary.get("system_id", "rackmain1-wall-ui"),
        "generated_at": now_iso(),
        "state": {
            "overall_state": state_summary.get("overall_state", "UNKNOWN"),
            "threat_level": state_summary.get("threat_level", "UNKNOWN"),
            "score": state_summary.get("score", 0),
            "summary": state_summary.get("summary", "No summary available."),
            "operator_summary": operator_summary
        },
        "trend": {
            "direction": trend_summary.get("trend_direction", "UNKNOWN"),
            "volatility": trend_summary.get("volatility", "UNKNOWN"),
            "state_duration_minutes": trend_summary.get("state_duration_minutes", 0),
            "score_delta_1h": trend_summary.get("score_delta_1h", 0),
            "state_changes_1h": trend_summary.get("state_changes_1h", 0)
        },
        "events": {
            "active": events_counts.get("active_events", 0),
            "total": events_counts.get("total_events", 0),
            "recovered": events_counts.get("recovered_events", 0),
            "last_event_at": event_summary.get("last_event_at")
        },
        "services": {
            "posture": service_summary.get("posture", "UNKNOWN"),
            "summary": service_summary.get("summary", "No service summary available."),
            "counts": {
                "total": service_counts.get("total", 0),
                "online": service_counts.get("online", 0),
                "degraded": service_counts.get("degraded", 0),
                "offline": service_counts.get("offline", 0),
                "flapping": service_counts.get("flapping", 0)
            }
        },
        "latency": {
            "posture": latency_summary.get("posture", "UNKNOWN"),
            "summary": latency_summary.get("summary", "No latency summary available."),
            "counts": {
                "total": latency_counts.get("total", 0),
                "stable": latency_counts.get("stable", 0),
                "watch": latency_counts.get("watch", 0),
                "degraded": latency_counts.get("degraded", 0)
            },
            "status_engine": status_engine_latency
        },
        "resources": {
            "posture": resource_summary.get("posture", "UNKNOWN"),
            "summary": resource_summary.get("summary", "No resource summary available."),
            "counts": {
                "total": resource_counts.get("total", 0),
                "stable": resource_counts.get("stable", 0),
                "watch": resource_counts.get("watch", 0),
                "degraded": resource_counts.get("degraded", 0),
                "critical": resource_counts.get("critical", 0)
            },
            "metrics": resource_metrics,
            "load": resource_summary.get("load", {}),
            "uptime_hours": resource_summary.get("uptime_hours")
        },
        "data_quality": state_summary.get("data_quality", {}),
        "weather": state_summary.get("weather", {})
    }


def normalize_feed_priority(event: Dict[str, Any]) -> str:
    """
    Convert backend event priority/type into ONI Tactical Feed priority classes.
    Recovery is intentionally surfaced as RECOVERY even if the stored event priority is INFO.
    """
    event_type = str(event.get("type") or "").upper()
    priority = str(event.get("priority") or "INFO").upper()

    if event.get("recovered") is True or event_type == "RECOVERY":
        return "RECOVERY"

    if priority in {"INFO", "WATCH", "ALERT", "CRITICAL"}:
        return priority

    return "INFO"


def normalize_feed_status(event: Dict[str, Any]) -> str:
    """
    Convert event lifecycle flags into compact feed status.
    Do not rely on status alone; existing lifecycle uses active/recovered booleans.
    """
    if event.get("recovered") is True:
        return "recovered"

    if event.get("active") is True:
        return "active"

    return "observed"


def build_feed_item(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert a raw backend operational event into a UI-ready Tactical Feed entry.
    """
    category = event.get("category") or "alpha"
    source = event.get("source") or category
    event_type = event.get("type") or "OBSERVED"

    return {
        "id": event.get("event_id") or build_event_id(
            str(category),
            str(source),
            str(event_type),
            event.get("timestamp") or now_iso(),
        ),
        "timestamp": event.get("timestamp") or event.get("generated_at") or now_iso(),
        "priority": normalize_feed_priority(event),
        "source": category,
        "title": event.get("title") or "Operational event observed",
        "summary": event.get("message") or event.get("summary") or "Event observed without additional detail.",
        "status": normalize_feed_status(event),
        "dedupe_key": event.get("dedupe_key") or f"{category}:{source}:{event_type}",
        "ttl_minutes": event.get("dedupe_ttl_minutes") or 60,
        "details": {
            "category": category,
            "source": source,
            "type": event_type,
            "state_before": event.get("state_before"),
            "state_after": event.get("state_after"),
            "score_before": event.get("score_before"),
            "score_after": event.get("score_after"),
            "acknowledged": event.get("acknowledged", False),
            "active": event.get("active", False),
            "recovered": event.get("recovered", False),
            "recovered_at": event.get("recovered_at"),
            "metadata": event.get("metadata", {}),
        },
    }


def feed_sort_rank(item: Dict[str, Any]) -> tuple:
    """
    Sort active high-severity events first, then recoveries, then routine info.
    Timestamp remains the secondary sort so newer items win inside each class.
    """
    priority = str(item.get("priority") or "INFO").upper()
    status = str(item.get("status") or "observed").lower()

    priority_rank = {
        "CRITICAL": 0,
        "ALERT": 1,
        "WATCH": 2,
        "RECOVERY": 3,
        "INFO": 4,
    }.get(priority, 5)

    status_rank = {
        "active": 0,
        "recovered": 1,
        "observed": 2,
    }.get(status, 3)

    # Descending timestamp is handled by reverse=True below for timestamp text.
    return (priority_rank, status_rank, item.get("timestamp") or "")



def active_event_exists(dedupe_key: str) -> bool:
    """
    Return True if an unrecovered active event exists for this dedupe key.
    """
    event_log = load_state("event_log.json", default_event_log())

    for event in reversed(event_log.get("events", [])):
        if event.get("dedupe_key") != dedupe_key:
            continue

        if event.get("active") is True and event.get("recovered") is not True:
            return True

    return False


def mark_active_events_recovered(dedupe_key: str, timestamp: str) -> int:
    """
    Mark active events with this dedupe key as recovered.
    Returns number of events updated.
    """
    event_log = load_state("event_log.json", default_event_log())
    events = event_log.setdefault("events", [])
    updated = 0

    for event in events:
        if event.get("dedupe_key") != dedupe_key:
            continue

        if event.get("active") is True and event.get("recovered") is not True:
            event["active"] = False
            event["recovered"] = True
            event["recovered_at"] = timestamp
            updated += 1

    if updated:
        event_log["generated_at"] = timestamp
        write_state("event_log.json", event_log)

    return updated


def create_backup_freshness_event(
    name: str,
    age_hours: Optional[float],
    priority: str,
    event_type: str,
    title: str,
    message: str,
    timestamp: str,
    active: bool = True,
    recovered: bool = False,
) -> Dict[str, Any]:
    """
    Create a backup freshness lifecycle event.
    All backup freshness conditions share one dedupe key per backup name.
    """
    dedupe_key = f"backup:{name}:freshness"

    event = {
        "event_id": build_event_id("backup", name, event_type, timestamp),
        "timestamp": timestamp,
        "priority": priority,
        "category": "backup",
        "source": name,
        "type": event_type,
        "title": title,
        "message": message,
        "state_before": None,
        "state_after": "RECOVERED" if recovered else event_type,
        "score_before": None,
        "score_after": None,
        "dedupe_key": dedupe_key,
        "dedupe_ttl_minutes": 360,
        "active": active,
        "acknowledged": False,
        "recovered": recovered,
        "recovered_at": timestamp if recovered else None,
        "metadata": {
            "backup_name": name,
            "age_hours": age_hours,
        },
    }

    return append_event(event)


def sync_backup_events_from_payload(backups: Dict[str, Any]) -> None:
    """
    Phase 4B:
    Convert backup freshness into low-noise lifecycle events.

    Rules:
    - Missing backup: CRITICAL
    - 24h+ old: ALERT
    - 8h+ old: WATCH
    - Fresh again after an active issue: RECOVERY
    """
    if not isinstance(backups, dict):
        return

    timestamp = now_iso()

    for name, raw_age in backups.items():
        backup_name = str(name or "unknown")
        dedupe_key = f"backup:{backup_name}:freshness"

        try:
            age_hours = None if raw_age is None else float(raw_age)
        except (TypeError, ValueError):
            age_hours = None

        if age_hours is None:
            create_backup_freshness_event(
                backup_name,
                age_hours,
                "CRITICAL",
                "BACKUP_MISSING",
                "Backup missing",
                f"{backup_name} backup file is missing or unreadable.",
                timestamp,
                active=True,
                recovered=False,
            )
            continue

        if age_hours >= 24:
            create_backup_freshness_event(
                backup_name,
                age_hours,
                "ALERT",
                "BACKUP_STALE",
                "Backup stale",
                f"{backup_name} backup is stale at {age_hours:.1f}h old.",
                timestamp,
                active=True,
                recovered=False,
            )
            continue

        if age_hours >= 8:
            create_backup_freshness_event(
                backup_name,
                age_hours,
                "WATCH",
                "BACKUP_AGING",
                "Backup aging",
                f"{backup_name} backup is aging at {age_hours:.1f}h old.",
                timestamp,
                active=True,
                recovered=False,
            )
            continue

        if active_event_exists(dedupe_key):
            mark_active_events_recovered(dedupe_key, timestamp)
            create_backup_freshness_event(
                backup_name,
                age_hours,
                "INFO",
                "RECOVERY",
                "Backup freshness recovered",
                f"{backup_name} backup freshness recovered at {age_hours:.1f}h old.",
                timestamp,
                active=False,
                recovered=True,
            )


def create_service_lifecycle_event(
    service_name: str,
    status: str,
    previous_status: Optional[str],
    priority: str,
    event_type: str,
    title: str,
    message: str,
    timestamp: str,
    active: bool = True,
    recovered: bool = False,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Create a service lifecycle event.
    All availability states share one dedupe key per service.
    """
    dedupe_key = f"service:{service_name}:availability"

    event = {
        "event_id": build_event_id("service", service_name, event_type, timestamp),
        "timestamp": timestamp,
        "priority": priority,
        "category": "service",
        "source": service_name,
        "type": event_type,
        "title": title,
        "message": message,
        "state_before": previous_status,
        "state_after": status,
        "score_before": None,
        "score_after": None,
        "dedupe_key": dedupe_key,
        "dedupe_ttl_minutes": 60,
        "active": active,
        "acknowledged": False,
        "recovered": recovered,
        "recovered_at": timestamp if recovered else None,
        "metadata": metadata or {},
    }

    return append_event(event)


def sync_service_events_from_state() -> None:
    """
    Phase 4B:
    Convert service state transitions into low-noise lifecycle events.

    Uses service_state.json, which already tracks current and previous status.
    """
    service_state_payload = load_state(
        "service_state.json",
        default_service_state()
    )

    services = service_state_payload.get("services", {})
    if not isinstance(services, dict):
        return

    timestamp = now_iso()

    for name, service in services.items():
        if not isinstance(service, dict):
            continue

        service_name = str(name or service.get("name") or "unknown")
        status = str(service.get("status") or "UNKNOWN").upper()
        previous_status = service.get("previous_status")
        previous_status = str(previous_status).upper() if previous_status else None
        dedupe_key = f"service:{service_name}:availability"

        metadata = {
            "service_name": service_name,
            "status": status,
            "previous_status": previous_status,
            "failure_count": service.get("failure_count", 0),
            "recovery_count": service.get("recovery_count", 0),
            "status_changes": service.get("status_changes", 0),
            "is_flapping": service.get("is_flapping", False),
            "last_seen_at": service.get("last_seen_at"),
            "last_change_at": service.get("last_change_at"),
            "raw": service.get("raw"),
        }

        if status == "ONLINE":
            # If an active service availability event exists, close it regardless of
            # previous_status. The service state updater may already have normalized
            # previous_status back to ONLINE by the time event sync runs.
            if active_event_exists(dedupe_key):
                recovered_from = previous_status if previous_status and previous_status != "ONLINE" else "NON-ONLINE"
                mark_active_events_recovered(dedupe_key, timestamp)
                create_service_lifecycle_event(
                    service_name,
                    status,
                    recovered_from,
                    "INFO",
                    "RECOVERY",
                    "Service recovered",
                    f"{service_name} recovered: {recovered_from} → ONLINE.",
                    timestamp,
                    active=False,
                    recovered=True,
                    metadata=metadata,
                )
            continue

        if status == "DEGRADED":
            priority = "ALERT"
            event_type = "SERVICE_DEGRADED"
            title = "Service degraded"
            message = f"{service_name} service degraded."
        elif status == "OFFLINE":
            priority = "CRITICAL"
            event_type = "SERVICE_OFFLINE"
            title = "Service offline"
            message = f"{service_name} service offline."
        else:
            priority = "WATCH"
            event_type = "SERVICE_UNKNOWN"
            title = "Service status unknown"
            message = f"{service_name} service status is {status}."

        if service.get("is_flapping") is True:
            priority = "ALERT"
            message = f"{message} Flapping detected."

        create_service_lifecycle_event(
            service_name,
            status,
            previous_status,
            priority,
            event_type,
            title,
            message,
            timestamp,
            active=True,
            recovered=False,
            metadata=metadata,
        )


def parse_feed_timestamp(timestamp: Optional[str]):
    """
    Parse ISO timestamps for feed TTL decisions.
    Returns None if timestamp is unavailable or malformed.
    """
    if not timestamp:
        return None

    try:
        from datetime import datetime
        return datetime.fromisoformat(str(timestamp))
    except Exception:
        return None


def feed_item_expired(item: Dict[str, Any]) -> bool:
    """
    Decide whether a non-active feed item should be hidden from Tactical Feed.

    Active events are never expired here.
    Recovered/observed/info events respect ttl_minutes.
    The event remains in event_log.json; this only controls feed visibility.
    """
    if item.get("status") == "active":
        return False

    try:
        ttl_minutes = int(item.get("ttl_minutes") or 60)
    except Exception:
        ttl_minutes = 60

    # Defensive floor/ceiling so bad event data cannot make the feed useless.
    if ttl_minutes < 5:
        ttl_minutes = 5

    if ttl_minutes > 1440:
        ttl_minutes = 1440

    event_time = parse_feed_timestamp(item.get("timestamp"))
    if event_time is None:
        return False

    try:
        from datetime import datetime, timezone
        now = datetime.now(event_time.tzinfo or timezone.utc)
        age_minutes = (now - event_time).total_seconds() / 60
        return age_minutes > ttl_minutes
    except Exception:
        return False

def build_ops_feed(limit: int = 14) -> Dict[str, Any]:
    auto_recover_stale_threat_escalations()
    """
    Phase 4A:
    Backend-authoritative ONI Tactical Feed.

    This is intentionally a translator over existing event_log.json and lifecycle state.
    It does not create new routine refresh events.
    It does not mutate the Wall-UI layout contract.
    """
    event_log = load_state("event_log.json", default_event_log())
    raw_events = event_log.get("events", [])

    items = []
    seen = set()

    # Newest first before dedupe so the latest copy of a dedupe_key wins.
    for event in reversed(raw_events):
        if not isinstance(event, dict):
            continue

        item = build_feed_item(event)
        dedupe_key = item.get("dedupe_key") or item.get("id")

        # Keep active and recovered lifecycle variants distinct when necessary.
        lifecycle_key = f"{dedupe_key}:{item.get('status')}:{item.get('priority')}"

        if lifecycle_key in seen:
            continue

        if feed_item_expired(item):
            continue

        seen.add(lifecycle_key)
        items.append(item)

    def sort_key(item: Dict[str, Any]):
        priority = str(item.get("priority") or "INFO").upper()
        status = str(item.get("status") or "observed").lower()

        priority_rank = {
            "CRITICAL": 0,
            "ALERT": 1,
            "WATCH": 2,
            "RECOVERY": 3,
            "INFO": 4,
        }.get(priority, 5)

        status_rank = {
            "active": 0,
            "recovered": 1,
            "observed": 2,
        }.get(status, 3)

        # Negative timestamp sorting is awkward with ISO strings, so use this key
        # with a final reverse pass inside same rank below.
        return (priority_rank, status_rank)

    items = sorted(
        items,
        key=lambda item: (
            sort_key(item)[0],
            sort_key(item)[1],
            item.get("timestamp") or "",
        ),
    )

    # Preserve priority ordering while making newer timestamps appear first within same rank.
    grouped = {}
    for item in items:
        key = (sort_key(item)[0], sort_key(item)[1])
        grouped.setdefault(key, []).append(item)

    ordered = []
    for key in sorted(grouped.keys()):
        ordered.extend(
            sorted(
                grouped[key],
                key=lambda item: item.get("timestamp") or "",
                reverse=True,
            )
        )

    limited_items = ordered[:limit]

    counts = {
        "total": len(limited_items),
        "active": len([item for item in limited_items if item.get("status") == "active"]),
        "recovered": len([item for item in limited_items if item.get("status") == "recovered"]),
        "critical": len([item for item in limited_items if item.get("priority") == "CRITICAL"]),
        "alert": len([item for item in limited_items if item.get("priority") == "ALERT"]),
        "watch": len([item for item in limited_items if item.get("priority") == "WATCH"]),
        "recovery": len([item for item in limited_items if item.get("priority") == "RECOVERY"]),
        "info": len([item for item in limited_items if item.get("priority") == "INFO"]),
    }

    return {
        "schema_version": "0.1",
        "system_id": event_log.get("system_id", "rackmain1-wall-ui"),
        "generated_at": now_iso(),
        "counts": counts,
        "items": limited_items,
    }


def posture_to_event_priority(posture: str) -> str:
    """
    Convert resource/latency posture into Tactical Feed priority.
    """
    posture = str(posture or "UNKNOWN").upper()

    if posture == "CRITICAL":
        return "CRITICAL"

    if posture == "DEGRADED":
        return "ALERT"

    if posture == "WATCH":
        return "WATCH"

    return "INFO"


def create_resource_lifecycle_event(
    metric_name: str,
    metric: Dict[str, Any],
    event_type: str,
    priority: str,
    title: str,
    message: str,
    timestamp: str,
    active: bool = True,
    recovered: bool = False,
) -> Dict[str, Any]:
    """
    Create a resource lifecycle event.
    One dedupe key per resource metric.
    """
    dedupe_key = f"resource:{metric_name}:posture"

    event = {
        "event_id": build_event_id("resource", metric_name, event_type, timestamp),
        "timestamp": timestamp,
        "priority": priority,
        "category": "resource",
        "source": metric_name,
        "type": event_type,
        "title": title,
        "message": message,
        "state_before": None,
        "state_after": "STABLE" if recovered else metric.get("posture"),
        "score_before": None,
        "score_after": None,
        "dedupe_key": dedupe_key,
        "dedupe_ttl_minutes": 120,
        "active": active,
        "acknowledged": False,
        "recovered": recovered,
        "recovered_at": timestamp if recovered else None,
        "metadata": {
            "metric_name": metric_name,
            "value": metric.get("value"),
            "unit": metric.get("unit"),
            "posture": metric.get("posture"),
            "watch_threshold": metric.get("watch_threshold"),
            "degraded_threshold": metric.get("degraded_threshold"),
            "critical_threshold": metric.get("critical_threshold"),
        },
    }

    return append_event(event)


def sync_resource_events_from_state() -> None:
    """
    Phase 4D:
    Convert root/media disk posture changes into low-noise lifecycle events.

    CPU/memory are intentionally excluded for now because they are noisier.
    """
    resource_state_payload = load_state(
        "resource_state.json",
        default_resource_state()
    )

    resources = resource_state_payload.get("resources", {})
    if not isinstance(resources, dict):
        return

    timestamp = now_iso()
    watched_metrics = ("root_disk", "media_disk")

    for metric_name in watched_metrics:
        metric = resources.get(metric_name)
        if not isinstance(metric, dict):
            continue

        posture = str(metric.get("posture") or "UNKNOWN").upper()
        dedupe_key = f"resource:{metric_name}:posture"
        value = metric.get("value")
        unit = metric.get("unit") or ""

        if posture == "STABLE":
            if active_event_exists(dedupe_key):
                mark_active_events_recovered(dedupe_key, timestamp)
                create_resource_lifecycle_event(
                    metric_name,
                    metric,
                    "RECOVERY",
                    "INFO",
                    "Resource recovered",
                    f"{metric_name} returned to STABLE at {value}{'%' if unit == 'percent' else unit}.",
                    timestamp,
                    active=False,
                    recovered=True,
                )
            continue

        if posture == "UNKNOWN":
            continue

        priority = posture_to_event_priority(posture)
        event_type = f"RESOURCE_{posture}"
        title = "Resource posture changed"
        message = f"{metric_name} is {posture} at {value}{'%' if unit == 'percent' else unit}."

        create_resource_lifecycle_event(
            metric_name,
            metric,
            event_type,
            priority,
            title,
            message,
            timestamp,
            active=True,
            recovered=False,
        )


def create_latency_lifecycle_event(
    probe_name: str,
    probe: Dict[str, Any],
    event_type: str,
    priority: str,
    title: str,
    message: str,
    timestamp: str,
    active: bool = True,
    recovered: bool = False,
) -> Dict[str, Any]:
    """
    Create a latency lifecycle event.
    One dedupe key per latency probe.
    """
    dedupe_key = f"latency:{probe_name}:posture"

    event = {
        "event_id": build_event_id("latency", probe_name, event_type, timestamp),
        "timestamp": timestamp,
        "priority": priority,
        "category": "latency",
        "source": probe_name,
        "type": event_type,
        "title": title,
        "message": message,
        "state_before": None,
        "state_after": "STABLE" if recovered else probe.get("posture"),
        "score_before": None,
        "score_after": None,
        "dedupe_key": dedupe_key,
        "dedupe_ttl_minutes": 90,
        "active": active,
        "acknowledged": False,
        "recovered": recovered,
        "recovered_at": timestamp if recovered else None,
        "metadata": {
            "probe_name": probe_name,
            "source": probe.get("source"),
            "latest_latency_ms": probe.get("latest_latency_ms"),
            "avg_latency_ms": probe.get("avg_latency_ms"),
            "min_latency_ms": probe.get("min_latency_ms"),
            "max_latency_ms": probe.get("max_latency_ms"),
            "sample_count": probe.get("sample_count"),
            "posture": probe.get("posture"),
            "last_seen_at": probe.get("last_seen_at"),
        },
    }

    return append_event(event)


def sync_latency_events_from_state() -> None:
    """
    Phase 5C:
    Convert status_engine latency posture and predictive trend changes into
    low-noise lifecycle events.

    Event families:
    - latency:{probe}:posture = hard threshold posture lifecycle
    - latency:{probe}:trend = predictive warning lifecycle
    """
    latency_state_payload = load_state(
        "latency_state.json",
        default_latency_state()
    )

    probes = latency_state_payload.get("probes", {})
    if not isinstance(probes, dict):
        return

    timestamp = now_iso()
    watched_probes = ("status_engine",)

    for probe_name in watched_probes:
        probe = probes.get(probe_name)
        if not isinstance(probe, dict):
            continue

        posture = str(probe.get("posture") or "UNKNOWN").upper()
        trend = str(probe.get("trend") or "STABLE").upper()

        posture_dedupe_key = f"latency:{probe_name}:posture"
        trend_dedupe_key = f"latency:{probe_name}:trend"

        latest = probe.get("latest_latency_ms")
        avg = probe.get("avg_latency_ms")
        rolling_5m = probe.get("rolling_avg_5m")
        rolling_15m = probe.get("rolling_avg_15m")
        baseline = probe.get("baseline_avg_latency_ms")
        baseline_delta = probe.get("baseline_delta_ms")
        short_vs_medium_delta = probe.get("short_vs_medium_delta_ms")
        spike_count_5m = int(probe.get("spike_count_5m") or 0)
        spike_count_15m = int(probe.get("spike_count_15m") or 0)
        volatility = str(probe.get("volatility") or "LOW").upper()

        # Hard posture lifecycle remains unchanged and authoritative.
        if posture == "STABLE":
            if active_event_exists(posture_dedupe_key):
                mark_active_events_recovered(posture_dedupe_key, timestamp)
                create_latency_lifecycle_event(
                    probe_name,
                    probe,
                    "RECOVERY",
                    "INFO",
                    "Latency recovered",
                    f"{probe_name} latency returned to STABLE. Latest {latest}ms, avg {avg}ms.",
                    timestamp,
                    active=False,
                    recovered=True,
                )
        elif posture != "UNKNOWN":
            priority = posture_to_event_priority(posture)
            event_type = f"LATENCY_{posture}"
            title = "Latency posture changed"
            message = f"{probe_name} latency is {posture}. Latest {latest}ms, avg {avg}ms."

            create_latency_lifecycle_event(
                probe_name,
                probe,
                event_type,
                priority,
                title,
                message,
                timestamp,
                active=True,
                recovered=False,
            )

        # ALPHA-G14C:
        # Predictive latency trend warnings are too noisy for the wall feed.
        # Keep latency telemetry visible in /api/ops/latency and /api/ops/brief,
        # but only lifecycle-log latency when posture itself is not STABLE.
        # Hard posture events above still handle WATCH/DEGRADED conditions.
        predictive_warning = (
            posture not in ("STABLE", "UNKNOWN")
            and trend in ("WORSENING", "VOLATILE")
            and (
                spike_count_5m >= 2
                or spike_count_15m >= 3
                or float(baseline_delta or 0) >= 175
                or float(short_vs_medium_delta or 0) >= 100
                or volatility == "HIGH"
            )
        )

        if predictive_warning:
            event = {
                "event_id": build_event_id("latency", probe_name, "LATENCY_TREND_WARNING", timestamp),
                "timestamp": timestamp,
                "priority": "WATCH",
                "category": "latency",
                "source": probe_name,
                "type": "LATENCY_TREND_WARNING",
                "title": "Latency trend worsening",
                "message": (
                    f"{probe_name} latency trend is {trend}. "
                    f"Rolling 5m {rolling_5m}ms vs baseline {baseline}ms "
                    f"({baseline_delta}ms delta); spikes 5m/15m: "
                    f"{spike_count_5m}/{spike_count_15m}."
                ),
                "state_before": None,
                "state_after": trend,
                "score_before": None,
                "score_after": None,
                "dedupe_key": trend_dedupe_key,
                "dedupe_ttl_minutes": 60,
                "active": True,
                "acknowledged": False,
                "recovered": False,
                "recovered_at": None,
                "metadata": {
                    "probe_name": probe_name,
                    "source": probe.get("source"),
                    "posture": posture,
                    "trend": trend,
                    "volatility": volatility,
                    "latest_latency_ms": latest,
                    "avg_latency_ms": avg,
                    "rolling_avg_5m": rolling_5m,
                    "rolling_avg_15m": rolling_15m,
                    "baseline_avg_latency_ms": baseline,
                    "baseline_delta_ms": baseline_delta,
                    "short_vs_medium_delta_ms": short_vs_medium_delta,
                    "spike_count_5m": spike_count_5m,
                    "spike_count_15m": spike_count_15m,
                    "sample_count": probe.get("sample_count"),
                    "last_seen_at": probe.get("last_seen_at"),
                },
            }
            append_event(event)
        else:
            if active_event_exists(trend_dedupe_key):
                mark_active_events_recovered(trend_dedupe_key, timestamp)

                event = {
                    "event_id": build_event_id("latency", probe_name, "LATENCY_TREND_RECOVERY", timestamp),
                    "timestamp": timestamp,
                    "priority": "RECOVERY",
                    "category": "latency",
                    "source": probe_name,
                    "type": "LATENCY_TREND_RECOVERY",
                    "title": "Latency trend stabilized",
                    "message": (
                        f"{probe_name} latency trend stabilized. "
                        f"Trend {trend}, rolling 5m {rolling_5m}ms, baseline {baseline}ms."
                    ),
                    "state_before": None,
                    "state_after": "STABLE",
                    "score_before": None,
                    "score_after": None,
                    "dedupe_key": trend_dedupe_key,
                    "dedupe_ttl_minutes": 60,
                    "active": False,
                    "acknowledged": False,
                    "recovered": True,
                    "recovered_at": timestamp,
                    "metadata": {
                        "probe_name": probe_name,
                        "source": probe.get("source"),
                        "posture": posture,
                        "trend": trend,
                        "volatility": volatility,
                        "latest_latency_ms": latest,
                        "avg_latency_ms": avg,
                        "rolling_avg_5m": rolling_5m,
                        "rolling_avg_15m": rolling_15m,
                        "baseline_avg_latency_ms": baseline,
                        "baseline_delta_ms": baseline_delta,
                        "short_vs_medium_delta_ms": short_vs_medium_delta,
                        "spike_count_5m": spike_count_5m,
                        "spike_count_15m": spike_count_15m,
                        "sample_count": probe.get("sample_count"),
                        "last_seen_at": probe.get("last_seen_at"),
                    },
                }
                append_event(event)

def build_resource_summary():
    """
    Phase 2E:
    Read-only resource baseline summary.
    """
    resource_state_payload = load_state(
        "resource_state.json",
        default_resource_state()
    )

    resources = resource_state_payload.get("resources", {})
    resource_list = list(resources.values())

    stable = [
        item for item in resource_list
        if item.get("posture") == "STABLE"
    ]

    watch = [
        item for item in resource_list
        if item.get("posture") == "WATCH"
    ]

    degraded = [
        item for item in resource_list
        if item.get("posture") == "DEGRADED"
    ]

    critical = [
        item for item in resource_list
        if item.get("posture") == "CRITICAL"
    ]

    unknown = [
        item for item in resource_list
        if item.get("posture") == "UNKNOWN"
    ]

    posture = resource_state_payload.get("posture", "UNKNOWN")

    if critical:
        summary = f"{len(critical)} resource metric(s) critical."
    elif degraded:
        summary = f"{len(degraded)} resource metric(s) degraded."
    elif watch:
        summary = f"{len(watch)} resource metric(s) elevated."
    elif resource_list and not unknown:
        summary = f"{len(resource_list)} resource metric(s) stable."
    elif resource_list:
        summary = f"{len(resource_list)} resource metric(s) observed with incomplete data."
    else:
        summary = "No resource metrics observed yet."

    return {
        "schema_version": "0.1",
        "system_id": resource_state_payload.get("system_id", "rackmain1-wall-ui"),
        "generated_at": now_iso(),
        "posture": posture,
        "summary": summary,
        "counts": {
            "total": len(resource_list),
            "stable": len(stable),
            "watch": len(watch),
            "degraded": len(degraded),
            "critical": len(critical),
            "unknown": len(unknown)
        },
        "resources": resource_list,
        "load": resource_state_payload.get("load", {}),
        "uptime_hours": resource_state_payload.get("uptime_hours")
    }

def resource_posture_to_state(posture):
    """
    Map resource posture into Alpha state vocabulary.
    """
    posture = str(posture or "UNKNOWN").upper()

    if posture == "STABLE":
        return "STABLE"

    if posture == "WATCH":
        return "WATCH"

    if posture == "DEGRADED":
        return "DEGRADING"

    if posture == "CRITICAL":
        return "CRITICAL"

    return "UNKNOWN"


def build_resource_active_condition(resource_summary):
    """
    Build an active condition from resource summary only when resources are not stable.
    """
    posture = resource_summary.get("posture", "UNKNOWN")
    counts = resource_summary.get("counts", {})

    if posture == "STABLE":
        return None

    if posture == "UNKNOWN":
        return None

    active_metrics = [
        resource for resource in resource_summary.get("resources", [])
        if resource.get("posture") in ("WATCH", "DEGRADED", "CRITICAL")
    ]

    return {
        "source": "resources",
        "level": posture,
        "state": resource_posture_to_state(posture),
        "score": resource_posture_to_score(posture),
        "summary": resource_summary.get("summary", "Resource posture changed."),
        "counts": counts,
        "active_metrics": active_metrics,
        "updated_at": now_iso()
    }


def sync_resource_condition_into_current_state():
    """
    Phase 2E.1:
    Fold resource posture into current_state.json active_conditions.

    Rules:
    - STABLE resources do not create an active condition.
    - WATCH/DEGRADED/CRITICAL resources create or update one resource condition.
    - Resource score can raise overall health if worse than current score.
    - Resource state does not lower existing weather/service/latency posture.
    - No event generation here.
    """
    current_state_payload = load_state(
        "current_state.json",
        default_current_state()
    )

    resource_summary = build_resource_summary()
    resource_condition = build_resource_active_condition(resource_summary)

    health = current_state_payload.setdefault("health", {})
    active_conditions = current_state_payload.setdefault("active_conditions", [])

    # Remove prior resource condition so we can replace it cleanly.
    active_conditions = [
        condition for condition in active_conditions
        if condition.get("source") != "resources"
    ]

    if resource_condition:
        active_conditions.append(resource_condition)

        current_score = int(health.get("score", 0) or 0)
        resource_score = int(resource_condition.get("score", 0) or 0)

        if resource_score > current_score:
            health["score"] = resource_score
            health["overall_state"] = resource_condition.get("state", "WATCH")
            health["threat_level"] = resource_condition.get("level", "WATCH")
            health["summary"] = resource_condition.get("summary", "Resource condition active.")

    current_state_payload["active_conditions"] = active_conditions
    current_state_payload["generated_at"] = now_iso()
    current_state_payload["resource_posture"] = resource_summary.get("posture", "UNKNOWN")
    current_state_payload["resource_counts"] = resource_summary.get("counts", {})

    write_state("current_state.json", current_state_payload)

    return current_state_payload

def build_latency_summary():
    """
    Phase 2D:
    Read-only latency baseline summary.
    """
    latency_state_payload = load_state(
        "latency_state.json",
        default_latency_state()
    )

    probes = latency_state_payload.get("probes", {})
    probe_list = list(probes.values())

    stable = [
        probe for probe in probe_list
        if probe.get("posture") == "STABLE"
    ]

    watch = [
        probe for probe in probe_list
        if probe.get("posture") == "WATCH"
    ]

    degraded = [
        probe for probe in probe_list
        if probe.get("posture") == "DEGRADED"
    ]

    if degraded:
        posture = "DEGRADED"
        summary = f"{len(degraded)} latency probe(s) degraded."
    elif watch:
        posture = "WATCH"
        summary = f"{len(watch)} latency probe(s) elevated."
    elif probe_list:
        posture = "STABLE"
        summary = f"{len(probe_list)} latency probe(s) stable."
    else:
        posture = "UNKNOWN"
        summary = "No latency probes observed yet."

    return {
        "schema_version": latency_state_payload.get("schema_version", "0.1"),
        "system_id": latency_state_payload.get("system_id", "rackmain1-wall-ui"),
        "generated_at": now_iso(),
        "posture": posture,
        "summary": summary,
        "counts": {
            "total": len(probe_list),
            "stable": len(stable),
            "watch": len(watch),
            "degraded": len(degraded)
        },
        "probes": probe_list
    }

def latency_posture_to_state(posture):
    """
    Map latency posture into Alpha state vocabulary.
    """
    posture = str(posture or "UNKNOWN").upper()

    if posture == "STABLE":
        return "STABLE"

    if posture == "WATCH":
        return "WATCH"

    if posture == "DEGRADED":
        return "DEGRADING"

    if posture == "CRITICAL":
        return "CRITICAL"

    return "UNKNOWN"


def latency_posture_to_score(posture, counts=None):
    """
    Lightweight latency score.
    Does not replace weather/service score unless more severe.
    """
    posture = str(posture or "UNKNOWN").upper()
    counts = counts or {}

    degraded = int(counts.get("degraded", 0) or 0)
    watch = int(counts.get("watch", 0) or 0)

    if posture == "CRITICAL":
        return 8

    if degraded > 0:
        return min(6, 4 + degraded)

    if watch > 0:
        return min(4, 2 + watch)

    if posture == "WATCH":
        return 3

    return 0


def build_latency_active_condition(latency_summary):
    """
    Build an active condition from latency summary only when latency is not stable.
    """
    posture = latency_summary.get("posture", "UNKNOWN")
    counts = latency_summary.get("counts", {})

    if posture == "STABLE":
        return None

    if posture == "UNKNOWN":
        return None

    return {
        "source": "latency",
        "level": posture,
        "state": latency_posture_to_state(posture),
        "score": latency_posture_to_score(posture, counts),
        "summary": latency_summary.get("summary", "Latency posture changed."),
        "counts": counts,
        "updated_at": now_iso()
    }


def sync_latency_condition_into_current_state():
    """
    Phase 2D.1:
    Fold latency posture into current_state.json active_conditions.

    Rules:
    - STABLE latency does not create an active condition.
    - WATCH/DEGRADED/CRITICAL latency creates or updates one latency condition.
    - Latency score can raise overall health if worse than current score.
    - Latency state does not lower existing weather/service posture.
    - No event generation here.
    """
    current_state_payload = load_state(
        "current_state.json",
        default_current_state()
    )

    latency_summary = build_latency_summary()
    latency_condition = build_latency_active_condition(latency_summary)

    health = current_state_payload.setdefault("health", {})
    active_conditions = current_state_payload.setdefault("active_conditions", [])

    # Remove prior latency condition so we can replace it cleanly.
    active_conditions = [
        condition for condition in active_conditions
        if condition.get("source") != "latency"
    ]

    if latency_condition:
        active_conditions.append(latency_condition)

        current_score = int(health.get("score", 0) or 0)
        latency_score = int(latency_condition.get("score", 0) or 0)

        if latency_score > current_score:
            health["score"] = latency_score
            health["overall_state"] = latency_condition.get("state", "WATCH")
            health["threat_level"] = latency_condition.get("level", "WATCH")
            health["summary"] = latency_condition.get("summary", "Latency condition active.")

    current_state_payload["active_conditions"] = active_conditions
    current_state_payload["generated_at"] = now_iso()
    current_state_payload["latency_posture"] = latency_summary.get("posture", "UNKNOWN")
    current_state_payload["latency_counts"] = latency_summary.get("counts", {})

    write_state("current_state.json", current_state_payload)

    return current_state_payload

def update_service_state_from_status_payload(status_payload):
    """
    Phase 2C:
    Persist lightweight service stability memory from the existing status() payload.

    Does not create events.
    Does not alert.
    Does not mutate event_log.json.
    """
    service_state_payload = load_state(
        "service_state.json",
        default_service_state()
    )

    now = now_iso()
    extracted_services = extract_services_from_status_payload(status_payload)

    services = service_state_payload.get("services", {})

    for service in extracted_services:
        name = service.get("name", "unknown_service")
        normalized_status = service.get("status", "UNKNOWN")

        previous = services.get(name, {})
        previous_status = previous.get("status", "UNKNOWN")

        status_changed = previous_status != normalized_status

        if not previous:
            services[name] = {
                "name": name,
                "status": normalized_status,
                "previous_status": "UNKNOWN",
                "first_seen_at": now,
                "last_seen_at": now,
                "last_change_at": now,
                "failure_count": 0,
                "recovery_count": 0,
                "status_changes": 0,
                "is_flapping": False,
                "raw": service.get("raw")
            }
            continue

        previous["previous_status"] = previous_status
        previous["status"] = normalized_status
        previous["last_seen_at"] = now
        previous["raw"] = service.get("raw")

        if status_changed:
            previous["last_change_at"] = now
            previous["status_changes"] = int(previous.get("status_changes", 0) or 0) + 1

            if normalized_status in ("OFFLINE", "DEGRADED"):
                previous["failure_count"] = int(previous.get("failure_count", 0) or 0) + 1

            if previous_status in ("OFFLINE", "DEGRADED") and normalized_status == "ONLINE":
                previous["recovery_count"] = int(previous.get("recovery_count", 0) or 0) + 1

        previous["is_flapping"] = int(previous.get("status_changes", 0) or 0) >= 4

        services[name] = previous

    service_state_payload["schema_version"] = "0.1"
    service_state_payload["system_id"] = service_state_payload.get("system_id", "rackmain1-wall-ui")
    service_state_payload["generated_at"] = now
    service_state_payload["last_updated_at"] = now
    service_state_payload["services"] = services
    service_state_payload["observed_service_count"] = len(services)

    write_state("service_state.json", service_state_payload)

    return service_state_payload


def build_service_summary():
    """
    Phase 2C:
    Read-only service stability summary.
    """
    service_state_payload = load_state(
        "service_state.json",
        default_service_state()
    )

    services = service_state_payload.get("services", {})

    service_list = list(services.values())

    online = [
        service for service in service_list
        if service.get("status") == "ONLINE"
    ]

    degraded = [
        service for service in service_list
        if service.get("status") == "DEGRADED"
    ]

    offline = [
        service for service in service_list
        if service.get("status") == "OFFLINE"
    ]

    unknown = [
        service for service in service_list
        if service.get("status") not in ("ONLINE", "DEGRADED", "OFFLINE")
    ]

    flapping = [
        service for service in service_list
        if service.get("is_flapping") is True
        and service.get("status") in {"OFFLINE", "DEGRADED", "UNKNOWN"}
    ]

    if offline:
        posture = "DEGRADED"
        summary = f"{len(offline)} service(s) offline."
    elif degraded:
        posture = "WATCH"
        summary = f"{len(degraded)} service(s) degraded."
    elif flapping:
        posture = "WATCH"
        summary = f"{len(flapping)} service(s) showing instability."
    elif service_list:
        posture = "STABLE"
        summary = f"{len(service_list)} service(s) observed and stable."
    else:
        posture = "UNKNOWN"
        summary = "No services observed yet."

    return {
        "schema_version": "0.1",
        "system_id": service_state_payload.get("system_id", "rackmain1-wall-ui"),
        "generated_at": now_iso(),
        "posture": posture,
        "summary": summary,
        "counts": {
            "total": len(service_list),
            "online": len(online),
            "degraded": len(degraded),
            "offline": len(offline),
            "unknown": len(unknown),
            "flapping": len(flapping)
        },
        "services": service_list
    }

def service_posture_to_state(posture):
    """
    Map service posture into Alpha state vocabulary.
    """
    posture = str(posture or "UNKNOWN").upper()

    if posture == "STABLE":
        return "STABLE"

    if posture == "WATCH":
        return "WATCH"

    if posture == "DEGRADED":
        return "DEGRADING"

    if posture == "CRITICAL":
        return "CRITICAL"

    return "UNKNOWN"


def service_posture_to_score(posture, counts=None):
    """
    Lightweight service score.
    Does not replace weather score unless more severe.
    """
    posture = str(posture or "UNKNOWN").upper()
    counts = counts or {}

    offline = int(counts.get("offline", 0) or 0)
    degraded = int(counts.get("degraded", 0) or 0)
    flapping = int(counts.get("flapping", 0) or 0)

    if posture == "CRITICAL":
        return 8

    if offline > 0:
        return min(7, 4 + offline)

    if degraded > 0:
        return min(5, 3 + degraded)

    if flapping > 0:
        return min(4, 2 + flapping)

    if posture == "WATCH":
        return 3

    return 0


def build_service_active_condition(service_summary):
    """
    Build an active condition from service summary only when services are not stable.
    """
    posture = service_summary.get("posture", "UNKNOWN")
    counts = service_summary.get("counts", {})

    if posture == "STABLE":
        return None

    if posture == "UNKNOWN":
        return None

    return {
        "source": "services",
        "level": posture,
        "state": service_posture_to_state(posture),
        "score": service_posture_to_score(posture, counts),
        "summary": service_summary.get("summary", "Service posture changed."),
        "counts": counts,
        "updated_at": now_iso()
    }


def sync_service_condition_into_current_state():
    """
    Phase 2C.1:
    Fold service posture into current_state.json active_conditions.

    Rules:
    - STABLE services do not create an active condition.
    - WATCH/DEGRADED/CRITICAL services create or update one service condition.
    - Service score can raise overall health if worse than current score.
    - Service state does not lower an existing weather/threat posture.
    - No event generation here.
    """
    current_state_payload = load_state(
        "current_state.json",
        default_current_state()
    )

    service_summary = build_service_summary()
    service_condition = build_service_active_condition(service_summary)

    health = current_state_payload.setdefault("health", {})
    active_conditions = current_state_payload.setdefault("active_conditions", [])

    # Remove prior service condition so we can replace it cleanly.
    active_conditions = [
        condition for condition in active_conditions
        if condition.get("source") != "services"
    ]

    if service_condition:
        active_conditions.append(service_condition)

        current_score = int(health.get("score", 0) or 0)
        service_score = int(service_condition.get("score", 0) or 0)

        if service_score > current_score:
            health["score"] = service_score
            health["overall_state"] = service_condition.get("state", "WATCH")
            health["threat_level"] = service_condition.get("level", "WATCH")
            health["summary"] = service_condition.get("summary", "Service condition active.")

    current_state_payload["active_conditions"] = active_conditions
    current_state_payload["generated_at"] = now_iso()
    current_state_payload["service_posture"] = service_summary.get("posture", "UNKNOWN")
    current_state_payload["service_counts"] = service_summary.get("counts", {})

    write_state("current_state.json", current_state_payload)

    return current_state_payload


def event_is_active(event: Dict[str, Any]) -> bool:
    """
    Canonical active-event test.

    Older state snapshots may contain active=True without status, while newer
    lifecycle entries use status/recovered flags. This helper prevents stale
    current_state.active_events from being treated as live truth.
    """
    if not isinstance(event, dict):
        return False

    if event.get("recovered") is True:
        return False

    if event.get("status") in ("recovered", "resolved", "expired"):
        return False

    return event.get("active") is True or event.get("status") == "active"


def dedupe_events_by_id(events: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    """
    Deduplicate events by event_id while preserving newest/rightmost copies.
    """
    deduped = {}
    ordered_keys = []

    for event in events or []:
        if not isinstance(event, dict):
            continue

        key = event.get("event_id") or event.get("id") or (
            str(event.get("dedupe_key", "")) + ":" + str(event.get("timestamp", ""))
        )

        if key not in deduped:
            ordered_keys.append(key)

        deduped[key] = event

    return [deduped[key] for key in ordered_keys]


def sync_current_state_event_cache() -> Dict[str, Any]:
    """
    Rebuild current_state.json active_events/recent_recoveries from event_log.json.

    /api/ops/brief and /api/ops/feed use event_log as authoritative lifecycle
    truth. This keeps the older current_state cache aligned so stale threat
    escalations do not linger as fake active events.
    """
    current_state = load_state("current_state.json", default_current_state())
    event_log = load_state("event_log.json", default_event_log())

    events = event_log.get("events", [])
    active_events = [event for event in events if event_is_active(event)]
    recovered_events = [
        event for event in events
        if event.get("recovered") is True or event.get("status") in ("recovered", "resolved")
    ]

    active_events = dedupe_events_by_id(active_events)
    recovered_events = dedupe_events_by_id(recovered_events)

    active_events.sort(key=get_event_timestamp)
    recovered_events.sort(key=get_event_timestamp)

    current_state["active_events"] = active_events[-10:]
    current_state["recent_recoveries"] = recovered_events[-10:]
    current_state["generated_at"] = now_iso()

    write_state("current_state.json", current_state)
    return current_state

def build_event_summary():
    auto_recover_stale_threat_escalations()
    """
    Phase 2B:
    Read-only operational event summary.
    Does not create, close, or mutate events.
    """
    current_state_payload = load_state(
        "current_state.json",
        default_current_state()
    )

    event_log_payload = load_state(
        "event_log.json",
        default_event_log()
    )

    events = event_log_payload.get("events", [])

    active_events = [
        event for event in events
        if event.get("active", event.get("status", "active") == "active") is True
        and event.get("recovered", False) is not True
        and event.get("type") != "RECOVERY"
    ]

    recovered_events = [
        event for event in events
        if event.get("recovered", False) is True
        or event.get("status") in ("recovered", "resolved")
        or event.get("type") == "RECOVERY"
    ]

    recent_events = events[-25:]

    last_event_at = None
    if events:
        last_event_at = events[-1].get("created_at") or events[-1].get("timestamp")

    return {
        "schema_version": "0.1",
        "system_id": current_state_payload.get("system_id", "rackmain1-wall-ui"),
        "generated_at": now_iso(),
        "counts": {
            "total_events": len(events),
            "active_events": len(active_events),
            "recovered_events": len(recovered_events),
            "recent_events": len(recent_events)
        },
        "last_event_at": last_event_at,
        "active_events": active_events,
        "recent_recoveries": recovered_events[-10:],
        "recent_events": recent_events
    }

def get_event_timestamp(event):
    """
    Return best available event timestamp.
    """
    return (
        event.get("created_at")
        or event.get("timestamp")
        or event.get("updated_at")
        or ""
    )


def find_active_events_by_source(events, source):
    """
    Find active events for a specific source.
    """
    return [
        event for event in events
        if event.get("source") == source
        and event.get("status", "active") == "active"
    ]


def mark_events_recovered(events, source, recovery_reason=None):
    """
    Mark active events from a source as recovered.
    Returns updated events and list of recovered event IDs.
    """
    recovered_ids = []
    recovered_at = now_iso()

    for event in events:
        if event.get("source") != source:
            continue

        if event.get("status", "active") != "active":
            continue

        event["status"] = "recovered"
        event["recovered_at"] = recovered_at
        event["recovery_reason"] = recovery_reason or "Condition returned to stable state."
        recovered_ids.append(event.get("id"))

    return events, recovered_ids


def apply_event_lifecycle():
    """
    Phase 2B.1:
    Apply lightweight event lifecycle rules.

    Current rule:
    - If threat is GREEN/STABLE, mark active threat events as recovered.
    - Do not create new events here.
    - Creation remains handled by transition logic.
    """
    event_log_payload = load_state(
        "event_log.json",
        default_event_log()
    )

    threat_state_payload = load_state(
        "threat_state.json",
        default_threat_state()
    )

    events = event_log_payload.get("events", [])
    threat_current = threat_state_payload.get("current", {})

    current_level = threat_current.get("level", "UNKNOWN")
    current_state = threat_current.get("state", "UNKNOWN")

    recovered_ids = []

    if current_level == "GREEN" or current_state == "STABLE":
        events, recovered_ids = mark_events_recovered(
            events,
            source="threat",
            recovery_reason="Threat level returned to GREEN/STABLE."
        )

    event_log_payload["events"] = events
    event_log_payload["generated_at"] = now_iso()
    event_log_payload["last_lifecycle_run_at"] = now_iso()

    write_state("event_log.json", event_log_payload)

    return {
        "recovered_event_ids": recovered_ids,
        "active_threat_events": len(find_active_events_by_source(events, "threat"))
    }


def create_data_quality_lifecycle_event(
    source_name: str,
    available: bool,
    errors: list,
    timestamp: str,
    active: bool = True,
    recovered: bool = False,
) -> Dict[str, Any]:
    """
    Phase 4E:
    Create data-quality availability lifecycle events.
    One dedupe key per telemetry source.
    """
    dedupe_key = f"data_quality:{source_name}:availability"

    data_quality_labels = {
        "telemetry_available": "Core telemetry",
        "weather_available": "Weather",
        "finance_available": "Finance",
        "latency_available": "Latency",
        "service_status_available": "Service status",
    }

    pretty_name = data_quality_labels.get(
        source_name,
        source_name.replace("_available", "").replace("_", " ").title()
    )

    if recovered:
        event_type = "RECOVERY"
        priority = "INFO"
        title = "Telemetry source recovered"
        message = f"{pretty_name} telemetry recovered."
    else:
        event_type = "DATA_QUALITY_UNAVAILABLE"
        priority = "WATCH"
        title = "Telemetry source unavailable"
        message = f"{pretty_name} telemetry unavailable."

    related_errors = [
        err for err in (errors or [])
        if source_name.replace("_available", "") in str(err).lower()
        or source_name.replace("_", " ") in str(err).lower()
    ]

    if related_errors and not recovered:
        message = f"{message} Cause: {'; '.join(map(str, related_errors[:2]))}"

    event = {
        "event_id": build_event_id("data_quality", source_name, event_type, timestamp),
        "timestamp": timestamp,
        "priority": priority,
        "category": "data_quality",
        "source": source_name,
        "type": event_type,
        "title": title,
        "message": message,
        "state_before": None,
        "state_after": "AVAILABLE" if recovered else "UNAVAILABLE",
        "score_before": None,
        "score_after": None,
        "dedupe_key": dedupe_key,
        "dedupe_ttl_minutes": 120,
        "active": active,
        "acknowledged": False,
        "recovered": recovered,
        "recovered_at": timestamp if recovered else None,
        "metadata": {
            "source_name": source_name,
            "available": available,
            "errors": errors or [],
            "related_errors": related_errors,
        },
    }

    return append_event(event)


def sync_data_quality_events_from_state() -> None:
    """
    Phase 4E:
    Convert current_state.json data_quality availability changes into low-noise events.

    This only emits when a telemetry source is unavailable, and emits recovery when
    an active data-quality event becomes available again.
    """
    current = load_state("current_state.json", default_current_state())
    data_quality = current.get("data_quality", {})

    if not isinstance(data_quality, dict):
        return

    timestamp = now_iso()
    errors = data_quality.get("errors", [])

    watched_sources = (
        "telemetry_available",
        "weather_available",
        "finance_available",
        "latency_available",
        "service_status_available",
    )

    for source_name in watched_sources:
        available = data_quality.get(source_name)

        if available is None:
            continue

        dedupe_key = f"data_quality:{source_name}:availability"

        if available is True:
            if active_event_exists(dedupe_key):
                mark_active_events_recovered(dedupe_key, timestamp)
                create_data_quality_lifecycle_event(
                    source_name,
                    True,
                    errors,
                    timestamp,
                    active=False,
                    recovered=True,
                )
            continue

        create_data_quality_lifecycle_event(
            source_name,
            False,
            errors,
            timestamp,
            active=True,
            recovered=False,
        )


def auto_recover_stale_threat_escalations() -> int:
    """
    Phase 9C transition lifecycle hardening.

    Compatibility note:
    The existing function name is retained, but this now handles both
    ESCALATION and DOWNGRADE threat transition notices.

    /api/ops/brief is authoritative for current Alpha posture.
    Tactical Feed transition records are historical notices, not permanent
    state holders. Both escalation and downgrade notices therefore expire
    after their configured TTL.
    """
    from datetime import datetime, timezone

    event_log = load_state(
        "event_log.json",
        default_event_log()
    )

    changed = 0
    timestamp = now_iso()

    try:
        now_dt = datetime.fromisoformat(timestamp)
    except Exception:
        now_dt = datetime.now(timezone.utc)

    transition_types = {
        "ESCALATION",
        "DOWNGRADE",
    }

    transition_prefixes = (
        "threat:global:escalation:",
        "threat:global:downgrade:",
    )

    for event in event_log.get("events", []):
        if not isinstance(event, dict):
            continue

        dedupe_key = str(
            event.get("dedupe_key") or ""
        )

        event_type = str(
            event.get("type")
            or event.get("details", {}).get("type")
            or ""
        ).upper()

        category = str(
            event.get("category")
            or event.get("details", {}).get("category")
            or ""
        ).lower()

        is_threat_transition = (
            dedupe_key.startswith(
                transition_prefixes
            )
            or (
                category == "threat"
                and event_type in transition_types
            )
        )

        if not is_threat_transition:
            continue

        if (
            event.get("active") is not True
            or event.get("recovered") is True
        ):
            continue

        try:
            ttl_minutes = int(
                event.get("dedupe_ttl_minutes")
                or event.get("ttl_minutes")
                or 30
            )
        except Exception:
            ttl_minutes = 30

        if ttl_minutes < 5:
            ttl_minutes = 5

        if ttl_minutes > 1440:
            ttl_minutes = 1440

        event_time_raw = event.get("timestamp")

        try:
            event_dt = datetime.fromisoformat(
                str(event_time_raw)
            )

            if event_dt.tzinfo is None:
                event_dt = event_dt.replace(
                    tzinfo=now_dt.tzinfo
                    or timezone.utc
                )
        except Exception:
            continue

        compare_now = now_dt

        if (
            compare_now.tzinfo is None
            and event_dt.tzinfo is not None
        ):
            compare_now = compare_now.replace(
                tzinfo=event_dt.tzinfo
            )
        elif (
            compare_now.tzinfo is not None
            and event_dt.tzinfo is not None
        ):
            compare_now = compare_now.astimezone(
                event_dt.tzinfo
            )

        age_minutes = (
            compare_now - event_dt
        ).total_seconds() / 60

        if age_minutes <= ttl_minutes:
            continue

        resolved_type = (
            event_type
            if event_type in transition_types
            else "TRANSITION"
        )

        event["active"] = False
        event["recovered"] = True
        event["acknowledged"] = True
        event["recovered_at"] = timestamp
        event["status"] = "recovered"

        event["resolution"] = (
            f"Auto-recovered stale threat "
            f"{resolved_type.lower()} transition after "
            f"{age_minutes:.1f}m; TTL was "
            f"{ttl_minutes}m. Current Alpha posture "
            "remains authoritative via /api/ops/brief."
        )

        metadata = event.setdefault(
            "metadata",
            {}
        )

        metadata["auto_recovered_reason"] = (
            "threat transition TTL expired"
        )
        metadata["auto_recovered_event_type"] = (
            resolved_type
        )
        metadata["auto_recovered_age_minutes"] = (
            round(age_minutes, 1)
        )
        metadata["auto_recovered_ttl_minutes"] = (
            ttl_minutes
        )

        changed += 1

    if changed:
        event_log["generated_at"] = timestamp
        event_log["last_lifecycle_run_at"] = (
            timestamp
        )

        write_state(
            "event_log.json",
            event_log
        )

        # Keep the legacy current_state cache aligned with
        # authoritative event_log lifecycle truth.
        sync_current_state_event_cache()

    return changed

def build_quality_summary(data_quality: Dict[str, Any]) -> str:
    """
    Create a calm operator-facing summary from data quality flags.
    """

    checks = {
        "telemetry": data_quality.get("telemetry_available") is True,
        "weather": data_quality.get("weather_available") is True,
        "finance": data_quality.get("finance_available") is True,
        "latency": data_quality.get("latency_available") is True,
        "service status": data_quality.get("service_status_available") is True,
    }

    online = [name for name, ok in checks.items() if ok]
    offline = [name for name, ok in checks.items() if not ok]

    if len(offline) == 0:
        return "All operational data sources reporting."

    if len(online) == 0:
        return "Operational state initialized. No live data sources confirmed yet."

    return (
        f"{len(online)} operational data source(s) reporting; "
        f"{len(offline)} unavailable: {', '.join(offline)}."
    )

def parse_iso_timestamp(value: Optional[str]) -> Optional[datetime]:
    """
    Safely parse ISO timestamps written by now_iso().
    Returns None if timestamp is missing or invalid.
    """

    if not value:
        return None

    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def purge_old_snapshots(entries: list[Dict[str, Any]], max_age_hours: int = 48) -> list[Dict[str, Any]]:
    """
    Hard purge helper:
    Remove snapshot entries older than max_age_hours.

    Default: 48 hours.

    This keeps Alpha lightweight and prevents JSON history files from growing forever.
    """

    cutoff = datetime.now().astimezone() - timedelta(hours=max_age_hours)
    retained = []

    for entry in entries:
        ts = parse_iso_timestamp(entry.get("timestamp"))

        if ts is None:
            # Drop malformed entries. The archive has no patience for nonsense.
            continue

        if ts >= cutoff:
            retained.append(entry)

    return retained


def compact_snapshot_from_summary(summary: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build a compact historical snapshot from the live ops summary.

    This is intentionally smaller than snapshot_latest.json.
    It is designed for trend/recovery engines, not UI rendering.
    """

    return {
        "timestamp": now_iso(),
        "overall_state": summary.get("overall_state", "UNKNOWN"),
        "threat_level": summary.get("threat_level", "UNKNOWN"),
        "score": summary.get("score", 0),
        "summary": summary.get("summary", "No summary available."),
        "active_alerts": summary.get("active_alerts", 0),
        "active_watch_items": summary.get("active_watch_items", 0),
        "data_quality": summary.get("data_quality", {}),
    }

def snapshot_signature(snapshot: Dict[str, Any]) -> str:
    """
    Build a compact signature for posture-history dedupe.
    Only operationally meaningful fields should create a new history point.
    """
    if not isinstance(snapshot, dict):
        return "invalid"

    return "|".join([
        str(snapshot.get("overall_state", "UNKNOWN")),
        str(snapshot.get("threat_level", "UNKNOWN")),
        str(snapshot.get("score", 0)),
        str(snapshot.get("summary", "")),
    ])


def should_append_snapshot(summary: Dict[str, Any], min_interval_seconds=30, max_unchanged_interval_seconds=600):
    """
    Prevent posture history spam from frequent API/UI polling.

    Append when:
    - no history exists
    - posture signature changed
    - max unchanged heartbeat interval elapsed

    Do not append when:
    - state/threat/score/summary are unchanged and the last snapshot is recent
    """
    from datetime import datetime, timezone

    history_payload = safe_load_json(
        SNAPSHOT_DIR / "history_48h.json",
        {"schema_version": "0.1", "entries": []}
    )

    entries = history_payload.get("entries", [])

    if not entries:
        return True

    latest = entries[-1]
    latest_ts = latest.get("timestamp")

    try:
        latest_dt = datetime.fromisoformat(str(latest_ts))
        now_dt = datetime.now(latest_dt.tzinfo or timezone.utc)
        age_seconds = (now_dt - latest_dt).total_seconds()
    except Exception:
        return True

    if age_seconds < min_interval_seconds:
        return False

    current_sig = snapshot_signature(summary)
    latest_sig = snapshot_signature(latest)

    if current_sig != latest_sig:
        return True

    # Quiet heartbeat: keep sparse proof that the system remained stable.
    if age_seconds >= max_unchanged_interval_seconds:
        return True

    return False

def append_snapshot_history(summary: Dict[str, Any]) -> Dict[str, Any]:
    """
    Phase 1 Delta:
    Append compact operational state into rolling history.

    Files:
    - snapshots/history_1h.json
    - snapshots/history_48h.json

    Behavior:
    - history_1h keeps entries from the last 1 hour
    - history_48h keeps entries from the last 48 hours
    - anything older than 48 hours is purged
    """

    snapshot = compact_snapshot_from_summary(summary)

    history_1h_path = SNAPSHOT_DIR / "history_1h.json"
    history_48h_path = SNAPSHOT_DIR / "history_48h.json"

    history_1h_data = safe_load_json(history_1h_path, {
        "schema_version": SCHEMA_VERSION,
        "retention_hours": 1,
        "entries": [],
    })

    history_48h_data = safe_load_json(history_48h_path, {
        "schema_version": SCHEMA_VERSION,
        "retention_hours": 48,
        "entries": [],
    })

    history_1h_entries = history_1h_data.get("entries", [])
    history_48h_entries = history_48h_data.get("entries", [])

    history_1h_entries.append(snapshot)
    history_48h_entries.append(snapshot)

    history_1h_entries = purge_old_snapshots(history_1h_entries, max_age_hours=1)
    history_48h_entries = purge_old_snapshots(history_48h_entries, max_age_hours=48)

    history_1h_data = {
        "schema_version": SCHEMA_VERSION,
        "retention_hours": 1,
        "updated_at": now_iso(),
        "count": len(history_1h_entries),
        "entries": history_1h_entries,
    }

    history_48h_data = {
        "schema_version": SCHEMA_VERSION,
        "retention_hours": 48,
        "updated_at": now_iso(),
        "count": len(history_48h_entries),
        "entries": history_48h_entries,
    }

    safe_write_json(history_1h_path, history_1h_data)
    safe_write_json(history_48h_path, history_48h_data)

    return {
        "history_1h_count": len(history_1h_entries),
        "history_48h_count": len(history_48h_entries),
    }

def write_snapshot_latest(summary: Dict[str, Any]) -> Dict[str, Any]:
    """
    Phase 1 Charlie:
    Persist the latest operational summary snapshot.

    This does not create trend history yet.
    It only writes the most recent clean operational state for future engines.
    """

    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "timestamp": now_iso(),
        "summary": {
            "overall_state": summary.get("overall_state", "UNKNOWN"),
            "threat_level": summary.get("threat_level", "UNKNOWN"),
            "score": summary.get("score", 0),
            "active_alerts": summary.get("active_alerts", 0),
            "active_watch_items": summary.get("active_watch_items", 0),
            "recent_recoveries": len(summary.get("recent_recoveries", [])),
            "operator_summary": summary.get("summary", "No summary available."),
        },
        "data_quality": summary.get("data_quality", {}),
        "active_events": dedupe_events_by_id(summary.get("active_events", [])),
        "recent_recoveries": dedupe_events_by_id(summary.get("recent_recoveries", [])),
    }

    safe_write_json(SNAPSHOT_DIR / "snapshot_latest.json", snapshot)
    return snapshot

def build_history_summary() -> Dict[str, Any]:
    """
    Phase 1 Echo:
    Return lightweight operational memory statistics.

    This confirms snapshot history is being written and retained properly.
    """

    history_1h = safe_load_json(SNAPSHOT_DIR / "history_1h.json", {
        "schema_version": SCHEMA_VERSION,
        "retention_hours": 1,
        "entries": [],
    })

    history_48h = safe_load_json(SNAPSHOT_DIR / "history_48h.json", {
        "schema_version": SCHEMA_VERSION,
        "retention_hours": 48,
        "entries": [],
    })

    entries_1h = history_1h.get("entries", [])
    entries_48h = history_48h.get("entries", [])

    latest = entries_48h[-1] if entries_48h else None
    oldest_48h = entries_48h[0] if entries_48h else None
    oldest_1h = entries_1h[0] if entries_1h else None

    return {
        "schema_version": SCHEMA_VERSION,
        "system_id": SYSTEM_ID,
        "generated_at": now_iso(),
        "history": {
            "history_1h_count": len(entries_1h),
            "history_48h_count": len(entries_48h),
            "oldest_1h": oldest_1h.get("timestamp") if oldest_1h else None,
            "oldest_48h": oldest_48h.get("timestamp") if oldest_48h else None,
            "newest": latest.get("timestamp") if latest else None,
        },
        "current": {
            "overall_state": latest.get("overall_state") if latest else "UNKNOWN",
            "threat_level": latest.get("threat_level") if latest else "UNKNOWN",
            "score": latest.get("score") if latest else 0,
            "summary": latest.get("summary") if latest else "No history available yet.",
        },
        "retention": {
            "short_window_hours": 1,
            "long_window_hours": 48,
            "hard_purge_hours": 48,
        },
    }

def build_ops_summary() -> Dict[str, Any]:
    current = load_state("current_state.json", default_current_state())
    threat = load_state("threat_state.json", default_threat_state())
    events = load_state("event_log.json", default_event_log())

    active_events = [
        event for event in events.get("events", [])
        if event.get("active") is True and event.get("recovered") is not True
    ]

    watch_items = [
        event for event in active_events
        if event.get("priority") == "WATCH"
    ]

    alerts = [
        event for event in active_events
        if event.get("priority") in ("ALERT", "CRITICAL")
    ]

    summary = {
        "schema_version": SCHEMA_VERSION,
        "system_id": SYSTEM_ID,
        "generated_at": now_iso(),
        "overall_state": current.get("health", {}).get("overall_state", "UNKNOWN"),
        "threat_level": threat.get("current", {}).get("level", "UNKNOWN"),
        "score": threat.get("current", {}).get("score", 0),
        "summary": current.get("health", {}).get("summary", "No summary available."),
        "active_alerts": len(alerts),
        "active_watch_items": len(watch_items),
        "active_events": active_events[-10:],
        "recent_recoveries": dedupe_events_by_id([
            event for event in events.get("events", [])
            if event.get("recovered") is True or event.get("status") in ("recovered", "resolved")
        ])[-5:],
        "data_quality": current.get("data_quality", {}),
        "weather": current.get("weather", {}),
    }

    write_snapshot_latest(summary)

    if should_append_snapshot(summary, min_interval_seconds=30, max_unchanged_interval_seconds=600):
        append_snapshot_history(summary)
    return summary

def minutes_between(start_iso, end_iso=None):
    """
    Return whole-minute distance between two ISO timestamps.
    Safe fallback: 0.
    """
    try:
        start = parse_iso_timestamp(start_iso)
        end = parse_iso_timestamp(end_iso) if end_iso else parse_iso_timestamp(now_iso())
        if not start or not end:
            return 0
        return max(0, int((end - start).total_seconds() // 60))
    except Exception:
        return 0


def classify_trend_direction(score_delta, state_changes):
    """
    Determine whether Alpha posture is stable, worsening, recovering, or volatile.
    """
    if state_changes >= 4:
        return "VOLATILE"

    if score_delta > 0:
        return "WORSENING"

    if score_delta < 0:
        return "RECOVERING"

    return "STABLE"


def classify_volatility(state_changes):
    """
    Low-noise volatility classification.
    """
    if state_changes >= 5:
        return "HIGH"
    if state_changes >= 3:
        return "MEDIUM"
    return "LOW"


def count_state_changes(entries):
    """
    Count overall_state transitions in a compact snapshot history.
    """
    if not entries:
        return 0

    changes = 0
    previous_state = None

    for entry in entries:
        state = entry.get("overall_state")
        if previous_state is not None and state != previous_state:
            changes += 1
        previous_state = state

    return changes


def find_current_state_start(entries, current_state):
    """
    Walk backward through history to estimate how long the current state has persisted.
    """
    if not entries or not current_state:
        return None

    matching_start = None

    for entry in reversed(entries):
        if entry.get("overall_state") == current_state:
            matching_start = entry.get("timestamp")
        else:
            break

    return matching_start


def build_operator_trend_summary(current_state, threat_level, score_delta_1h, state_duration_minutes, trend_direction, volatility):
    """
    Human-readable ONI-style summary for Wall-UI.
    """
    if trend_direction == "WORSENING":
        return f"Operational posture is worsening. Threat level is {threat_level}, state is {current_state}, score increased by {score_delta_1h} over the last hour."

    if trend_direction == "RECOVERING":
        return f"Operational posture is recovering. Threat level is {threat_level}, state is {current_state}, score decreased by {abs(score_delta_1h)} over the last hour."

    if trend_direction == "VOLATILE":
        return f"Operational posture is volatile. State changed repeatedly within the last hour. Current state is {current_state}."

    if current_state and threat_level:
        return f"Operational posture remains {current_state}. Threat level has held at {threat_level} for approximately {state_duration_minutes} minutes."

    return "Operational trend data is available, but current posture could not be fully classified."


def build_trend_summary():
    """
    Phase 2A:
    Analyze snapshot history and return lightweight operational trend intelligence.
    No database. No event generation. No UI coupling.
    """
    current_state_payload = load_state(
        "current_state.json",
        default_current_state()
    )

    threat_state_payload = load_state(
        "threat_state.json",
        default_threat_state()
    )

    history_1h_payload = safe_load_json(
        get_state_path("snapshots/history_1h.json"),
        {"schema_version": "0.1", "entries": []}
    )

    history_48h_payload = safe_load_json(
        get_state_path("snapshots/history_48h.json"),
        {"schema_version": "0.1", "entries": []}
    )

    history_1h = history_1h_payload.get("entries", [])
    history_48h = history_48h_payload.get("entries", [])

    current_health = current_state_payload.get("health", {})
    threat_current = threat_state_payload.get("current", {})

    current_state = (
        current_health.get("overall_state")
        or threat_current.get("state")
        or "UNKNOWN"
    )

    threat_level = (
        current_health.get("threat_level")
        or threat_current.get("level")
        or "UNKNOWN"
    )

    current_score = int(
        current_health.get("score")
        or threat_current.get("score")
        or 0
    )

    first_1h_score = current_score
    if history_1h:
        first_1h_score = int(history_1h[0].get("score", current_score) or 0)

    first_48h_score = current_score
    if history_48h:
        first_48h_score = int(history_48h[0].get("score", current_score) or 0)

    score_delta_1h = current_score - first_1h_score
    score_delta_48h = current_score - first_48h_score

    state_changes_1h = count_state_changes(history_1h)
    state_changes_48h = count_state_changes(history_48h)

    state_start = find_current_state_start(history_48h, current_state)
    state_duration_minutes = minutes_between(state_start) if state_start else 0

    volatility = classify_volatility(state_changes_1h)
    trend_direction = classify_trend_direction(score_delta_1h, state_changes_1h)

    summary = build_operator_trend_summary(
        current_state=current_state,
        threat_level=threat_level,
        score_delta_1h=score_delta_1h,
        state_duration_minutes=state_duration_minutes,
        trend_direction=trend_direction,
        volatility=volatility
    )

    return {
        "schema_version": "0.1",
        "system_id": current_state_payload.get("system_id", "rackmain1-wall-ui"),
        "generated_at": now_iso(),
        "current_state": current_state,
        "current_threat_level": threat_level,
        "current_score": current_score,
        "state_duration_minutes": state_duration_minutes,
        "score_delta_1h": score_delta_1h,
        "score_delta_48h": score_delta_48h,
        "state_changes_1h": state_changes_1h,
        "state_changes_48h": state_changes_48h,
        "volatility": volatility,
        "trend_direction": trend_direction,
        "summary": summary
    }

def main() -> None:
    initialize_state_files(overwrite=False)
    summary = build_ops_summary()

    print(json.dumps({
        "status": "ok",
        "message": "Priority Alpha state files initialized.",
        "state_dir": str(STATE_DIR),
        "summary": summary,
    }, indent=2))


if __name__ == "__main__":
    main()
