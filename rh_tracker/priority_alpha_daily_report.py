#!/usr/bin/env python3
"""
Priority Alpha Daily Intelligence Report — Phase 12A

Purpose:
  Produce a daily operator report for the passive wall system.

Behavior:
  - Reads live API endpoints.
  - Reads Operator Agent state and incident memory.
  - Finds latest Priority Alpha backup archive.
  - Writes JSON + Markdown report files.
  - Sends concise Discord summary if webhook config is enabled.
  - Does not restart services.
  - Does not perform destructive actions.
"""

from __future__ import annotations

import glob
import json
import os
import urllib.request
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any

RH_ROOT = Path("/home/ap3x/scripts/rh_tracker")
STATE_DIR = RH_ROOT / "state"
REPORT_DIR = STATE_DIR / "daily_reports"
CONFIG_DIR = RH_ROOT / "config"
DISCORD_ENV_PATH = CONFIG_DIR / "discord_webhook.env"

TZ = ZoneInfo("America/New_York")

ENDPOINTS = {
    "ops_brief": "http://127.0.0.1:3098/api/ops/brief",
    "ops_feed": "http://127.0.0.1:3098/api/ops/feed",
    "operator_agent": "http://127.0.0.1:3098/api/operator/agent",
    "weather": "http://127.0.0.1:3098/api/weather",
    "status": "http://127.0.0.1:3098/api/status",
    "external": "http://127.0.0.1:3098/api/external",
    "finance_daily": "http://127.0.0.1:3098/api/finance/daily",
}


def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out

    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def fetch_json(name: str, url: str, timeout: int = 12) -> dict[str, Any]:
    started = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PriorityAlphaDailyReport/12A"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return {
                "name": name,
                "ok": True,
                "status_code": getattr(resp, "status", 200),
                "latency_ms": round((time.time() - started) * 1000, 2),
                "error": None,
                "data": json.loads(raw),
            }
    except Exception as e:
        return {
            "name": name,
            "ok": False,
            "status_code": None,
            "latency_ms": round((time.time() - started) * 1000, 2),
            "error": str(e),
            "data": None,
        }


def latest_backup() -> dict[str, Any]:
    pattern = str(STATE_DIR / "priority_alpha_backups" / "priority-alpha-full-*.tar.gz")
    files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    if not files:
        return {"available": False, "path": None, "size_bytes": None, "mtime": None}

    p = Path(files[0])
    return {
        "available": True,
        "path": str(p),
        "size_bytes": p.stat().st_size,
        "mtime": datetime.fromtimestamp(p.stat().st_mtime, TZ).isoformat(timespec="seconds"),
    }


def money(value: Any) -> str:
    try:
        return f"${float(value):,.2f}"
    except Exception:
        return "—"


def pct(value: Any) -> str:
    try:
        return f"{float(value):.1f}%"
    except Exception:
        return "—"


def build_summary(payloads: dict[str, Any]) -> dict[str, Any]:
    ops = payloads.get("ops_brief", {}).get("data") or {}
    feed = payloads.get("ops_feed", {}).get("data") or {}
    agent = payloads.get("operator_agent", {}).get("data") or {}
    weather = payloads.get("weather", {}).get("data") or {}
    status = payloads.get("status", {}).get("data") or {}
    external = payloads.get("external", {}).get("data") or {}
    finance = payloads.get("finance_daily", {}).get("data") or {}

    state = ops.get("state", {})
    services = ops.get("services", {})
    resources = ops.get("resources", {})
    latency = ops.get("latency", {})
    events = ops.get("events", {})
    data_quality = ops.get("data_quality", {})

    agent_live = agent.get("live_action_result", {}) or {}
    agent_incidents = (agent.get("incident_memory", {}) or {}).get("incidents", {}) or {}

    return {
        "created_at": now_iso(),
        "priority_alpha": {
            "overall_state": state.get("overall_state"),
            "threat_level": state.get("threat_level"),
            "score": state.get("score"),
            "summary": state.get("summary") or state.get("operator_summary"),
            "events": events,
            "data_quality": data_quality,
        },
        "agent": {
            "mode": agent.get("mode"),
            "overall": agent.get("overall"),
            "autonomous_actions_enabled": agent.get("autonomous_actions_enabled"),
            "dry_run_enabled": agent.get("dry_run_enabled"),
            "last_action": agent_live,
            "discord": agent.get("discord", {}),
            "incident_count": len(agent_incidents),
            "incidents": agent_incidents,
        },
        "services": {
            "posture": services.get("posture"),
            "summary": services.get("summary"),
            "counts": services.get("counts"),
            "raw_status": status.get("services", {}),
            "external": external,
        },
        "resources": {
            "posture": resources.get("posture"),
            "summary": resources.get("summary"),
            "metrics": resources.get("metrics"),
            "system": status.get("system", {}),
        },
        "latency": {
            "posture": latency.get("posture"),
            "summary": latency.get("summary"),
            "status_engine": latency.get("status_engine", {}),
        },
        "weather": {
            "posture": weather.get("posture"),
            "source_label": weather.get("source_label"),
            "cache_source": weather.get("cache_source"),
            "stale": weather.get("stale"),
            "temperature": weather.get("temperature"),
            "feels_like": weather.get("feels_like"),
            "wind_mph": weather.get("wind_mph"),
            "gust_mph": weather.get("gust_mph"),
            "rain_chance": weather.get("rain_chance"),
            "precip_timeline": weather.get("precip_timeline", {}),
        },
        "finance": finance,
        "feed": {
            "counts": feed.get("counts", {}),
            "top_items": (feed.get("items") or [])[:5],
        },
        "backup": latest_backup(),
        "endpoint_health": {
            name: {
                "ok": data.get("ok"),
                "latency_ms": data.get("latency_ms"),
                "error": data.get("error"),
            }
            for name, data in payloads.items()
        },
    }


def format_report_markdown(summary: dict[str, Any]) -> str:
    alpha = summary["priority_alpha"]
    agent = summary["agent"]
    services = summary["services"]
    resources = summary["resources"]
    weather = summary["weather"]
    finance = summary["finance"]
    backup = summary["backup"]
    latency = summary["latency"]

    weather_timeline = weather.get("precip_timeline") or {}
    system = resources.get("system") or {}
    service_counts = (services.get("counts") or {})
    dq = alpha.get("data_quality") or {}
    events = alpha.get("events") or {}
    last_action = agent.get("last_action") or {}

    lines = [
        "# Priority Alpha Daily Intelligence Report",
        "",
        f"Generated: {summary['created_at']}",
        "",
        "## Command Posture",
        f"- Alpha: {alpha.get('overall_state')} / {alpha.get('threat_level')} / score {alpha.get('score')}",
        f"- Active events: {events.get('active')} / total {events.get('total')} / recovered {events.get('recovered')}",
        f"- Data quality errors: {len(dq.get('errors') or [])}",
        "",
        "## Operator Agent",
        f"- Mode: {agent.get('mode')}",
        f"- Overall: {agent.get('overall')}",
        f"- Autonomous actions: {agent.get('autonomous_actions_enabled')}",
        f"- Last action: {last_action.get('action')} / executed={last_action.get('executed')} / ok={last_action.get('ok')}",
        f"- Incident count: {agent.get('incident_count')}",
        "",
        "## Services / Resources",
        f"- Services: {service_counts.get('online')}/{service_counts.get('total')} online; posture {services.get('posture')}",
        f"- Resources: {resources.get('posture')}",
        f"- CPU: {pct(system.get('cpu'))}, RAM: {pct(system.get('memory'))}, root: {pct(system.get('root_disk'))}, media: {pct(system.get('media_disk'))}",
        f"- Latency: {latency.get('posture')} — {latency.get('summary')}",
        "",
        "## Weather",
        f"- Source: {weather.get('source_label')} / stale={weather.get('stale')}",
        f"- Temp: {weather.get('temperature')}°F, feels {weather.get('feels_like')}°F",
        f"- Wind: {weather.get('wind_mph')} mph, gust {weather.get('gust_mph')} mph",
        f"- Rain chance: {weather.get('rain_chance')}%",
        f"- Next precip: {weather_timeline.get('next_precip_at')} / in {weather_timeline.get('minutes_until_precip')} min",
        f"- Last precip: {weather_timeline.get('last_precip_at')} / {weather_timeline.get('minutes_since_precip')} min ago",
        "",
        "## Finance",
        f"- Equity: {money(finance.get('equity') or finance.get('market_value'))}",
        f"- Daily change: {money(finance.get('daily_change'))} / {finance.get('daily_percent', '—')}%",
        "",
        "## Backup",
        f"- Latest backup available: {backup.get('available')}",
        f"- Latest backup: {backup.get('path')}",
        f"- Backup time: {backup.get('mtime')}",
    ]

    return "\n".join(lines) + "\n"


def format_discord(summary: dict[str, Any]) -> str:
    alpha = summary["priority_alpha"]
    agent = summary["agent"]
    services = summary["services"]
    resources = summary["resources"]
    weather = summary["weather"]
    backup = summary["backup"]
    finance = summary["finance"]

    service_counts = services.get("counts") or {}
    system = resources.get("system") or {}
    last_action = agent.get("last_action") or {}
    timeline = weather.get("precip_timeline") or {}

    msg = (
        "**Priority Alpha Daily Intelligence Report**\n"
        f"Alpha: `{alpha.get('overall_state')} / {alpha.get('threat_level')} / score {alpha.get('score')}`\n"
        f"Agent: `{agent.get('overall')} / {agent.get('mode')}`\n"
        f"Last action: `{last_action.get('action')} / executed={last_action.get('executed')} / ok={last_action.get('ok')}`\n"
        f"Incidents tracked: `{agent.get('incident_count')}`\n"
        f"Services: `{service_counts.get('online')}/{service_counts.get('total')} online`\n"
        f"Resources: CPU `{system.get('cpu', '—')}%`, RAM `{system.get('memory', '—')}%`, root `{system.get('root_disk', '—')}%`, media `{system.get('media_disk', '—')}%`\n"
        f"Weather: `{weather.get('source_label')} / stale={weather.get('stale')}`, next rain `{timeline.get('minutes_until_precip')}m`\n"
        f"Finance: `{money(finance.get('equity') or finance.get('market_value'))}`, daily `{money(finance.get('daily_change'))}`\n"
        f"Backup: `{backup.get('mtime')}`\n"
    )

    errors = (alpha.get("data_quality") or {}).get("errors") or []
    if errors:
        msg += f"Data quality errors: `{len(errors)}`\n"

    return msg[:1900]


def discord_post(content: str) -> dict[str, Any]:
    env = parse_env_file(DISCORD_ENV_PATH)
    enabled = env.get("DISCORD_ENABLED", "false").lower() == "true"
    webhook = env.get("DISCORD_WEBHOOK_URL")
    bot_name = env.get("DISCORD_BOT_NAME", "Priority Alpha Sentinel")

    if not enabled or not webhook:
        return {"attempted": False, "ok": False, "error": "discord disabled or missing webhook"}

    payload = json.dumps({
        "username": bot_name,
        "content": content,
    }).encode("utf-8")

    req = urllib.request.Request(
        webhook,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "PriorityAlphaDailyReport/12A"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return {
                "attempted": True,
                "ok": 200 <= getattr(resp, "status", 204) < 300,
                "status_code": getattr(resp, "status", None),
                "error": None,
            }
    except Exception as e:
        return {"attempted": True, "ok": False, "status_code": None, "error": str(e)}


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(TZ).strftime("%Y%m%d-%H%M%S")

    payloads = {name: fetch_json(name, url) for name, url in ENDPOINTS.items()}
    summary = build_summary(payloads)
    markdown = format_report_markdown(summary)

    json_path = REPORT_DIR / f"priority-alpha-daily-{stamp}.json"
    md_path = REPORT_DIR / f"priority-alpha-daily-{stamp}.md"

    json_path.write_text(json.dumps(summary, indent=2))
    md_path.write_text(markdown)

    discord_result = discord_post(format_discord(summary))

    result = {
        "created_at": summary["created_at"],
        "json_path": str(json_path),
        "markdown_path": str(md_path),
        "discord": discord_result,
        "alpha": summary["priority_alpha"],
        "agent": {
            "overall": summary["agent"].get("overall"),
            "mode": summary["agent"].get("mode"),
            "incident_count": summary["agent"].get("incident_count"),
        },
    }

    print(json.dumps(result, indent=2))
    return 0 if all(v.get("ok") for v in payloads.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
