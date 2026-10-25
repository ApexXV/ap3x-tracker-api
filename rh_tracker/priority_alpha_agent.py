#!/usr/bin/env python3
"""
Priority Alpha Autonomous Operator Agent — Phase 11A Observe-Only

Purpose:
  Passive display support agent.
  No touchscreen, no mouse, no manual wall controls assumed.

Phase 11A behavior:
  - Observe local services/endpoints.
  - Write operator_agent_state.json.
  - Do not restart services.
  - Do not perform destructive actions.
  - Do not send Discord yet.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any

RH_ROOT = Path("/home/ap3x/scripts/rh_tracker")
STATE_DIR = RH_ROOT / "state"
STATE_PATH = STATE_DIR / "operator_agent_state.json"
LOG_PATH = STATE_DIR / "operator_agent_log.json"
ACTION_INCIDENT_PATH = STATE_DIR / "operator_agent_incidents.json"
CONFIG_DIR = RH_ROOT / "config"
DISCORD_ENV_PATH = CONFIG_DIR / "discord_webhook.env"
ACTIONS_ENV_PATH = CONFIG_DIR / "agent_actions.env"

TZ = ZoneInfo("America/New_York")

CHECKS = {
    "backend_ops_brief": "http://127.0.0.1:8000/api/ops/brief",
    "backend_weather": "http://127.0.0.1:8000/api/weather",
    "backend_status": "http://127.0.0.1:8000/status",
    "backend_external": "http://127.0.0.1:8000/api/external",
    "wall_ops_brief": "http://127.0.0.1:3098/api/ops/brief",
    "wall_ops_feed": "http://127.0.0.1:3098/api/ops/feed",
    "wall_weather": "http://127.0.0.1:3098/api/weather",
    "wall_status": "http://127.0.0.1:3098/api/status",
    "wall_external": "http://127.0.0.1:3098/api/external",
}

SYSTEMD_UNITS = [
    "rh-tracker-api.service",
    "rh-tracker.service",
    "robinhood-sync.timer",
]

DOCKER_CONTAINERS = [
    "wall-ui",
]


def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def run_cmd(cmd: list[str], timeout: int = 10) -> dict[str, Any]:
    try:
        p = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return {
            "ok": p.returncode == 0,
            "returncode": p.returncode,
            "stdout": p.stdout.strip(),
            "stderr": p.stderr.strip(),
        }
    except Exception as e:
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": str(e),
        }


def fetch_json(name: str, url: str, timeout: int = 8) -> dict[str, Any]:
    started = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PriorityAlphaAgent/11A"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            latency_ms = round((time.time() - started) * 1000, 2)
            data = json.loads(raw)
            return {
                "name": name,
                "url": url,
                "ok": True,
                "status_code": getattr(resp, "status", 200),
                "latency_ms": latency_ms,
                "error": None,
                "data": data,
            }
    except Exception as e:
        latency_ms = round((time.time() - started) * 1000, 2)
        return {
            "name": name,
            "url": url,
            "ok": False,
            "status_code": None,
            "latency_ms": latency_ms,
            "error": str(e),
            "data": None,
        }


def check_systemd_unit(unit: str) -> dict[str, Any]:
    active = run_cmd(["systemctl", "is-active", unit])
    enabled = run_cmd(["systemctl", "is-enabled", unit])
    return {
        "unit": unit,
        "active": active["stdout"],
        "enabled": enabled["stdout"],
        "ok": active["stdout"] in ("active", "activating"),
        "error": active["stderr"] or None,
    }


def check_docker_container(name: str) -> dict[str, Any]:
    result = run_cmd([
        "docker",
        "inspect",
        "-f",
        "{{.State.Status}}",
        name,
    ])
    status = result["stdout"].strip()
    return {
        "container": name,
        "status": status or "unknown",
        "ok": status == "running",
        "error": result["stderr"] or None,
    }


def check_port(host: str, port: int, timeout: float = 2.0) -> dict[str, Any]:
    started = time.time()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {
                "host": host,
                "port": port,
                "ok": True,
                "latency_ms": round((time.time() - started) * 1000, 2),
                "error": None,
            }
    except Exception as e:
        return {
            "host": host,
            "port": port,
            "ok": False,
            "latency_ms": round((time.time() - started) * 1000, 2),
            "error": str(e),
        }


def summarize_endpoint_data(endpoints: dict[str, Any]) -> dict[str, Any]:
    # Prefer wall-proxy data for what the display sees, but fall back to backend-direct
    # so Discord does not claim Alpha is UNKNOWN just because wall-ui is down.
    ops = (
        endpoints.get("wall_ops_brief", {}).get("data")
        or endpoints.get("backend_ops_brief", {}).get("data")
        or {}
    )
    feed = endpoints.get("wall_ops_feed", {}).get("data") or {}
    weather = (
        endpoints.get("wall_weather", {}).get("data")
        or endpoints.get("backend_weather", {}).get("data")
        or {}
    )
    status = (
        endpoints.get("wall_status", {}).get("data")
        or endpoints.get("backend_status", {}).get("data")
        or {}
    )
    external = (
        endpoints.get("wall_external", {}).get("data")
        or endpoints.get("backend_external", {}).get("data")
        or {}
    )

    state = ops.get("state", {}) if isinstance(ops, dict) else {}
    events = ops.get("events", {}) if isinstance(ops, dict) else {}
    data_quality = ops.get("data_quality", {}) if isinstance(ops, dict) else {}

    fallback_sources = []
    if not endpoints.get("wall_ops_brief", {}).get("ok") and endpoints.get("backend_ops_brief", {}).get("ok"):
        fallback_sources.append("backend_ops_brief")
    if not endpoints.get("wall_weather", {}).get("ok") and endpoints.get("backend_weather", {}).get("ok"):
        fallback_sources.append("backend_weather")
    if not endpoints.get("wall_status", {}).get("ok") and endpoints.get("backend_status", {}).get("ok"):
        fallback_sources.append("backend_status")
    if not endpoints.get("wall_external", {}).get("ok") and endpoints.get("backend_external", {}).get("ok"):
        fallback_sources.append("backend_external")

    return {
        "alpha_state": state.get("overall_state"),
        "threat_level": state.get("threat_level"),
        "score": state.get("score"),
        "active_events": events.get("active"),
        "data_quality_errors": data_quality.get("errors", []),
        "ops_feed_counts": feed.get("counts", {}) if isinstance(feed, dict) else {},
        "weather": {
            "posture": weather.get("posture"),
            "source_label": weather.get("source_label"),
            "cache_source": weather.get("cache_source"),
            "stale": weather.get("stale"),
            "cache_age_seconds": weather.get("cache_age_seconds"),
        },
        "services": status.get("services", {}) if isinstance(status, dict) else {},
        "system": status.get("system", {}) if isinstance(status, dict) else {},
        "external": external if isinstance(external, dict) else {},
        "fallback_sources": fallback_sources,
    }


def build_dry_run_actions(
    endpoints: dict[str, Any],
    systemd: dict[str, Any],
    docker: dict[str, Any],
    ports: dict[str, Any],
    summary: dict[str, Any],
    recommendations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Phase 11D dry-run action planner.

    This DOES NOT execute restarts.
    It only records what the autonomous operator would do if enabled.
    """
    actions: list[dict[str, Any]] = []

    api_port_ok = ports.get("api_8000", {}).get("ok")
    wall_port_ok = ports.get("wall_3098", {}).get("ok")
    api_unit_ok = systemd.get("rh-tracker-api.service", {}).get("ok")
    wall_container_ok = docker.get("wall-ui", {}).get("ok")

    failed_endpoints = [name for name, data in endpoints.items() if not data.get("ok")]

    if not wall_container_ok or not wall_port_ok:
        actions.append({
            "mode": "dry_run",
            "action": "restart_container",
            "target": "wall-ui",
            "reason": "Wall UI container or port 3098 is unavailable.",
            "would_execute": "docker restart wall-ui",
            "allowed_when_live": True,
            "risk": "low",
        })

    if not api_unit_ok or not api_port_ok:
        actions.append({
            "mode": "dry_run",
            "action": "restart_service",
            "target": "rh-tracker-api.service",
            "reason": "rh-tracker-api service or port 8000 is unavailable.",
            "would_execute": "sudo systemctl restart rh-tracker-api.service",
            "allowed_when_live": True,
            "risk": "low",
        })

    api_related_failures = [
        name for name in failed_endpoints
        if name.startswith("backend_") or name.startswith("wall_ops") or name in ("wall_weather", "wall_status", "wall_external")
    ]

    if api_related_failures and api_port_ok:
        actions.append({
            "mode": "dry_run",
            "action": "validate_then_consider_api_restart",
            "target": "rh-tracker-api.service",
            "reason": "API port is reachable, but one or more API endpoints failed: " + ", ".join(api_related_failures),
            "would_execute": "retry validation first; restart API only after repeated failures",
            "allowed_when_live": False,
            "risk": "medium",
        })

    weather = summary.get("weather", {})
    if weather.get("stale"):
        actions.append({
            "mode": "dry_run",
            "action": "weather_cache_watch",
            "target": "weather",
            "reason": "Weather cache is stale.",
            "would_execute": "monitor cache age; refresh/restart only after repeated stale checks",
            "allowed_when_live": False,
            "risk": "medium",
        })

    system = summary.get("system", {})
    root_disk = system.get("root_disk")
    media_disk = system.get("media_disk")
    cpu = system.get("cpu")
    memory = system.get("memory")

    if isinstance(root_disk, (int, float)) and root_disk >= 90:
        actions.append({
            "mode": "dry_run",
            "action": "notify_only",
            "target": "root_disk",
            "reason": f"Root disk high at {root_disk}%.",
            "would_execute": "Discord notify and display recommendation only",
            "allowed_when_live": True,
            "risk": "notify_only",
        })

    if isinstance(media_disk, (int, float)) and media_disk >= 92:
        actions.append({
            "mode": "dry_run",
            "action": "notify_only",
            "target": "media_disk",
            "reason": f"Media disk high at {media_disk}%.",
            "would_execute": "Discord notify and display recommendation only",
            "allowed_when_live": True,
            "risk": "notify_only",
        })

    if isinstance(cpu, (int, float)) and cpu >= 95:
        actions.append({
            "mode": "dry_run",
            "action": "notify_only",
            "target": "cpu",
            "reason": f"CPU critical at {cpu}%.",
            "would_execute": "Discord notify and display recommendation only",
            "allowed_when_live": True,
            "risk": "notify_only",
        })

    if isinstance(memory, (int, float)) and memory >= 92:
        actions.append({
            "mode": "dry_run",
            "action": "notify_only",
            "target": "memory",
            "reason": f"Memory critical at {memory}%.",
            "would_execute": "Discord notify and display recommendation only",
            "allowed_when_live": True,
            "risk": "notify_only",
        })

    return actions



def _score_bool_penalty(ok: bool, penalty: int) -> int:
    return 0 if ok else penalty



def _phase3b_ops_brief(endpoints: dict[str, Any]) -> dict[str, Any]:
    """Return the best available ops brief payload from wall or backend endpoint checks."""
    if not isinstance(endpoints, dict):
        return {}

    for key in ("wall_ops_brief", "backend_ops_brief"):
        payload = (endpoints.get(key) or {}).get("data")
        if isinstance(payload, dict):
            return payload
    return {}


def _phase3b_add_recommendation(
    recommendations: list[dict[str, Any]],
    *,
    severity: str,
    target: str,
    recommendation: str,
    category: str = "operator",
    evidence: dict[str, Any] | None = None,
) -> None:
    """Append a normalized Phase 3B recommendation if it is not already present."""
    evidence = evidence or {}
    fingerprint = (severity, category, target, recommendation)

    for existing in recommendations:
        existing_fp = (
            existing.get("severity"),
            existing.get("category", "operator"),
            existing.get("target"),
            existing.get("recommendation"),
        )
        if existing_fp == fingerprint:
            return

    recommendations.append({
        "phase": "3B",
        "severity": severity,
        "category": category,
        "target": target,
        "recommendation": recommendation,
        "evidence": evidence,
        "auto_action_allowed_now": False,
        "dry_run_only": True,
    })



def phase3c_default_simulation_metadata() -> dict[str, Any]:
    return {
        "schema_version": "3C.fault_simulation.1",
        "enabled": False,
        "scenario": "none",
        "applied": False,
        "note": "Simulation disabled; no in-memory mutations applied.",
    }


def _phase3c_get_simulation_name() -> str:
    """
    Manual-only simulation selector.

    Usage:
      EPSILON_FAULT_SIM=wall_down python3 priority_alpha_agent.py

    No real services are touched. This only mutates in-memory observation payloads.
    """
    return os.getenv("EPSILON_FAULT_SIM", "").strip().lower()


def apply_phase3c_fault_simulation(
    endpoints: dict[str, Any],
    systemd: dict[str, Any],
    docker: dict[str, Any],
    ports: dict[str, Any],
    summary: dict[str, Any],
) -> dict[str, Any]:
    """
    EPSILON PHASE 3C-R2:
    Inject fake faults into local observation payloads for recommendation testing.

    This is environment-variable gated and manual-only.
    It never stops, restarts, modifies, deletes, or touches live services.
    """
    sim = _phase3c_get_simulation_name()
    meta = phase3c_default_simulation_metadata()

    if not sim:
        return meta

    meta.update({
        "enabled": True,
        "scenario": sim,
        "note": "Simulation mutates in-memory agent observations only; no real services are touched.",
    })

    if sim in ("wall_down", "wall-ui-down", "wall"):
        if "wall_ops_brief" in endpoints:
            endpoints["wall_ops_brief"]["ok"] = False
            endpoints["wall_ops_brief"]["status_code"] = None
            endpoints["wall_ops_brief"]["error"] = "SIMULATED: wall ops brief unavailable"
        if "wall_3098" in ports:
            ports["wall_3098"]["ok"] = False
            ports["wall_3098"]["error"] = "SIMULATED: wall port unavailable"
        if "wall-ui" in docker:
            docker["wall-ui"]["ok"] = False
            docker["wall-ui"]["status"] = "simulated_unavailable"
            docker["wall-ui"]["error"] = "SIMULATED: wall-ui container unhealthy"
        meta["applied"] = True
        return meta

    if sim in ("api_down", "rh_api_down", "backend_down", "api"):
        for key in ("backend_ops_brief", "backend_weather", "backend_status", "backend_external"):
            if key in endpoints:
                endpoints[key]["ok"] = False
                endpoints[key]["status_code"] = None
                endpoints[key]["error"] = "SIMULATED: backend endpoint unavailable"
        if "api_8000" in ports:
            ports["api_8000"]["ok"] = False
            ports["api_8000"]["error"] = "SIMULATED: API port unavailable"
        if "rh-tracker-api.service" in systemd:
            systemd["rh-tracker-api.service"]["ok"] = False
            systemd["rh-tracker-api.service"]["active"] = "simulated_failed"
            systemd["rh-tracker-api.service"]["error"] = "SIMULATED: rh-tracker-api unhealthy"
        meta["applied"] = True
        return meta

    if sim in ("weather_stale", "stale_weather", "weather"):
        weather = summary.setdefault("weather", {})
        if isinstance(weather, dict):
            weather["stale"] = True
            weather["posture"] = "CACHED"
            weather["cache_age_seconds"] = 9999
            weather["provider"] = weather.get("provider") or "simulated"
            weather["source_label"] = "SIMULATED stale weather cache"
        meta["applied"] = True
        return meta

    if sim in ("camera_stale", "stale_camera", "camera"):
        for key in ("wall_ops_brief", "backend_ops_brief"):
            data = (endpoints.get(key) or {}).get("data")
            if isinstance(data, dict):
                cam = data.setdefault("camera_alpha", {})
                if isinstance(cam, dict):
                    cam["posture"] = "DEGRADED"
                    cam["issues"] = ["SIMULATED stale frame"]
                    snap = cam.setdefault("snapshot", {})
                    if isinstance(snap, dict):
                        snap["ok"] = True
                        snap["frame_age_seconds"] = 9999
                        snap["last_good_frame_at"] = "SIMULATED"
        meta["applied"] = True
        return meta

    if sim in ("external_slow", "slow_external", "external"):
        external = summary.setdefault("external", {})
        if isinstance(external, dict):
            external["dashboard"] = {
                "state": "online",
                "status_code": 200,
                "latency_ms": 3500,
                "simulated": True,
            }

        # Also mutate ops brief endpoint payloads so Phase 3B sees the simulated
        # value even when ops brief data is preferred over compact summary data.
        for key in ("wall_ops_brief", "backend_ops_brief"):
            data = (endpoints.get(key) or {}).get("data")
            if isinstance(data, dict):
                ext = data.setdefault("external", {})
                if isinstance(ext, dict):
                    ext["dashboard"] = {
                        "state": "online",
                        "status_code": 200,
                        "latency_ms": 3500,
                        "simulated": True,
                    }

        meta["applied"] = True
        return meta

    if sim in ("external_down", "offline_external"):
        external = summary.setdefault("external", {})
        if isinstance(external, dict):
            external["dashboard"] = {
                "state": "offline",
                "status_code": 503,
                "latency_ms": None,
                "error": "SIMULATED external outage",
            }
        meta["applied"] = True
        return meta

    if sim in ("root_disk_high", "disk_high", "root_disk"):
        system = summary.setdefault("system", {})
        if isinstance(system, dict):
            system["root_disk"] = 88.8
        meta["applied"] = True
        return meta

    if sim in ("media_disk_high", "media_disk"):
        system = summary.setdefault("system", {})
        if isinstance(system, dict):
            system["media_disk"] = 90.1
        meta["applied"] = True
        return meta

    if sim in ("robinhood_timer_bad", "robinhood_bad", "finance_timer"):
        systemd["robinhood-sync.timer"] = {
            "unit": "robinhood-sync.timer",
            "active": "simulated_failed",
            "enabled": "enabled",
            "ok": False,
            "error": "SIMULATED: Robinhood timer unhealthy",
        }
        meta["applied"] = True
        return meta

    if sim in ("service_down", "media_service_down", "qbit_down"):
        services = summary.setdefault("services", {})
        if isinstance(services, dict):
            services["qbit"] = "offline"
        meta["applied"] = True
        return meta

    meta["note"] = f"Unknown simulation scenario '{sim}'. No mutation applied."
    return meta


def phase3d_normalize_recommendations(recommendations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    EPSILON PHASE 3D:
    Normalize legacy/base recommendations so wall/API/Discord views can group them cleanly.
    """
    normalized: list[dict[str, Any]] = []

    for rec in recommendations or []:
        if not isinstance(rec, dict):
            continue

        item = dict(rec)
        target = str(item.get("target", "") or "")
        recommendation = str(item.get("recommendation", "") or "").lower()

        if not item.get("category"):
            # Specific infrastructure categories first. Do not let words like
            # backend_weather inside an API failure message misclassify the whole
            # recommendation as weather.
            if (
                "api-endpoints" in target
                or "rh-tracker-api" in target
                or "api port" in recommendation
                or "one or more endpoints failed" in recommendation
            ):
                item["category"] = "api"
            elif "wall-ui" in target:
                item["category"] = "wall"
            elif "service:" in target or "qbit" in target or "sonarr" in target or "radarr" in target or "prowlarr" in target:
                item["category"] = "services"
            elif "camera" in target or "camera" in recommendation:
                item["category"] = "camera"
            elif "robinhood" in target or "finance" in recommendation:
                item["category"] = "finance"
            elif "disk" in target or "cpu" in target or "memory" in target:
                item["category"] = "resources"
            elif "external" in target or "external endpoint" in recommendation:
                item["category"] = "external"
            elif "weather" in target or "weather cache" in recommendation or "weather provider" in recommendation:
                item["category"] = "weather"
            else:
                item["category"] = "operator"

        item.setdefault("auto_action_allowed_now", False)
        item.setdefault("dry_run_only", True)
        normalized.append(item)

    return normalized


def phase3d_simulation_result(
    simulation: dict[str, Any],
    recommendations: list[dict[str, Any]],
    dry_run_actions: list[dict[str, Any]],
    brain: dict[str, Any],
) -> dict[str, Any]:
    """
    Compact simulator result for validation output.
    """
    categories: dict[str, int] = {}
    severities: dict[str, int] = {}

    for rec in recommendations or []:
        category = str((rec or {}).get("category") or "unknown")
        severity = str((rec or {}).get("severity") or "unknown")
        categories[category] = categories.get(category, 0) + 1
        severities[severity] = severities.get(severity, 0) + 1

    return {
        "schema_version": "3D.sim_result.1",
        "simulation_enabled": bool((simulation or {}).get("enabled")),
        "scenario": (simulation or {}).get("scenario", "none"),
        "applied": bool((simulation or {}).get("applied")),
        "recommendation_count": len(recommendations or []),
        "dry_run_action_count": len(dry_run_actions or []),
        "brain_posture": (brain or {}).get("posture"),
        "risk_score": (brain or {}).get("risk_score"),
        "categories": categories,
        "severities": severities,
        "live_safe": True,
    }

def phase3b_recommendations(
    endpoints: dict[str, Any],
    systemd: dict[str, Any],
    docker: dict[str, Any],
    ports: dict[str, Any],
    summary: dict[str, Any],
    existing_recommendations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    EPSILON PHASE 3B:
    Expand recommendation intelligence while remaining dry-run only.

    This layer does not execute anything.
    It only emits operator-facing recommendations for API/wall/Discord visibility.
    """
    recommendations = list(existing_recommendations or [])
    ops = _phase3b_ops_brief(endpoints)

    weather = {}
    if isinstance(summary.get("weather"), dict):
        weather = summary.get("weather") or {}
    if isinstance(ops.get("weather"), dict):
        weather = ops.get("weather") or weather

    weather_stale = bool(weather.get("stale"))
    weather_posture = str(weather.get("posture", "") or "").upper()
    weather_cache_age = weather.get("cache_age_seconds")
    weather_provider = weather.get("provider") or weather.get("source_label") or "unknown"

    if weather_stale:
        _phase3b_add_recommendation(
            recommendations,
            severity="WATCH",
            category="weather",
            target="weather_cache",
            recommendation="Weather cache is stale. Verify provider/cache before considering service action.",
            evidence={
                "posture": weather_posture,
                "provider": weather_provider,
                "cache_age_seconds": weather_cache_age,
            },
        )
    elif weather_posture in ("FALLBACK", "CACHED"):
        _phase3b_add_recommendation(
            recommendations,
            severity="INFO",
            category="weather",
            target="weather_provider",
            recommendation="Weather provider is not fully live. Monitor before escalating.",
            evidence={
                "posture": weather_posture,
                "provider": weather_provider,
                "cache_age_seconds": weather_cache_age,
            },
        )

    alerts = weather.get("alerts") if isinstance(weather.get("alerts"), dict) else {}
    if int(alerts.get("active", 0) or 0) > 0:
        _phase3b_add_recommendation(
            recommendations,
            severity="WATCH",
            category="weather",
            target="weather_alerts",
            recommendation="Active weather alert detected. Keep wall feed visible and avoid auto-clearing weather state.",
            evidence={
                "active": alerts.get("active"),
                "highest_severity": alerts.get("highest_severity"),
                "primary": alerts.get("primary"),
            },
        )

    camera = ops.get("camera_alpha") if isinstance(ops.get("camera_alpha"), dict) else {}
    if camera:
        camera_posture = str(camera.get("posture", "UNKNOWN") or "UNKNOWN").upper()
        snapshot = camera.get("snapshot") if isinstance(camera.get("snapshot"), dict) else {}
        proxy = snapshot.get("proxy") if isinstance(snapshot.get("proxy"), dict) else {}
        direct = snapshot.get("direct") if isinstance(snapshot.get("direct"), dict) else {}

        snapshot_ok = bool(snapshot.get("ok"))
        proxy_ok = bool(proxy.get("ok")) if proxy else True
        direct_ok = bool(direct.get("ok")) if direct else True
        frame_age = snapshot.get("frame_age_seconds")
        issues = camera.get("issues") if isinstance(camera.get("issues"), list) else []

        if camera_posture not in ("ONLINE", "OK", "STABLE") or not snapshot_ok or not proxy_ok or not direct_ok or issues:
            _phase3b_add_recommendation(
                recommendations,
                severity="WATCH",
                category="camera",
                target="camera_alpha",
                recommendation="Camera Alpha is degraded or snapshot health is imperfect. Verify go2rtc/camera path before wall integration decisions.",
                evidence={
                    "posture": camera_posture,
                    "snapshot_ok": snapshot_ok,
                    "proxy_ok": proxy_ok,
                    "direct_ok": direct_ok,
                    "issues": issues,
                    "frame_age_seconds": frame_age,
                },
            )

        try:
            if frame_age is not None and float(frame_age) > 300:
                _phase3b_add_recommendation(
                    recommendations,
                    severity="WATCH",
                    category="camera",
                    target="camera_alpha_frame",
                    recommendation="Camera Alpha frame age is stale. Snapshot may be frozen even if endpoint responds.",
                    evidence={
                        "frame_age_seconds": frame_age,
                        "last_good_frame_at": snapshot.get("last_good_frame_at"),
                    },
                )
        except Exception:
            pass

    external = {}
    if isinstance(summary.get("external"), dict):
        external = summary.get("external") or {}
    if isinstance(ops.get("external"), dict):
        external = ops.get("external") or external

    for name, data in external.items():
        if name == "_cache" or not isinstance(data, dict):
            continue

        state = str(data.get("state", "unknown") or "unknown").lower()
        latency = data.get("latency_ms")

        if state not in ("online", "ok", "healthy"):
            _phase3b_add_recommendation(
                recommendations,
                severity="WATCH",
                category="external",
                target=f"external:{name}",
                recommendation=f"External endpoint {name} is not online. Confirm before escalating.",
                evidence=data,
            )
        else:
            try:
                if latency is not None and float(latency) >= 2000:
                    _phase3b_add_recommendation(
                        recommendations,
                        severity="INFO",
                        category="external",
                        target=f"external:{name}",
                        recommendation=f"External endpoint {name} is slow. Track latency but do not restart anything.",
                        evidence=data,
                    )
            except Exception:
                pass

    services = summary.get("services") if isinstance(summary.get("services"), dict) else {}
    for name, state in services.items():
        if str(state).lower() not in ("online", "ok", "healthy"):
            _phase3b_add_recommendation(
                recommendations,
                severity="WATCH",
                category="services",
                target=f"service:{name}",
                recommendation=f"Service {name} is not online in status summary. Validate service-specific health.",
                evidence={"state": state},
            )

    resources = summary.get("system") if isinstance(summary.get("system"), dict) else {}
    try:
        root_disk = float(resources.get("root_disk", 0) or 0)
        if root_disk >= 80:
            _phase3b_add_recommendation(
                recommendations,
                severity="WATCH" if root_disk < 90 else "ALERT",
                category="resources",
                target="root_disk",
                recommendation="Root disk usage is approaching pressure threshold. Manual cleanup review recommended.",
                evidence={"root_disk": root_disk},
            )
    except Exception:
        pass

    try:
        media_disk = float(resources.get("media_disk", 0) or 0)
        if media_disk >= 85:
            _phase3b_add_recommendation(
                recommendations,
                severity="WATCH" if media_disk < 92 else "ALERT",
                category="resources",
                target="media_disk",
                recommendation="Media disk usage is approaching pressure threshold. Review incoming/download directories.",
                evidence={"media_disk": media_disk},
            )
    except Exception:
        pass

    # Timer/systemd awareness. These are observational only.
    robinhood_timer = systemd.get("robinhood-sync.timer") if isinstance(systemd, dict) else None
    if isinstance(robinhood_timer, dict) and not robinhood_timer.get("ok"):
        _phase3b_add_recommendation(
            recommendations,
            severity="WATCH",
            category="finance",
            target="robinhood-sync.timer",
            recommendation="Robinhood sync timer is not healthy. Manual timer/service validation recommended.",
            evidence=robinhood_timer,
        )

    # Endpoint cache health.
    ext_cache = external.get("_cache") if isinstance(external.get("_cache"), dict) else {}
    try:
        if ext_cache and float(ext_cache.get("age_seconds", 0) or 0) > float(ext_cache.get("ttl_seconds", 90) or 90) * 3:
            _phase3b_add_recommendation(
                recommendations,
                severity="INFO",
                category="cache",
                target="external_health_cache",
                recommendation="External health cache age is unusually high. Monitor cache warmer/timer behavior.",
                evidence=ext_cache,
            )
    except Exception:
        pass

    return recommendations

def build_brain_assessment(
    endpoints: dict[str, Any],
    systemd: dict[str, Any],
    docker: dict[str, Any],
    ports: dict[str, Any],
    summary: dict[str, Any],
    recommendations: list[dict[str, Any]],
    dry_run_actions: list[dict[str, Any]],
    action_config: dict[str, Any],
) -> dict[str, Any]:
    """
    EPSILON PHASE 3A-BRAINBASE:
    Read-only operator intelligence layer.

    This does not execute actions.
    It converts observed health into a compact operator brain block for wall/API/Discord use.
    """
    risk_score = 0
    reasons: list[str] = []

    failed_endpoints = [
        name for name, data in endpoints.items()
        if not (data or {}).get("ok")
    ]
    failed_systemd = [
        name for name, data in systemd.items()
        if not (data or {}).get("ok")
    ]
    failed_docker = [
        name for name, data in docker.items()
        if not (data or {}).get("ok")
    ]
    failed_ports = [
        name for name, data in ports.items()
        if not (data or {}).get("ok")
    ]

    risk_score += len(failed_endpoints) * 12
    risk_score += len(failed_systemd) * 16
    risk_score += len(failed_docker) * 16
    risk_score += len(failed_ports) * 14
    risk_score += len(recommendations) * 8
    risk_score += len(dry_run_actions) * 10

    service_recommendations = [
        rec for rec in recommendations
        if isinstance(rec, dict) and (
            rec.get("category") == "services"
            or str(rec.get("target", "")).startswith("service:")
        )
    ]
    if service_recommendations:
        risk_score += max(8, len(service_recommendations) * 10)
        reasons.append(
            "Service recommendation detected: "
            + ", ".join(str(rec.get("target", "service")) for rec in service_recommendations[:4])
        )

    if failed_endpoints:
        reasons.append("Endpoint failure detected: " + ", ".join(failed_endpoints[:4]))
    if failed_systemd:
        reasons.append("Systemd unit issue detected: " + ", ".join(failed_systemd[:4]))
    if failed_docker:
        reasons.append("Docker container issue detected: " + ", ".join(failed_docker[:4]))
    if failed_ports:
        reasons.append("Port probe issue detected: " + ", ".join(failed_ports[:4]))

    alpha_state = str(summary.get("alpha_state", "UNKNOWN")).upper()
    threat_level = str(summary.get("threat_level", "UNKNOWN")).upper()
    score = int(summary.get("score", 0) or 0)

    if alpha_state not in ("STABLE", "GREEN", "OK"):
        risk_score += 10
        reasons.append(f"Alpha state is {alpha_state}.")
    if threat_level not in ("GREEN", "STABLE", "OK"):
        risk_score += 10
        reasons.append(f"Threat level is {threat_level}.")
    if score >= 7:
        risk_score += 20
        reasons.append(f"Threat score elevated at {score}.")
    elif score >= 3:
        risk_score += 8
        reasons.append(f"Threat score watch at {score}.")

    resources = summary.get("system", {}) if isinstance(summary.get("system"), dict) else {}
    root_disk = float(resources.get("root_disk", 0) or 0)
    media_disk = float(resources.get("media_disk", 0) or 0)
    memory = float(resources.get("memory", 0) or 0)
    cpu = float(resources.get("cpu", 0) or 0)

    if root_disk >= 90:
        risk_score += 20
        reasons.append(f"Root disk critical pressure: {root_disk}%.")
    elif root_disk >= 80:
        risk_score += 10
        reasons.append(f"Root disk watch pressure: {root_disk}%.")

    if media_disk >= 92:
        risk_score += 16
        reasons.append(f"Media disk high pressure: {media_disk}%.")
    elif media_disk >= 85:
        risk_score += 8
        reasons.append(f"Media disk watch pressure: {media_disk}%.")

    if memory >= 90:
        risk_score += 14
        reasons.append(f"Memory pressure high: {memory}%.")
    elif memory >= 75:
        risk_score += 6
        reasons.append(f"Memory pressure watch: {memory}%.")

    if cpu >= 90:
        risk_score += 10
        reasons.append(f"CPU pressure high: {cpu}%.")

    if action_config.get("autonomous_actions_enabled") and not action_config.get("dry_run_enabled"):
        risk_score += 5
        reasons.append("Autonomous live actions are enabled.")

    risk_score = max(0, min(100, int(risk_score)))
    health_score = max(0, 100 - risk_score)

    if risk_score >= 70:
        posture = "CRITICAL"
        confidence = "medium"
        attention_required = True
    elif risk_score >= 40:
        posture = "ALERT"
        confidence = "medium"
        attention_required = True
    elif risk_score >= 15:
        posture = "WATCH"
        confidence = "high"
        attention_required = True
    else:
        posture = "NOMINAL"
        confidence = "high"
        attention_required = False

    if not reasons:
        reasons.append("All observed operator checks are nominal.")

    return {
        "schema_version": "3A.brainbase.1",
        "posture": posture,
        "health_score": health_score,
        "risk_score": risk_score,
        "attention_required": attention_required,
        "confidence": confidence,
        "summary": reasons[0],
        "reasons": reasons[:8],
        "safety": {
            "autonomous_actions_enabled": bool(action_config.get("autonomous_actions_enabled")),
            "dry_run_enabled": bool(action_config.get("dry_run_enabled")),
            "live_restart_wall_ui_allowed": bool(action_config.get("allow_restart_wall_ui")),
            "live_restart_rh_api_allowed": bool(action_config.get("allow_restart_rh_api")),
            "max_actions_per_run": action_config.get("max_actions_per_run"),
            "incident_cooldown_minutes": action_config.get("incident_cooldown_minutes"),
            "require_consecutive_failures": action_config.get("require_consecutive_failures"),
        },
        "counts": {
            "failed_endpoints": len(failed_endpoints),
            "failed_systemd": len(failed_systemd),
            "failed_docker": len(failed_docker),
            "failed_ports": len(failed_ports),
            "recommendations": len(recommendations),
            "dry_run_actions": len(dry_run_actions),
        },
    }

def summarize_decision(dry_run_actions: list[dict[str, Any]], recommendations: list[dict[str, Any]]) -> dict[str, Any]:
    if dry_run_actions:
        first = dry_run_actions[0]
        return {
            "mode": "dry_run",
            "would_act": True,
            "action": first.get("action"),
            "target": first.get("target"),
            "reason": first.get("reason"),
            "count": len(dry_run_actions),
            "timestamp": now_iso(),
        }

    if recommendations:
        first = recommendations[0]
        return {
            "mode": "dry_run",
            "would_act": False,
            "action": "recommendation_only",
            "target": first.get("target", "system"),
            "reason": first.get("recommendation", "review recommended"),
            "count": 0,
            "timestamp": now_iso(),
        }

    return {
        "mode": "dry_run",
        "would_act": False,
        "action": "none",
        "target": "none",
        "reason": "No action required.",
        "count": 0,
        "timestamp": now_iso(),
    }

def decide_recommendations(
    endpoints: dict[str, Any],
    systemd: dict[str, Any],
    docker: dict[str, Any],
    ports: dict[str, Any],
    summary: dict[str, Any],
) -> list[dict[str, Any]]:
    recs: list[dict[str, Any]] = []

    if not ports.get("api_8000", {}).get("ok"):
        recs.append({
            "severity": "CRITICAL",
            "target": "rh-tracker-api",
            "recommendation": "API port 8000 is unreachable. Manual or future autonomous restart candidate.",
            "auto_action_allowed_now": False,
        })

    if not ports.get("wall_3098", {}).get("ok"):
        recs.append({
            "severity": "CRITICAL",
            "target": "wall-ui",
            "recommendation": "Wall UI port 3098 is unreachable. Manual or future autonomous container restart candidate.",
            "auto_action_allowed_now": False,
        })

    for unit, data in systemd.items():
        if not data.get("ok"):
            recs.append({
                "severity": "ALERT",
                "target": unit,
                "recommendation": f"{unit} is not active.",
                "auto_action_allowed_now": False,
            })

    for name, data in docker.items():
        if not data.get("ok"):
            recs.append({
                "severity": "ALERT",
                "target": name,
                "recommendation": f"Docker container {name} is not running.",
                "auto_action_allowed_now": False,
            })

    failed_endpoints = [name for name, data in endpoints.items() if not data.get("ok")]
    if failed_endpoints:
        recs.append({
            "severity": "WATCH",
            "target": "api-endpoints",
            "recommendation": "One or more endpoints failed: " + ", ".join(failed_endpoints),
            "auto_action_allowed_now": False,
        })

    weather = summary.get("weather", {})
    if weather.get("stale"):
        recs.append({
            "severity": "WATCH",
            "target": "weather",
            "recommendation": "Weather cache is stale. Monitor provider/cache before restart.",
            "auto_action_allowed_now": False,
        })

    system = summary.get("system", {})
    root_disk = system.get("root_disk")
    media_disk = system.get("media_disk")

    if isinstance(root_disk, (int, float)) and root_disk >= 90:
        recs.append({
            "severity": "ALERT",
            "target": "root_disk",
            "recommendation": f"Root disk high at {root_disk}%. Manual cleanup recommended.",
            "auto_action_allowed_now": False,
        })

    if isinstance(media_disk, (int, float)) and media_disk >= 92:
        recs.append({
            "severity": "ALERT",
            "target": "media_disk",
            "recommendation": f"Media disk high at {media_disk}%. Manual review recommended.",
            "auto_action_allowed_now": False,
        })

    return recs



def load_previous_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out

    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        out[key] = value

    return out


def load_discord_config() -> dict[str, Any]:
    env = parse_env_file(DISCORD_ENV_PATH)

    enabled = env.get("DISCORD_ENABLED", "false").lower() == "true"
    heartbeat_enabled = env.get("DISCORD_HEARTBEAT_ENABLED", "false").lower() == "true"

    try:
        retry_seconds = int(env.get("DISCORD_RETRY_SECONDS", "10"))
    except Exception:
        retry_seconds = 10

    return {
        "enabled": enabled and bool(env.get("DISCORD_WEBHOOK_URL")),
        "webhook_url": env.get("DISCORD_WEBHOOK_URL"),
        "bot_name": env.get("DISCORD_BOT_NAME", "Priority Alpha Sentinel"),
        "heartbeat_enabled": heartbeat_enabled,
        "retry_seconds": retry_seconds,
        "config_present": DISCORD_ENV_PATH.exists(),
    }


def discord_post(config: dict[str, Any], content: str) -> dict[str, Any]:
    if not config.get("enabled"):
        return {
            "attempted": False,
            "ok": False,
            "error": "discord disabled or webhook missing",
            "status_code": None,
        }

    payload = json.dumps({
        "username": config.get("bot_name", "Priority Alpha Sentinel"),
        "content": content,
    }).encode("utf-8")

    request = urllib.request.Request(
        config["webhook_url"],
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "PriorityAlphaAgent/11B",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=10) as resp:
            return {
                "attempted": True,
                "ok": 200 <= getattr(resp, "status", 204) < 300,
                "status_code": getattr(resp, "status", None),
                "error": None,
            }
    except Exception as e:
        return {
            "attempted": True,
            "ok": False,
            "status_code": None,
            "error": str(e),
        }


def should_send_daily_heartbeat(previous_discord: dict[str, Any]) -> bool:
    last_heartbeat = previous_discord.get("last_heartbeat_at")
    today = datetime.now(TZ).date().isoformat()

    if not last_heartbeat:
        return True

    return not str(last_heartbeat).startswith(today)


def fmt_bool(value: Any) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "unknown"


def build_discord_message(
    event_type: str,
    overall: str,
    summary: dict[str, Any],
    recommendations: list[dict[str, Any]],
    live_action_result: dict[str, Any] | None = None,
    incident_memory: dict[str, Any] | None = None,
) -> str:
    live_action_result = live_action_result or {}
    incident_memory = incident_memory or {}

    alpha = summary.get("alpha_state") or "UNKNOWN"
    threat = summary.get("threat_level") or "UNKNOWN"
    score = summary.get("score")
    active = summary.get("active_events")
    weather = summary.get("weather", {})
    system = summary.get("system", {})
    services = summary.get("services", {})
    fallback_sources = summary.get("fallback_sources", [])

    online_count = sum(1 for v in services.values() if str(v).lower() == "online")
    total_count = len(services)

    action = live_action_result.get("action", "none")
    target = live_action_result.get("target", "none")
    executed = live_action_result.get("executed")
    action_ok = live_action_result.get("ok")
    result = live_action_result.get("result")
    cooldown = live_action_result.get("cooldown", {}) or {}

    action_lines = []
    if action != "none" or cooldown.get("active"):
        action_lines.append("")
        action_lines.append("Action:")
        action_lines.append(f"- `{action}` / `{target}`")
        action_lines.append(f"- executed: `{fmt_bool(executed)}` / ok: `{fmt_bool(action_ok)}`")
        if result:
            action_lines.append(f"- result: `{str(result)[:140]}`")

        if cooldown:
            if cooldown.get("active"):
                action_lines.append(f"- cooldown: `ACTIVE`, remaining `{cooldown.get('remaining_minutes', '—')}m`")
                action_lines.append(f"- last attempt: `{cooldown.get('last_attempt_at', '—')}`")
            elif cooldown.get("recorded"):
                action_lines.append(f"- cooldown: `recorded`, window `{cooldown.get('cooldown_minutes', '—')}m`")
            else:
                action_lines.append(f"- cooldown: `{cooldown.get('reason', 'clear')}`")

    incident_lines = []
    incidents = incident_memory.get("incidents", {}) if isinstance(incident_memory, dict) else {}
    if incidents:
        latest = sorted(
            incidents.values(),
            key=lambda x: str(x.get("last_attempt_at", "")),
            reverse=True
        )[0]
        incident_lines.append("")
        incident_lines.append("Last incident:")
        incident_lines.append(f"- `{latest.get('action', 'unknown')}` / `{latest.get('target', 'unknown')}`")
        incident_lines.append(f"- attempts: `{latest.get('attempt_count', 0)}`")
        incident_lines.append(f"- last attempt: `{latest.get('last_attempt_at', '—')}`")

    base = (
        f"**Priority Alpha Sentinel — {event_type}**\n"
        f"Overall: `{overall}`\n"
        f"Alpha: `{alpha} / {threat} / score {score}`\n"
        f"Events active: `{active}`\n"
        f"Services: `{online_count}/{total_count} online`\n"
        f"Weather: `{weather.get('posture', 'UNKNOWN')} / {weather.get('source_label', 'Unknown')} / stale={weather.get('stale')}`\n"
        f"System: CPU `{system.get('cpu', '—')}%`, RAM `{system.get('memory', '—')}%`, root `{system.get('root_disk', '—')}%`, media `{system.get('media_disk', '—')}%`\n"
    )

    if fallback_sources:
        base += "\nFallback source used:\n"
        base += "\n".join(f"- `{src}`" for src in fallback_sources[:4]) + "\n"

    if action_lines:
        base += "\n" + "\n".join(action_lines) + "\n"

    if incident_lines:
        base += "\n" + "\n".join(incident_lines) + "\n"

    if recommendations:
        top = recommendations[:4]
        rec_text = "\n".join(
            f"- `{r.get('severity', 'INFO')}` {r.get('target', 'unknown')}: {r.get('recommendation', 'review')}"
            for r in top
        )
        base += f"\nRecommendations:\n{rec_text}\n"

    return base[:1900]




def bool_env(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def int_env(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def load_action_config() -> dict[str, Any]:
    env = parse_env_file(ACTIONS_ENV_PATH)

    return {
        "config_present": ACTIONS_ENV_PATH.exists(),
        "autonomous_actions_enabled": bool_env(env.get("AUTONOMOUS_ACTIONS_ENABLED"), False),
        "dry_run_enabled": bool_env(env.get("DRY_RUN_ENABLED"), True),
        "allow_restart_wall_ui": bool_env(env.get("ALLOW_RESTART_WALL_UI"), False),
        "allow_restart_rh_api": bool_env(env.get("ALLOW_RESTART_RH_API"), False),
        "max_actions_per_run": int_env(env.get("MAX_ACTIONS_PER_RUN"), 1),
        "incident_cooldown_minutes": int_env(env.get("INCIDENT_COOLDOWN_MINUTES"), 30),
        "require_consecutive_failures": int_env(env.get("REQUIRE_CONSECUTIVE_FAILURES"), 1),
    }



def load_action_incident_state() -> dict[str, Any]:
    if not ACTION_INCIDENT_PATH.exists():
        return {
            "schema_version": "11F.incident_memory.1",
            "incidents": {},
            "last_updated_at": None,
        }

    try:
        data = json.loads(ACTION_INCIDENT_PATH.read_text())
        if not isinstance(data, dict):
            raise ValueError("incident state is not a JSON object")
        data.setdefault("schema_version", "11F.incident_memory.1")
        data.setdefault("incidents", {})
        return data
    except Exception:
        return {
            "schema_version": "11F.incident_memory.1",
            "incidents": {},
            "last_updated_at": None,
            "load_error": "failed to parse existing incident state",
        }


def save_action_incident_state(data: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    data["schema_version"] = "11F.incident_memory.1"
    data["last_updated_at"] = now_iso()

    tmp = ACTION_INCIDENT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(ACTION_INCIDENT_PATH)


def action_incident_key(action: dict[str, Any]) -> str:
    return f"{action.get('action', 'unknown')}::{action.get('target', 'unknown')}"


def minutes_since_iso(value: Any) -> float | None:
    if not value:
        return None

    try:
        then = datetime.fromisoformat(str(value))
        if then.tzinfo is None:
            then = then.replace(tzinfo=TZ)
        now = datetime.now(TZ)
        return (now - then).total_seconds() / 60.0
    except Exception:
        return None


def cooldown_check(
    candidate: dict[str, Any],
    action_config: dict[str, Any],
    incident_state: dict[str, Any],
) -> dict[str, Any]:
    cooldown_minutes = int(action_config.get("incident_cooldown_minutes", 30))
    key = action_incident_key(candidate)
    incident = incident_state.get("incidents", {}).get(key, {})

    last_attempt_at = incident.get("last_attempt_at")
    elapsed = minutes_since_iso(last_attempt_at)

    if elapsed is not None and elapsed < cooldown_minutes:
        return {
            "allowed": False,
            "key": key,
            "cooldown_minutes": cooldown_minutes,
            "elapsed_minutes": round(elapsed, 2),
            "remaining_minutes": round(cooldown_minutes - elapsed, 2),
            "last_attempt_at": last_attempt_at,
            "last_result": incident.get("last_result"),
        }

    return {
        "allowed": True,
        "key": key,
        "cooldown_minutes": cooldown_minutes,
        "elapsed_minutes": elapsed,
        "remaining_minutes": 0,
        "last_attempt_at": last_attempt_at,
        "last_result": incident.get("last_result"),
    }


def record_action_incident(
    candidate: dict[str, Any],
    result: dict[str, Any],
    incident_state: dict[str, Any],
    cooldown: dict[str, Any],
) -> dict[str, Any]:
    key = cooldown.get("key") or action_incident_key(candidate)
    incidents = incident_state.setdefault("incidents", {})
    previous = incidents.get(key, {})

    incidents[key] = {
        "key": key,
        "target": candidate.get("target"),
        "action": candidate.get("action"),
        "reason": candidate.get("reason"),
        "last_attempt_at": now_iso(),
        "last_result": {
            "executed": result.get("executed"),
            "ok": result.get("ok"),
            "result": result.get("result"),
            "timestamp": result.get("timestamp"),
        },
        "attempt_count": int(previous.get("attempt_count", 0)) + 1,
        "cooldown_minutes": cooldown.get("cooldown_minutes"),
    }

    save_action_incident_state(incident_state)
    return incidents[key]


def execute_live_action(action: dict[str, Any], action_config: dict[str, Any]) -> dict[str, Any]:
    """
    Phase 11E/11F live action executor.

    Only supports two whitelisted low-risk actions:
      - docker restart wall-ui
      - systemctl restart rh-tracker-api.service

    Everything else is refused.
    """
    requested = action.get("action")
    target = action.get("target")

    if not action_config.get("autonomous_actions_enabled"):
        return {
            "executed": False,
            "ok": False,
            "target": target,
            "action": requested,
            "result": "autonomous actions disabled",
            "timestamp": now_iso(),
        }

    if action_config.get("dry_run_enabled"):
        return {
            "executed": False,
            "ok": False,
            "target": target,
            "action": requested,
            "result": "dry run enabled",
            "timestamp": now_iso(),
        }

    if requested == "restart_container" and target == "wall-ui":
        if not action_config.get("allow_restart_wall_ui"):
            return {
                "executed": False,
                "ok": False,
                "target": target,
                "action": requested,
                "result": "wall-ui restart not allowed by config",
                "timestamp": now_iso(),
            }

        result = run_cmd(["sudo", "docker", "restart", "wall-ui"], timeout=30)
        time.sleep(4)
        port = check_port("127.0.0.1", 3098)

        return {
            "executed": True,
            "ok": bool(result.get("ok") and port.get("ok")),
            "target": target,
            "action": requested,
            "command": "sudo docker restart wall-ui",
            "result": result.get("stdout") or result.get("stderr") or "completed",
            "validation": {"wall_3098": port},
            "timestamp": now_iso(),
        }

    if requested == "restart_service" and target == "rh-tracker-api.service":
        if not action_config.get("allow_restart_rh_api"):
            return {
                "executed": False,
                "ok": False,
                "target": target,
                "action": requested,
                "result": "rh-tracker-api restart not allowed by config",
                "timestamp": now_iso(),
            }

        result = run_cmd(["sudo", "systemctl", "restart", "rh-tracker-api.service"], timeout=30)
        time.sleep(5)
        port = check_port("127.0.0.1", 8000)
        active = check_systemd_unit("rh-tracker-api.service")

        return {
            "executed": True,
            "ok": bool(result.get("ok") and port.get("ok") and active.get("ok")),
            "target": target,
            "action": requested,
            "command": "sudo systemctl restart rh-tracker-api.service",
            "result": result.get("stdout") or result.get("stderr") or "completed",
            "validation": {
                "api_8000": port,
                "rh-tracker-api.service": active,
            },
            "timestamp": now_iso(),
        }

    return {
        "executed": False,
        "ok": False,
        "target": target,
        "action": requested,
        "result": "action refused: not in live whitelist",
        "timestamp": now_iso(),
    }

def run_live_action_policy(
    dry_run_actions: list[dict[str, Any]],
    action_config: dict[str, Any],
) -> dict[str, Any]:
    incident_state = load_action_incident_state()

    max_actions = max(0, int(action_config.get("max_actions_per_run", 1)))
    if not dry_run_actions or max_actions <= 0:
        return {
            "action": "none",
            "result": "no action required",
            "timestamp": now_iso(),
            "executed": False,
            "ok": True,
            "cooldown": {
                "active": False,
                "reason": "no action required",
            },
            "incident_state_path": str(ACTION_INCIDENT_PATH),
        }

    # Only one action per run by design unless explicitly raised.
    candidate = dry_run_actions[0]
    cooldown = cooldown_check(candidate, action_config, incident_state)

    if not cooldown.get("allowed"):
        return {
            "executed": False,
            "ok": False,
            "target": candidate.get("target"),
            "action": candidate.get("action"),
            "result": "cooldown active; action suppressed",
            "reason": candidate.get("reason"),
            "timestamp": now_iso(),
            "cooldown": {
                "active": True,
                "key": cooldown.get("key"),
                "cooldown_minutes": cooldown.get("cooldown_minutes"),
                "elapsed_minutes": cooldown.get("elapsed_minutes"),
                "remaining_minutes": cooldown.get("remaining_minutes"),
                "last_attempt_at": cooldown.get("last_attempt_at"),
                "last_result": cooldown.get("last_result"),
            },
            "incident_state_path": str(ACTION_INCIDENT_PATH),
        }

    result = execute_live_action(candidate, action_config)
    incident_record = record_action_incident(candidate, result, incident_state, cooldown)

    result["cooldown"] = {
        "active": False,
        "key": cooldown.get("key"),
        "cooldown_minutes": cooldown.get("cooldown_minutes"),
        "recorded": True,
    }
    result["incident"] = incident_record
    result["incident_state_path"] = str(ACTION_INCIDENT_PATH)

    return result

def run_discord_policy(
    config: dict[str, Any],
    previous_state: dict[str, Any],
    overall: str,
    summary: dict[str, Any],
    recommendations: list[dict[str, Any]],
    live_action_result: dict[str, Any] | None = None,
    incident_memory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    live_action_result = live_action_result or {}
    incident_memory = incident_memory or {}

    previous_discord = previous_state.get("discord", {}) if isinstance(previous_state, dict) else {}
    previous_overall = previous_state.get("overall") if isinstance(previous_state, dict) else None
    previous_action = previous_state.get("live_action_result", {}) if isinstance(previous_state, dict) else {}

    discord_state = {
        "enabled": bool(config.get("enabled")),
        "config_present": bool(config.get("config_present")),
        "heartbeat_enabled": bool(config.get("heartbeat_enabled")),
        "last_success_at": previous_discord.get("last_success_at"),
        "last_failure_at": previous_discord.get("last_failure_at"),
        "last_heartbeat_at": previous_discord.get("last_heartbeat_at"),
        "last_error": previous_discord.get("last_error"),
        "last_event_sent": previous_discord.get("last_event_sent"),
    }

    if not config.get("enabled"):
        return discord_state

    event_type = None

    current_action_fingerprint = {
        "executed": live_action_result.get("executed"),
        "action": live_action_result.get("action"),
        "target": live_action_result.get("target"),
        "result": live_action_result.get("result"),
        "cooldown_active": (live_action_result.get("cooldown") or {}).get("active"),
    }

    previous_action_fingerprint = {
        "executed": previous_action.get("executed"),
        "action": previous_action.get("action"),
        "target": previous_action.get("target"),
        "result": previous_action.get("result"),
        "cooldown_active": (previous_action.get("cooldown") or {}).get("active"),
    }

    if live_action_result.get("executed") and current_action_fingerprint != previous_action_fingerprint:
        event_type = "ACTION EXECUTED"
    elif (live_action_result.get("cooldown") or {}).get("active") and current_action_fingerprint != previous_action_fingerprint:
        event_type = "ACTION SUPPRESSED"
    elif overall == "ATTENTION" and previous_overall != "ATTENTION":
        event_type = "ATTENTION"
    elif overall == "WATCHING" and previous_overall == "ATTENTION":
        event_type = "RECOVERY"
    elif config.get("heartbeat_enabled") and should_send_daily_heartbeat(previous_discord):
        event_type = "HEARTBEAT"

    if not event_type:
        return discord_state

    message = build_discord_message(
        event_type,
        overall,
        summary,
        recommendations,
        live_action_result=live_action_result,
        incident_memory=incident_memory,
    )

    result = discord_post(config, message)

    if not result.get("ok"):
        time.sleep(max(0, int(config.get("retry_seconds", 10))))
        retry = discord_post(config, message)
        result = retry if retry.get("ok") else result

    if result.get("ok"):
        discord_state["last_success_at"] = now_iso()
        discord_state["last_error"] = None
        discord_state["last_event_sent"] = event_type
        if event_type == "HEARTBEAT":
            discord_state["last_heartbeat_at"] = now_iso()
    else:
        discord_state["last_failure_at"] = now_iso()
        discord_state["last_error"] = result.get("error") or "unknown discord failure"

    return discord_state



def append_log(entry: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    if LOG_PATH.exists():
        try:
            log = json.loads(LOG_PATH.read_text())
        except Exception:
            log = {"entries": []}
    else:
        log = {"entries": []}

    entries = log.setdefault("entries", [])
    entries.append(entry)
    log["entries"] = entries[-200:]
    log["last_updated_at"] = now_iso()

    tmp = LOG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(log, indent=2))
    tmp.replace(LOG_PATH)


def main() -> int:
    simulation = phase3c_default_simulation_metadata()
    started = now_iso()

    endpoints = {name: fetch_json(name, url) for name, url in CHECKS.items()}
    systemd = {unit: check_systemd_unit(unit) for unit in SYSTEMD_UNITS}
    docker = {name: check_docker_container(name) for name in DOCKER_CONTAINERS}
    ports = {
        "api_8000": check_port("127.0.0.1", 8000),
        "wall_3098": check_port("127.0.0.1", 3098),
    }

    summary = summarize_endpoint_data(endpoints)

    simulation = apply_phase3c_fault_simulation(endpoints, systemd, docker, ports, summary)
    recommendations = decide_recommendations(endpoints, systemd, docker, ports, summary)
    recommendations = phase3b_recommendations(endpoints, systemd, docker, ports, summary, recommendations)
    recommendations = phase3d_normalize_recommendations(recommendations)
    dry_run_actions = build_dry_run_actions(endpoints, systemd, docker, ports, summary, recommendations)
    decision = summarize_decision(dry_run_actions, recommendations)

    action_config = load_action_config()
    brain = build_brain_assessment(
        endpoints,
        systemd,
        docker,
        ports,
        summary,
        recommendations,
        dry_run_actions,
        action_config,
    )

    healthy = (
        all(v.get("ok") for v in endpoints.values())
        and all(v.get("ok") for v in systemd.values())
        and all(v.get("ok") for v in docker.values())
        and all(v.get("ok") for v in ports.values())
        and not recommendations
    )

    previous_state = load_previous_state()
    operator_mode = "live_guarded_self_healing" if action_config.get("autonomous_actions_enabled") and not action_config.get("dry_run_enabled") else "self_healing_dry_run"
    decision["mode"] = operator_mode

    simulation_result = phase3d_simulation_result(
        simulation,
        recommendations,
        dry_run_actions,
        brain,
    )

    live_action_result = {
        "action": "none",
        "result": "no action required",
        "timestamp": now_iso(),
        "executed": False,
        "ok": True,
    }

    if action_config.get("autonomous_actions_enabled") and not action_config.get("dry_run_enabled"):
        live_action_result = run_live_action_policy(dry_run_actions, action_config)

        # If an action was executed, perform a second observation pass so the final
        # published state reflects post-action health instead of pre-action failure.
        if live_action_result.get("executed"):
            endpoints = {name: fetch_json(name, url) for name, url in CHECKS.items()}
            systemd = {unit: check_systemd_unit(unit) for unit in SYSTEMD_UNITS}
            docker = {name: check_docker_container(name) for name in DOCKER_CONTAINERS}
            ports = {
                "api_8000": check_port("127.0.0.1", 8000),
                "wall_3098": check_port("127.0.0.1", 3098),
            }
            summary = summarize_endpoint_data(endpoints)

            simulation = apply_phase3c_fault_simulation(endpoints, systemd, docker, ports, summary)
            recommendations = decide_recommendations(endpoints, systemd, docker, ports, summary)
            recommendations = phase3b_recommendations(endpoints, systemd, docker, ports, summary, recommendations)
            dry_run_actions = build_dry_run_actions(endpoints, systemd, docker, ports, summary, recommendations)
            decision = summarize_decision(dry_run_actions, recommendations)
            decision["mode"] = operator_mode

            simulation_result = phase3d_simulation_result(
                simulation,
                recommendations,
                dry_run_actions,
                brain,
            )

            healthy = (
                all(v.get("ok") for v in endpoints.values())
                and all(v.get("ok") for v in systemd.values())
                and all(v.get("ok") for v in docker.values())
                and all(v.get("ok") for v in ports.values())
                and not recommendations
            )

    overall = "WATCHING" if healthy else "ATTENTION"
    discord_config = load_discord_config()
    incident_memory_for_report = load_action_incident_state()
    discord_state = run_discord_policy(
        discord_config,
        previous_state,
        overall,
        summary,
        recommendations,
        live_action_result=live_action_result,
        incident_memory=incident_memory_for_report,
    )

    state = {
        "schema_version": "11E.live_guarded_self_healing.1",
        "system_id": "priority-alpha",
        "mode": operator_mode,
        "autonomous_actions_enabled": bool(action_config.get("autonomous_actions_enabled")),
        "dry_run_enabled": bool(action_config.get("dry_run_enabled")),
        "action_config": {
            "config_present": action_config.get("config_present"),
            "allow_restart_wall_ui": action_config.get("allow_restart_wall_ui"),
            "allow_restart_rh_api": action_config.get("allow_restart_rh_api"),
            "max_actions_per_run": action_config.get("max_actions_per_run"),
            "incident_cooldown_minutes": action_config.get("incident_cooldown_minutes"),
            "require_consecutive_failures": action_config.get("require_consecutive_failures"),
        },
        "discord_enabled": bool(discord_config.get("enabled")),
        "started_at": started,
        "generated_at": now_iso(),
        "overall": overall,
        "summary": summary,
        "brain": brain,
        "checks": {
            "endpoints": {
                k: {kk: vv for kk, vv in v.items() if kk != "data"}
                for k, v in endpoints.items()
            },
            "systemd": systemd,
            "docker": docker,
            "ports": ports,
        },
        "simulation": simulation,
        "simulation_result": simulation_result,
        "recommendation_engine": {
            "schema_version": "3B.recommendation_engine.1",
            "dry_run_only": True,
            "categories": [
                "weather",
                "camera",
                "external",
                "services",
                "resources",
                "finance",
                "cache"
            ],
            "recommendation_count": len(recommendations),
            "dry_run_action_count": len(dry_run_actions),
        },
        "recommendations": recommendations,
        "dry_run_actions": dry_run_actions,
        "decision": decision,
        "live_action_result": live_action_result,
        "incident_memory": incident_memory_for_report,
        "last_action": live_action_result,
        "discord": discord_state,
    }

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE_PATH)

    append_log({
        "timestamp": now_iso(),
        "overall": state["overall"],
        "recommendation_count": len(recommendations),
        "dry_run_action_count": len(dry_run_actions),
        "simulation": simulation,
        "simulation_result": simulation_result,
        "decision": decision,
        "alpha_state": summary.get("alpha_state"),
        "threat_level": summary.get("threat_level"),
        "score": summary.get("score"),
    })

    print(json.dumps({
        "generated_at": state["generated_at"],
        "overall": state["overall"],
        "recommendations": recommendations,
        "state_path": str(STATE_PATH),
    }, indent=2))

    return 0  # Completed successfully; operational posture is recorded in state.


if __name__ == "__main__":
    raise SystemExit(main())
