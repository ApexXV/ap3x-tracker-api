#!/usr/bin/env python3
from __future__ import annotations
from zoneinfo import ZoneInfo

import json
import glob
import logging
import os
import re
import socket
import sys
import time
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional
from ops_state import build_ops_summary, build_history_summary, build_trend_summary, build_event_summary, apply_event_lifecycle, update_service_state_from_status_payload, build_service_summary, update_data_quality, update_threat_state_from_payload, sync_service_condition_into_current_state, update_latency_state_from_measurement, build_latency_summary, sync_latency_condition_into_current_state, update_resource_state_from_status_payload, build_resource_summary, sync_resource_condition_into_current_state, build_alpha_overview, build_alpha_brief, build_ops_feed, sync_backup_events_from_payload, sync_service_events_from_state, sync_resource_events_from_state, sync_latency_events_from_state, sync_data_quality_events_from_state

import psutil
import requests
import urllib3

# PASS ALPHA-A: suppress expected verify=False health-probe warning spam.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator

ROBINHOOD_SNAPSHOT = Path("/home/ap3x/scripts/rh_tracker/robinhood_snapshot.json")
INTENTIONAL_OFFLINE_GAMES = {"rust", "cs2", "pz", "minecraft", "minecraft_modded"}
CRITICAL_GAMES = {"terraria_calamity"}


sys.path.insert(0, str(Path(__file__).parent))
from trackerctl import (
    DEFAULT_SHARES_FILE,
    SERVICE_NAME,
    read_shares,
    write_shares,
    restart_service,
)
from voo_nvda_tracker import fetch_price

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("tracker_api")

app = FastAPI(title="rh-tracker API", version="4.0", redoc_url=None)

# ===== ALPHA-G18B stale-while-revalidate helpers =====
# Goal: high-frequency wall endpoints must never block on expensive rebuilds
# when a last-known-good payload already exists.
_ALPHA_REFRESH_LOCKS = {}
_ALPHA_WARM_CACHE = {
    "payload": None,
    "fetched_at": 0.0,
    "ttl_seconds": 20.0,
    "in_progress": False,
    "last_started_at": 0.0,
    "last_finished_at": 0.0,
}

def _alpha_deepcopy_payload(payload):
    return json.loads(json.dumps(payload))

def _alpha_cache_meta(layer, hit, age, ttl_seconds, stale=False, refreshing=False):
    return {
        "enabled": True,
        "hit": bool(hit),
        "stale": bool(stale),
        "refreshing": bool(refreshing),
        "age_seconds": round(float(age or 0.0), 2),
        "ttl_seconds": ttl_seconds,
        "layer": layer,
        "policy": "stale_while_revalidate",
    }

def _alpha_return_cached(cache, layer, ttl_seconds, age, stale=False, refreshing=False):
    payload = cache.get("payload")
    if not isinstance(payload, dict):
        return None
    cloned = _alpha_deepcopy_payload(payload)
    cloned["_cache"] = _alpha_cache_meta(
        layer=layer,
        hit=True,
        age=age,
        ttl_seconds=ttl_seconds,
        stale=stale,
        refreshing=refreshing,
    )
    return cloned

def _alpha_refresh_lock(name):
    lock = _ALPHA_REFRESH_LOCKS.get(name)
    if lock is None:
        lock = threading.Lock()
        _ALPHA_REFRESH_LOCKS[name] = lock
    return lock

def _alpha_spawn_refresh(name, target):
    lock = _alpha_refresh_lock(name)
    if lock.locked():
        return False

    def runner():
        with lock:
            try:
                target()
            except Exception as exc:
                log.warning("ALPHA-G18B background refresh failed for %s: %s", name, exc)

    thread = threading.Thread(target=runner, name=f"alpha-g18b-refresh-{name}", daemon=True)
    thread.start()
    return True


# ===== ALPHA-G19A games/threat cache helpers =====
# Goal: avoid serial game-port probes on every wall/threat poll.
_ALPHA_GAMES_CACHE = {
    "payload": None,
    "fetched_at": 0.0,
    "ttl_seconds": 45.0,
}

def _alpha_games_cache_meta(hit, age, ttl_seconds, stale=False, refreshing=False):
    return {
        "enabled": True,
        "hit": bool(hit),
        "stale": bool(stale),
        "refreshing": bool(refreshing),
        "age_seconds": round(float(age or 0.0), 2),
        "ttl_seconds": ttl_seconds,
        "layer": "games",
        "policy": "stale_while_revalidate",
    }

def _alpha_return_games_cached(age, stale=False, refreshing=False):
    payload = _ALPHA_GAMES_CACHE.get("payload")
    if not isinstance(payload, dict):
        return None
    cloned = _alpha_deepcopy_payload(payload)
    cloned["_cache"] = _alpha_games_cache_meta(
        hit=True,
        age=age,
        ttl_seconds=float(_ALPHA_GAMES_CACHE.get("ttl_seconds") or 45.0),
        stale=stale,
        refreshing=refreshing,
    )
    return cloned

def _refresh_games_cache_g19a(return_payload=False):
    started = time.monotonic()
    payload = check_game_servers()
    if isinstance(payload, dict):
        _ALPHA_GAMES_CACHE["payload"] = _alpha_deepcopy_payload(payload)
        _ALPHA_GAMES_CACHE["fetched_at"] = started

        returned = _alpha_deepcopy_payload(payload)
        returned["_cache"] = _alpha_games_cache_meta(
            hit=False,
            age=0.0,
            ttl_seconds=float(_ALPHA_GAMES_CACHE.get("ttl_seconds") or 45.0),
            stale=False,
            refreshing=False,
        )
        if return_payload:
            return returned

    if return_payload:
        return payload
    return None

def get_cached_game_servers_g19a():
    """
    Threat-safe game telemetry accessor.

    Returns last-known-good game state immediately when available.
    Triggers background refresh if stale, but never makes threat wait on serial
    offline game-port timeouts unless this is a cold start.
    """
    ttl_seconds = float(_ALPHA_GAMES_CACHE.get("ttl_seconds") or 45.0)
    now = time.monotonic()
    fetched_at = float(_ALPHA_GAMES_CACHE.get("fetched_at") or 0.0)
    age = now - fetched_at if fetched_at else 999999.0
    payload = _ALPHA_GAMES_CACHE.get("payload")

    if isinstance(payload, dict) and age < ttl_seconds:
        return _alpha_deepcopy_payload(payload)

    if isinstance(payload, dict):
        _alpha_spawn_refresh("games", lambda: _refresh_games_cache_g19a(return_payload=False))
        return _alpha_deepcopy_payload(payload)

    # Cold start only: no last-known-good exists yet.
    cold = _refresh_games_cache_g19a(return_payload=True)
    if isinstance(cold, dict):
        cold.pop("_cache", None)
    return cold


# ===== ALPHA-G19B threat cache helpers =====
# Goal: /threat should not spend ~1s rebuilding on every wall poll.
_ALPHA_THREAT_CACHE = {
    "payload": None,
    "fetched_at": 0.0,
    "ttl_seconds": 20.0,
}

def _alpha_threat_cache_meta(hit, age, ttl_seconds, stale=False, refreshing=False):
    return {
        "enabled": True,
        "hit": bool(hit),
        "stale": bool(stale),
        "refreshing": bool(refreshing),
        "age_seconds": round(float(age or 0.0), 2),
        "ttl_seconds": ttl_seconds,
        "layer": "threat",
        "policy": "stale_while_revalidate",
    }

def _alpha_return_threat_cached(age, stale=False, refreshing=False):
    payload = _ALPHA_THREAT_CACHE.get("payload")
    if not isinstance(payload, dict):
        return None
    cloned = _alpha_deepcopy_payload(payload)
    cloned["_cache"] = _alpha_threat_cache_meta(
        hit=True,
        age=age,
        ttl_seconds=float(_ALPHA_THREAT_CACHE.get("ttl_seconds") or 20.0),
        stale=stale,
        refreshing=refreshing,
    )
    return cloned

def _refresh_threat_cache_g19b(return_payload=False):
    started = time.monotonic()
    payload = calculate_threat_level()

    if isinstance(payload, dict):
        _ALPHA_THREAT_CACHE["payload"] = _alpha_deepcopy_payload(payload)
        _ALPHA_THREAT_CACHE["fetched_at"] = started

        returned = _alpha_deepcopy_payload(payload)
        returned["_cache"] = _alpha_threat_cache_meta(
            hit=False,
            age=0.0,
            ttl_seconds=float(_ALPHA_THREAT_CACHE.get("ttl_seconds") or 20.0),
            stale=False,
            refreshing=False,
        )
        if return_payload:
            return returned

    if return_payload:
        return payload
    return None

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://tracker.ap3xservers.com",
        "https://tracker-ui.ap3xservers.com",
        "https://dashboard.ap3xservers.com",
        "https://status.ap3xservers.com",

        "http://192.168.4.115:3099",
        "http://rackmain1:3099",
        "http://rackmain1.local:3099",
        "http://192.168.4.62:3099",
    ],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

_last_request: dict[str, float] = {}
RATE_LIMIT_SECONDS = 2

# Phase 8A: Weather provider cache / rate-limit protection.
WEATHER_CACHE_TTL_SECONDS = 900
WEATHER_ALERTS_CACHE_TTL_SECONDS = 900
WEATHER_FAILURE_TTL_SECONDS = 180

_weather_cache = {
    "payload": None,
    "fetched_at": 0.0,
    "failed_at": 0.0,
    "last_error": None,
}

_weather_alerts_cache = {
    "payload": None,
    "fetched_at": 0.0,
    "failed_at": 0.0,
    "last_error": None,
}


def check_rate_limit(key: str) -> None:
    now = time.monotonic()
    last = _last_request.get(key, 0.0)
    if now - last < RATE_LIMIT_SECONDS:
        remaining = round(RATE_LIMIT_SECONDS - (now - last), 1)
        raise HTTPException(
            status_code=429,
            detail=f"Too many requests. Try again in {remaining}s.",
        )
    _last_request[key] = now


class UpdateRequest(BaseModel):
    voo: Optional[float] = None
    nvda: Optional[float] = None
    restart: bool = False

    @field_validator("voo", "nvda", mode="before")
    @classmethod
    def must_be_non_negative(cls, v):
        if v is not None and float(v) < 0:
            raise ValueError("share quantities cannot be negative")
        return v


class SharesResponse(BaseModel):
    voo: float
    nvda: float
    restarted: Optional[bool] = None


class HealthResponse(BaseModel):
    status: str


class PortfolioValueResponse(BaseModel):
    voo_shares: float
    nvda_shares: float
    voo_price: float
    nvda_price: float
    voo_value: float
    nvda_value: float
    total_value: float
    updated_at: str


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.monotonic()
    response = await call_next(request)
    elapsed = round((time.monotonic() - start) * 1000, 1)
    log.info(
        "%s %s — %s (%sms) — client: %s",
        request.method,
        request.url.path,
        response.status_code,
        elapsed,
        request.client.host if request.client else "unknown",
    )
    return response


def check_port(host: str, port: int, timeout: int = 2) -> str:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return "online"
    except Exception:
        return "offline"


def check_http(url: str) -> str:
    try:
        r = requests.get(url, timeout=3, allow_redirects=True, verify=False)
        if r.status_code < 500:
            return "online"
        return "degraded"
    except Exception:
        return "offline"


def get_disk_percent(path: str):
    try:
        return psutil.disk_usage(path).percent
    except Exception:
        return None


def get_uptime_hours():
    try:
        return round((time.time() - psutil.boot_time()) / 3600, 1)
    except Exception:
        return None


def get_load():
    try:
        if hasattr(os, "getloadavg"):
            load1, load5, load15 = os.getloadavg()
            return {
                "1m": round(load1, 2),
                "5m": round(load5, 2),
                "15m": round(load15, 2),
            }
    except Exception:
        pass
    return None


def get_system_stats():
    return {
        "cpu": psutil.cpu_percent(interval=1),
        "memory": psutil.virtual_memory().percent,
        "root_disk": psutil.disk_usage("/").percent,
        "media_disk": get_disk_percent("/home/md-media/media"),
        "uptime_hours": get_uptime_hours(),
        "load": get_load(),
    }


def load_robinhood_snapshot():
    if not ROBINHOOD_SNAPSHOT.exists():
        return {}
    try:
        with open(ROBINHOOD_SNAPSHOT) as f:
            return json.load(f)
    except Exception:
        return {}


def get_file_age_hours(path: str):
    try:
        mtime = os.path.getmtime(path)
        return round((time.time() - mtime) / 3600, 1)
    except Exception:
        return None


def get_backup_status():
    return {
        "homepage": get_file_age_hours("/home/ap3x/backups/homepage/latest.tar.gz"),
        "docker_compose": get_file_age_hours("/home/ap3x/backups/media-automation/latest.tar.gz"),
    }


def check_game_servers():
    apex = "192.168.4.54"
    return {
        "minecraft": check_port(apex, 25565),
        "minecraft_modded": check_port(apex, 25566),
        # Terraria Calamity is hosted locally on rackmain1 via Docker/NAT.
        "terraria_calamity": check_port("127.0.0.1", 7777),
        "rust": check_port(apex, 28015),
        "cs2": check_port(apex, 27015),
        "pz": check_port(apex, 27025),
    }


def get_status():
    stats = get_system_stats()
    return {
        "services": {
            "qbit": check_http("http://127.0.0.1:8090"),
            "sonarr": check_http("http://127.0.0.1:8989"),
            "radarr": check_http("http://127.0.0.1:7878"),
            "prowlarr": check_http("http://127.0.0.1:9696"),
            "readarr": check_http("http://127.0.0.1:8787"),
            "portainer": check_http("https://127.0.0.1:9443"),
            "plex": check_port("127.0.0.1", 32400),
        },
        "system": stats,
    }


def calculate_threat_level():
    status_data = get_status()
    backups = get_backup_status()
    # ALPHA-G19A:
    # Use cached game telemetry so /threat does not wait on serial offline port probes.
    games = get_cached_game_servers_g19a()
    weather_alerts = get_weather_alerts()
    external = get_external_health()

    score = 0
    reasons = []

    system = status_data["system"]

    if system["cpu"] and system["cpu"] > 90:
        score += 2
        reasons.append(f"CPU critical: {system['cpu']}%")

    if system["memory"] and system["memory"] > 90:
        score += 2
        reasons.append(f"Memory critical: {system['memory']}%")

    if system["root_disk"] and system["root_disk"] > 90:
        score += 3
        reasons.append(f"Root disk critical: {system['root_disk']}%")

    if system["media_disk"] and system["media_disk"] > 90:
        score += 3
        reasons.append(f"Media disk critical: {system['media_disk']}%")

    for service, state in status_data["services"].items():
        if state != "online":
            score += 1
            reasons.append(f"{service} service {state}")

    for game, state in games.items():
        if game in INTENTIONAL_OFFLINE_GAMES:
            continue

        if game in CRITICAL_GAMES and state != "online":
            score += 2
            reasons.append(f"{game.replace('_', ' ')} offline")

    for name, age in backups.items():
        if age is None:
            score += 2
            reasons.append(f"{name} backup missing")
        elif age > 168:
            score += 2
            reasons.append(f"{name} backup stale: {age}h")
        elif age > 72:
            score += 1
            reasons.append(f"{name} backup aging: {age}h")

    # Weather alerts (interpreted)
    seen_weather_reasons = set()
    for alert in weather_alerts.get("alerts", []):
        severity = (alert.get("severity") or "").lower()
        event = (alert.get("event") or "").lower()
        description = (alert.get("description") or "").lower()

        # Interpret what the alert actually means
        if "snow" in event or "snow" in description:
            reason = "Snow expected — accumulation and reduced visibility possible"

        elif "rain" in event or "rain" in description:
            reason = "Rain system active — wet conditions and reduced traction"

        elif "wind" in event or "wind" in description:
            reason = "Strong winds expected — possible debris and instability"

        elif "storm" in event or "thunder" in event:
            reason = "Storm activity — lightning and heavy rainfall possible"

        elif "freeze" in event or "frost" in event:
            reason = "Freezing conditions — ice risk on surfaces"

        elif "fog" in event:
            reason = "Low visibility due to fog — hazardous driving conditions"

        elif "heat" in event:
            reason = "Elevated temperatures — heat stress risk"

        else:
            text = description or (alert.get("headline") or alert.get("event") or "Weather Alert")
            reason = f"Weather advisory - {text[:140]}"

        # Apply severity scoring
        if reason not in seen_weather_reasons:
            seen_weather_reasons.add(reason)

            if severity in ["extreme", "severe"]:
                score += 4
            elif severity == "moderate":
                score += 2
            else:
                score += 1

            reasons.append(reason)

    # Current weather intelligence
    weather_now = get_weather()

    rain_chance = as_float(weather_now.get("rain_chance"))
    precip = as_float(weather_now.get("precipitation"))
    wind = as_float(weather_now.get("wind_mph"))
    temp = as_float(weather_now.get("temp"))

    # Weather probability alone should not escalate Alpha.
    # Open-Meteo daily precipitation probability can stay high after a storm passes,
    # so require current precipitation, hazardous wind, freezing risk, or official alerts
    # before converting rain probability into WATCH/YELLOW score.
    active_precip = precip > 0
    meaningful_precip = precip > 0.02
    high_rain_probability = rain_chance >= 85
    elevated_wind = wind >= 25

    if active_precip:
        score += 1
        reasons.append(f"Active precipitation — {precip} in currently falling")

    if high_rain_probability and not active_precip:
        timeline = weather_now.get("precip_timeline") or {}
        until_precip = timeline.get("minutes_until_precip")
        since_precip = timeline.get("minutes_since_precip")
        until_storm = timeline.get("minutes_until_storm")
        next_precip_amount = as_float(timeline.get("next_precip_amount"))
        peak_precip_12h = as_float(timeline.get("peak_precip_next_12h"))
        peak_prob_12h = as_float(timeline.get("peak_probability_next_12h"))

        # Phase 12D.6:
        # Daily rain probability can remain high after a storm has already passed.
        # Do not create a threat-driver from "last rain" alone, and only treat
        # high probability as operationally relevant if precip/storm is near future.
        # Ignore zero-minute phantom storm/rain timings unless precipitation is active.
        # These can occur when hourly weather-code windows round onto "now".
        near_storm = until_storm is not None and 5 <= as_float(until_storm) <= 360
        near_precip = until_precip is not None and 5 <= as_float(until_precip) <= 360
        measurable_future = next_precip_amount >= 0.02 or peak_precip_12h >= 0.02
        meaningful_future_prob = peak_prob_12h >= 55 and (near_precip or near_storm)

        if near_storm:
            timing = f"storm risk {format_minutes_phrase(until_storm, future=True)}"
            reasons.append(f"Rain likely later — {rain_chance}% probability, {timing}, no active precipitation")
        elif near_precip and (measurable_future or meaningful_future_prob):
            timing = f"rain expected {format_minutes_phrase(until_precip, future=True)}"
            reasons.append(f"Rain likely later — {rain_chance}% probability, {timing}, no active precipitation")
        elif since_precip is not None and as_float(since_precip) <= 360 and measurable_future:
            timing = f"last rain {format_minutes_phrase(since_precip, future=False)}"
            reasons.append(f"Rain likely later — {rain_chance}% probability, {timing}, no active precipitation")

    if (high_rain_probability and (active_precip or elevated_wind)) or meaningful_precip:
        score += 2
        reasons.append("Incoming weather system — elevated precipitation risk")

    if wind >= 25:
        score += 1
        reasons.append(f"Elevated wind — {wind} mph gusts")

    if wind >= 40:
        score += 3
        reasons.append(f"High wind risk — {wind} mph, potential hazards")

    if temp <= 32 and precip > 0:
        score += 2
        reasons.append("Freezing precipitation risk — icy conditions likely")

    # Fuse related weather reasons into one cleaner operational line
    has_rain = any("rain" in r.lower() or "precipitation" in r.lower() for r in reasons)
    has_wind = any("wind" in r.lower() or "gust" in r.lower() for r in reasons)

    if has_rain and has_wind:
        reasons = [
            r for r in reasons
            if not (
                "rain" in r.lower()
                or "precipitation" in r.lower()
                or "wind" in r.lower()
                or "gust" in r.lower()
            )
        ]

        reasons.insert(
            0,
            "Gusty rain system — elevated winds, reduced traction, and reduced visibility"
        )

    # Phase 12D.6:
    # Remove stale weather reasons that are not currently actionable.
    # Weather timeline still lives in the Weather panel; Threat Drivers should only
    # show current or near-future operational risk.
    try:
        timeline = weather_now.get("precip_timeline") or {}
        since_precip = timeline.get("minutes_since_precip")
        until_precip = timeline.get("minutes_until_precip")
        until_storm = timeline.get("minutes_until_storm")
        peak_precip_12h = as_float(timeline.get("peak_precip_next_12h"))
        current_temp = as_float(weather_now.get("temperature") or weather_now.get("temp"))
        feels_like = as_float(weather_now.get("feels_like"), current_temp)

        old_precip_context = (
            precip <= 0
            and until_precip is None
            and until_storm is None
            and (since_precip is None or as_float(since_precip) > 360)
            and peak_precip_12h < 0.02
        )

        if old_precip_context:
            reasons = [
                r for r in reasons
                if "rain likely later" not in r.lower()
                and "incoming weather system" not in r.lower()
                and "active precipitation" not in r.lower()
            ]

        warm_surface_context = min(current_temp, feels_like) > 38 and precip <= 0
        if warm_surface_context:
            reasons = [
                r for r in reasons
                if "freezing conditions" not in r.lower()
                and "freezing precipitation" not in r.lower()
                and "ice risk" not in r.lower()
            ]
    except Exception:
        pass

    for name, data in external.items():

        # Ignore metadata/cache objects.
        if name == "_cache":
            continue

        state = data.get("state")
        latency = data.get("latency_ms")

        if state != "online":
            score += 1
            reasons.append(f"External endpoint {name}: {state}")
        elif latency and latency > 2500:
            score += 1
            reasons.append(f"External endpoint {name} slow: {latency}ms")

    # Phase 12D.6b:
    # If all stale/non-actionable reasons were decayed away, normalize score too.
    # Threat score should not remain elevated without any current driver.
    if not reasons:
        score = 0

    if score <= 2:
        level = "GREEN"
    elif score <= 5:
        level = "YELLOW"
    elif score <= 9:
        level = "ORANGE"
    elif score <= 14:
        level = "RED"
    else:
        level = "BLACK"

    return {
        "level": level,
        "score": score,
        "reasons": reasons[:6],
    }

@app.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse(status="ok")


@app.get("/shares", response_model=SharesResponse)
def shares():
    shares_data = read_shares(DEFAULT_SHARES_FILE)
    return SharesResponse(voo=shares_data["VOO"], nvda=shares_data["NVDA"])


@app.get("/status")
def status():
    """
    PASS ALPHA-D:
    Cached status telemetry.

    /status is polled directly and is also consumed by Alpha refresh logic.
    Keep a very short TTL so CPU/RAM/disk remain fresh while avoiding repeated
    expensive full telemetry rebuilds during wall/operator bursts.
    """
    ttl_seconds = 8.0
    now = time.monotonic()

    cache = globals().setdefault(
        "_STATUS_CACHE",
        {
            "payload": None,
            "fetched_at": 0.0,
            "ttl_seconds": ttl_seconds,
        },
    )

    payload = cache.get("payload")
    fetched_at = float(cache.get("fetched_at") or 0.0)
    age = now - fetched_at if fetched_at else 999999.0

    if isinstance(payload, dict) and age < ttl_seconds:
        status_cached = json.loads(json.dumps(payload))
        status_cached["_cache"] = {
            "enabled": True,
            "hit": True,
            "age_seconds": round(age, 2),
            "ttl_seconds": ttl_seconds,
            "layer": "status",
        }
        return status_cached

    fresh = get_status()

    if isinstance(fresh, dict):
        cache["payload"] = json.loads(json.dumps(fresh))
        cache["fetched_at"] = now
        cache["ttl_seconds"] = ttl_seconds

        fresh["_cache"] = {
            "enabled": True,
            "hit": False,
            "age_seconds": 0.0,
            "ttl_seconds": ttl_seconds,
            "layer": "status",
        }

    return fresh


@app.post("/shares/update", response_model=SharesResponse)
def update_shares(req: UpdateRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    check_rate_limit(f"update:{client_ip}")

    if req.voo is None and req.nvda is None:
        raise HTTPException(
            status_code=422,
            detail="At least one of 'voo' or 'nvda' must be provided.",
        )

    current = read_shares(DEFAULT_SHARES_FILE)

    new_voo = req.voo if req.voo is not None else current["VOO"]
    new_nvda = req.nvda if req.nvda is not None else current["NVDA"]

    write_shares(DEFAULT_SHARES_FILE, new_voo, new_nvda)

    restarted = None
    if req.restart:
        restarted = restart_service(SERVICE_NAME)
        if not restarted:
            raise HTTPException(
                status_code=500,
                detail="Shares updated but service restart failed.",
            )

    return SharesResponse(voo=new_voo, nvda=new_nvda, restarted=restarted)

def as_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def get_finance_daily():
    snap = load_robinhood_snapshot()
    positions = snap.get("positions", {})

    equity = as_float(snap.get("equity"))
    market_value = as_float(snap.get("market_value"))
    buying_power = as_float(snap.get("withdrawable_amount"))

    total_gain = 0.0
    rows = {}

    for symbol, p in positions.items():
        equity_change = as_float(p.get("equity_change"))
        intraday_pct = as_float(p.get("intraday_percent_change"))
        total_gain += equity_change

        rows[symbol] = {
            "price": as_float(p.get("price")),
            "equity": as_float(p.get("equity")),
            "equity_change": round(equity_change, 2),
            "intraday_percent_change": round(intraday_pct, 2),
            "percent_change": round(as_float(p.get("percent_change")), 2),
            "percentage": round(as_float(p.get("percentage")), 2),
        }

    pct = round((total_gain / max(equity - total_gain, 0.01)) * 100, 2)

    # ROTH-2B:
    # Preserve legacy taxable fields while exposing the account-aware
    # Robinhood snapshot added by the sync layer.
    accounts = snap.get("accounts") if isinstance(snap.get("accounts"), dict) else {}
    combined = snap.get("combined") if isinstance(snap.get("combined"), dict) else {}
    roth_config = snap.get("roth_config") if isinstance(snap.get("roth_config"), dict) else {}

    return {
        "equity": round(equity, 2),
        "market_value": round(market_value, 2),
        "buying_power": round(buying_power, 2),
        "daily_change": round(total_gain, 2),
        "daily_percent": pct,
        "positions": rows,
        "timestamp": snap.get("timestamp"),

        # New non-breaking account-aware fields.
        "accounts": accounts,
        "taxable": accounts.get("taxable", {}),
        "roth_ira": accounts.get("roth_ira", {}),
        "combined": combined,
        "roth_config": roth_config,
    }


def _cache_age_seconds(cache: dict) -> float:
    fetched_at = cache.get("fetched_at") or 0.0
    if not fetched_at:
        return 999999.0
    return time.monotonic() - float(fetched_at)


def _failure_age_seconds(cache: dict) -> float:
    failed_at = cache.get("failed_at") or 0.0
    if not failed_at:
        return 999999.0
    return time.monotonic() - float(failed_at)


def _cache_is_fresh(cache: dict, ttl_seconds: int) -> bool:
    return cache.get("payload") is not None and _cache_age_seconds(cache) < ttl_seconds


def _decorate_cache_payload(payload: dict, cache: dict, source: str) -> dict:
    out = dict(payload or {})
    out["cache_source"] = source
    out["cache_age_seconds"] = round(_cache_age_seconds(cache), 1)
    out["stale"] = source == "stale_cache"
    return out


def _parse_nws_temperature(period: dict):
    val = period.get("temperature")
    try:
        return float(val) if val is not None else None
    except Exception:
        return None


def _parse_nws_wind_mph(period: dict):
    text = str(period.get("windSpeed") or "")
    nums = [int(x) for x in re.findall(r"\d+", text)]
    if not nums:
        return None
    return max(nums)


def _parse_nws_precip_probability(period: dict):
    pop = period.get("probabilityOfPrecipitation") or {}
    val = pop.get("value")
    try:
        return float(val) if val is not None else 0.0
    except Exception:
        return 0.0


def _estimate_nws_precipitation(period: dict):
    # NWS hourly forecast does not provide measured current precip like Open-Meteo.
    # Use 0.0 unless shortForecast strongly indicates active precip.
    short = str(period.get("shortForecast") or "").lower()
    if any(word in short for word in ["rain", "shower", "thunderstorm", "snow", "sleet", "drizzle"]):
        return 0.01
    return 0.0


def get_nws_weather_fallback():
    lat = 42.3298
    lon = -75.1987
    headers = {
        "User-Agent": "ap3xservers-wall-ui/1.0 (ap3xservers.com)",
        "Accept": "application/geo+json",
    }

    try:
        points = requests.get(
            f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}",
            timeout=6,
            headers=headers,
        )
        points.raise_for_status()
        props = points.json().get("properties", {})
        hourly_url = props.get("forecastHourly")
        daily_url = props.get("forecast")

        if not hourly_url:
            raise RuntimeError("NWS points response missing forecastHourly")

        hourly = requests.get(hourly_url, timeout=8, headers=headers)
        hourly.raise_for_status()
        hourly_periods = hourly.json().get("properties", {}).get("periods", [])
        if not hourly_periods:
            raise RuntimeError("NWS hourly forecast returned no periods")

        current = hourly_periods[0]
        temp = _parse_nws_temperature(current)
        wind = _parse_nws_wind_mph(current)
        rain_chance = _parse_nws_precip_probability(current)
        precipitation = _estimate_nws_precipitation(current)

        high = None
        low = None
        if daily_url:
            try:
                daily = requests.get(daily_url, timeout=8, headers=headers)
                daily.raise_for_status()
                daily_periods = daily.json().get("properties", {}).get("periods", [])
                temps = []
                for period in daily_periods[:4]:
                    t = _parse_nws_temperature(period)
                    if t is not None:
                        temps.append(t)
                if temps:
                    high = max(temps)
                    low = min(temps)
            except Exception as daily_exc:
                log.warning("NWS daily fallback partial failure: %s", daily_exc)

        return {
            "temperature": temp,
            "temp": temp,
            "feels_like": temp,
            "humidity": None,
            "wind_mph": wind,
            "gust_mph": wind,
            "precipitation": precipitation,
            "weather_code": None,
            "short_forecast": current.get("shortForecast"),
            "detailed_forecast": current.get("detailedForecast"),
            "high": high,
            "low": low,
            "rain_chance": rain_chance,
            "updated_at": datetime.now(ZoneInfo("America/New_York")).isoformat(timespec="seconds"),
            "provider": "nws_fallback",
        }

    except Exception as exc:
        log.error("NWS weather fallback failed: %s", exc)
        return {
            "error": "nws_weather_fallback_unavailable",
            "last_error": str(exc),
            "provider": "nws_fallback",
        }



def build_precip_timeline(hourly: dict, now_dt=None) -> dict:
    """
    Phase 9D:
    Build near-term precipitation timing intelligence from Open-Meteo hourly arrays.

    Returns:
    - time until next likely precipitation
    - time since last observed/likely precipitation
    - peak precipitation probability in the next 12 hours
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("America/New_York")
    if now_dt is None:
        now_dt = datetime.now(tz)

    times = hourly.get("time") or []
    probs = hourly.get("precipitation_probability") or []
    precips = hourly.get("precipitation") or []
    codes = hourly.get("weather_code") or []

    rows = []
    for i, raw_time in enumerate(times):
        try:
            dt = datetime.fromisoformat(str(raw_time))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=tz)
        except Exception:
            continue

        prob = probs[i] if i < len(probs) else None
        precip = precips[i] if i < len(precips) else None
        code = codes[i] if i < len(codes) else None

        try:
            prob_f = float(prob) if prob is not None else 0.0
        except Exception:
            prob_f = 0.0

        try:
            precip_f = float(precip) if precip is not None else 0.0
        except Exception:
            precip_f = 0.0

        try:
            code_i = int(code) if code is not None else None
        except Exception:
            code_i = None

        # Avoid treating tiny/noisy trace values as meaningful rain.
        # Count precipitation as likely if measurable precip is forecast
        # or probability is independently high.
        likely_precip = precip_f >= 0.02 or prob_f >= 55
        storm_like = code_i in {95, 96, 99}

        rows.append({
            "time": dt,
            "probability": prob_f,
            "precipitation": precip_f,
            "weather_code": code_i,
            "likely_precip": likely_precip,
            "storm_like": storm_like,
        })

    past = [r for r in rows if r["time"] <= now_dt]
    future = [r for r in rows if r["time"] > now_dt]

    next_precip = next((r for r in future if r["likely_precip"]), None)
    next_storm = next((r for r in future if r["storm_like"]), None)
    last_precip = next((r for r in reversed(past) if r["likely_precip"]), None)

    next_12h = [
        r for r in future
        if 0 <= (r["time"] - now_dt).total_seconds() <= 12 * 3600
    ]

    peak_probability_next_12h = max(
        [r["probability"] for r in next_12h],
        default=None,
    )

    peak_precip_next_12h = max(
        [r["precipitation"] for r in next_12h],
        default=None,
    )

    def minutes_until(row):
        if not row:
            return None
        return int(round((row["time"] - now_dt).total_seconds() / 60))

    def minutes_since(row):
        if not row:
            return None
        return int(round((now_dt - row["time"]).total_seconds() / 60))

    if next_storm:
        status = "storm_upcoming"
    elif next_precip:
        status = "precip_upcoming"
    elif last_precip:
        status = "recent_precip"
    else:
        status = "clear"

    return {
        "status": status,
        "next_precip_at": next_precip["time"].isoformat(timespec="seconds") if next_precip else None,
        "minutes_until_precip": minutes_until(next_precip),
        "next_precip_probability": next_precip["probability"] if next_precip else None,
        "next_precip_amount": next_precip["precipitation"] if next_precip else None,
        "next_storm_at": next_storm["time"].isoformat(timespec="seconds") if next_storm else None,
        "minutes_until_storm": minutes_until(next_storm),
        "last_precip_at": last_precip["time"].isoformat(timespec="seconds") if last_precip else None,
        "minutes_since_precip": minutes_since(last_precip),
        "last_precip_probability": last_precip["probability"] if last_precip else None,
        "last_precip_amount": last_precip["precipitation"] if last_precip else None,
        "peak_probability_next_12h": peak_probability_next_12h,
        "peak_precip_next_12h": peak_precip_next_12h,
    }


def format_minutes_phrase(minutes: int | None, future: bool = True) -> str:
    """
    Convert minutes into compact operator text.
    """
    if minutes is None:
        return "unknown timing"

    try:
        minutes = int(minutes)
    except Exception:
        return "unknown timing"

    if minutes < 0:
        minutes = abs(minutes)

    hours = minutes // 60
    mins = minutes % 60

    if hours <= 0:
        phrase = f"{mins}m"
    elif mins == 0:
        phrase = f"{hours}h"
    else:
        phrase = f"{hours}h {mins}m"

    return f"in {phrase}" if future else f"{phrase} ago"

def get_weather(force_refresh: bool = False):
    # Franklin / Oneonta NY Geo Location
    lat = 42.3298
    lon = -75.1987

    if not force_refresh and _cache_is_fresh(_weather_cache, WEATHER_CACHE_TTL_SECONDS):
        return _decorate_cache_payload(_weather_cache["payload"], _weather_cache, "memory_cache")

    if (
        not force_refresh
        and _weather_cache.get("payload") is not None
        and _failure_age_seconds(_weather_cache) < WEATHER_FAILURE_TTL_SECONDS
    ):
        return _decorate_cache_payload(_weather_cache["payload"], _weather_cache, "stale_cache")

    # Cold-start protection: if upstream failed recently and we have no good payload yet,
    # return the cached failure instead of hammering Open-Meteo every dashboard refresh.
    if (
        not force_refresh
        and _weather_cache.get("payload") is None
        and _failure_age_seconds(_weather_cache) < WEATHER_FAILURE_TTL_SECONDS
    ):
        return {
            "error": "weather_unavailable",
            "last_error": _weather_cache.get("last_error"),
            "cache_source": "recent_failure",
            "cache_age_seconds": None,
            "failure_age_seconds": round(_failure_age_seconds(_weather_cache), 1),
            "stale": True,
        }

    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,relative_humidity_2m,apparent_temperature,"
        "precipitation,weather_code,wind_speed_10m,wind_gusts_10m"
        "&hourly=precipitation_probability,precipitation,weather_code"
        "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
        "&temperature_unit=fahrenheit"
        "&wind_speed_unit=mph"
        "&timezone=America%2FNew_York"
        "&past_days=1"
        "&forecast_days=2"
    )

    try:
        r = requests.get(
            url,
            timeout=5,
            headers={"User-Agent": "ap3xservers-wall-ui/1.0"},
        )
        r.raise_for_status()
        data = r.json()
        current = data.get("current", {})
        daily = data.get("daily", {})
        hourly = data.get("hourly", {})

        temp = current.get("temperature_2m")
        precip_timeline = build_precip_timeline(hourly)

        payload = {
            "provider": "open_meteo",
            "temperature": temp,
            "temp": temp,
            "feels_like": current.get("apparent_temperature"),
            "humidity": current.get("relative_humidity_2m"),
            "wind_mph": current.get("wind_speed_10m"),
            "gust_mph": current.get("wind_gusts_10m"),
            "precipitation": current.get("precipitation"),
            "weather_code": current.get("weather_code"),
            "high": (daily.get("temperature_2m_max") or [None])[0],
            "low": (daily.get("temperature_2m_min") or [None])[0],
            "rain_chance": (daily.get("precipitation_probability_max") or [None])[0],
            "precip_timeline": precip_timeline,
            "updated_at": datetime.now(ZoneInfo("America/New_York")).isoformat(timespec="seconds"),
        }

        _weather_cache["payload"] = payload
        _weather_cache["fetched_at"] = time.monotonic()
        _weather_cache["last_error"] = None

        return _decorate_cache_payload(payload, _weather_cache, "live")

    except Exception as exc:
        _weather_cache["failed_at"] = time.monotonic()
        _weather_cache["last_error"] = str(exc)
        log.error("Weather fetch failed: %s", exc)

        fallback = get_nws_weather_fallback()
        if isinstance(fallback, dict) and not fallback.get("error"):
            fallback["primary_error"] = str(exc)
            _weather_cache["payload"] = fallback
            _weather_cache["fetched_at"] = time.monotonic()
            _weather_cache["last_error"] = None
            return _decorate_cache_payload(fallback, _weather_cache, "nws_fallback")

        if _weather_cache.get("payload") is not None:
            payload = _decorate_cache_payload(_weather_cache["payload"], _weather_cache, "stale_cache")
            payload["warning"] = "weather_provider_unavailable_using_stale_cache"
            payload["last_error"] = str(exc)
            payload["fallback_error"] = fallback.get("last_error") if isinstance(fallback, dict) else None
            return payload

        return {
            "error": "weather_unavailable",
            "last_error": str(exc),
            "fallback_error": fallback.get("last_error") if isinstance(fallback, dict) else None,
            "cache_source": "none",
            "cache_age_seconds": None,
            "stale": True,
        }



def build_weather_intelligence(weather_payload: dict) -> dict:
    """
    Phase 9B:
    Convert raw /api/weather payload into Alpha-readable weather source intelligence.
    This does not create events. It only describes provider/source posture.
    """
    w = weather_payload if isinstance(weather_payload, dict) else {}

    provider = w.get("provider") or "open_meteo"
    cache_source = w.get("cache_source") or "unknown"
    stale = bool(w.get("stale"))
    error = w.get("error")
    primary_error = w.get("primary_error")
    last_error = w.get("last_error")
    fallback_error = w.get("fallback_error")

    usable = error is None and w.get("temperature") is not None

    if usable and cache_source == "live":
        posture = "LIVE"
        source_label = "Open-Meteo"
    elif usable and (provider == "nws_fallback" or cache_source == "nws_fallback"):
        posture = "FALLBACK"
        source_label = "NWS"
    elif usable and cache_source == "memory_cache":
        if provider == "nws_fallback":
            posture = "FALLBACK"
            source_label = "NWS"
        elif provider == "open_meteo":
            posture = "LIVE"
            source_label = "Open-Meteo cache"
        else:
            posture = "LIVE"
            source_label = "Recent cache"
    elif usable and cache_source == "stale_cache":
        posture = "STALE"
        source_label = "Stale cache"
    elif cache_source == "recent_failure":
        posture = "DEGRADED"
        source_label = "Provider unavailable"
    else:
        posture = "OFFLINE"
        source_label = "No weather source"

    temp = w.get("temperature")
    wind = w.get("wind_mph")
    short = w.get("short_forecast") or "No forecast text"

    if posture in ("LIVE", "FALLBACK", "CACHED", "STALE") and temp is not None:
        summary = f"Weather {posture} via {source_label}: {short}, {temp:.0f}°F"
        if wind is not None:
            summary += f", wind {wind}mph"
        summary += "."
    elif posture == "DEGRADED":
        age = w.get("failure_age_seconds")
        if age is not None:
            summary = f"Weather DEGRADED: provider unavailable, last failure {age}s ago."
        else:
            summary = "Weather DEGRADED: provider unavailable."
    else:
        summary = "Weather OFFLINE: no usable provider or cache."

    return {
        "posture": posture,
        "provider": provider,
        "source_label": source_label,
        "cache_source": cache_source,
        "cache_age_seconds": w.get("cache_age_seconds"),
        "failure_age_seconds": w.get("failure_age_seconds"),
        "stale": stale,
        "temperature": temp,
        "feels_like": w.get("feels_like"),
        "wind_mph": wind,
        "gust_mph": w.get("gust_mph"),
        "precipitation": w.get("precipitation"),
        "rain_chance": w.get("rain_chance"),
        "precip_timeline": w.get("precip_timeline"),
        "short_forecast": w.get("short_forecast"),
        "detailed_forecast": w.get("detailed_forecast"),
        "updated_at": w.get("updated_at"),
        "summary": summary,
        "usable": usable,
        "primary_error": primary_error,
        "last_error": last_error,
        "fallback_error": fallback_error,
        "error": error,
    }


def update_weather_intelligence_state(weather_intel: dict) -> None:
    """
    Phase 9B:
    Persist weather intelligence into current_state.json for Alpha brief/overview.
    """
    try:
        from ops_state import load_state, write_state, default_current_state
        current = load_state("current_state.json", default_current_state())
        current["weather"] = weather_intel if isinstance(weather_intel, dict) else {}
        write_state("current_state.json", current)
    except Exception as exc:
        log.error("Weather intelligence state update failed: %s", exc)

# ===== PHASE 2B-WX WEATHER ALERT TIMING HELPERS =====
def _wx_alert_now():
    from datetime import datetime as _dt_datetime
    from zoneinfo import ZoneInfo as _ZoneInfo
    return _dt_datetime.now(_ZoneInfo("America/New_York"))


def _wx_alert_iso_parse(value):
    if not value:
        return None
    try:
        from zoneinfo import ZoneInfo as _ZoneInfo
        from datetime import datetime as _dt_datetime
        return _dt_datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(_ZoneInfo("America/New_York"))
    except Exception:
        return None


def _wx_alert_iso(dt):
    if not dt:
        return None
    try:
        return dt.isoformat(timespec="seconds")
    except Exception:
        return None


def _wx_next_weekday(base, weekday_name):
    from datetime import timedelta as _dt_timedelta
    days = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }
    wanted = days.get(str(weekday_name or "").lower())
    if wanted is None:
        return base.date()
    delta = (wanted - base.weekday()) % 7
    return (base + _dt_timedelta(days=delta)).date()


def _wx_parse_time_phrase(phrase, base):
    import re as _re
    from datetime import datetime as _dt_datetime, time as _dt_time, timedelta as _dt_timedelta
    from zoneinfo import ZoneInfo as _ZoneInfo

    if not phrase:
        return None

    raw = " ".join(str(phrase).replace(".", "").split())
    lower = raw.lower()

    if "midnight" in lower:
        hour, minute = 0, 0
    elif "noon" in lower:
        hour, minute = 12, 0
    else:
        m = _re.search(r'\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b', lower)
        if not m:
            return None
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        ampm = m.group(3)
        if ampm == "pm" and hour != 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0

    date_obj = base.date()

    if "tonight" in lower:
        # For evening NWS alerts, "midnight tonight" means next calendar day at 00:00.
        if base.hour >= 12:
            date_obj = (base + _dt_timedelta(days=1)).date()
    elif "tomorrow" in lower:
        date_obj = (base + _dt_timedelta(days=1)).date()
    else:
        wd = _re.search(r'\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b', lower)
        if wd:
            date_obj = _wx_next_weekday(base, wd.group(1))

    return _dt_datetime.combine(date_obj, _dt_time(hour=hour, minute=minute), tzinfo=_ZoneInfo("America/New_York"))


def _wx_extract_when_text(alert):
    import re as _re

    blob = "\n".join(
        str(alert.get(k) or "")
        for k in ("description", "headline", "instruction")
        if alert.get(k)
    )

    m = _re.search(
        r'\*\s*WHEN\.\.\.(.*?)(?:\n\s*\n|\*\s*[A-Z]+\.\.\.|$)',
        blob,
        flags=_re.I | _re.S,
    )
    if m:
        return " ".join(m.group(1).split())

    headline = str(alert.get("headline") or "")
    if "until" in headline.lower():
        return headline

    return blob


def _wx_normalize_single_alert(alert):
    import re as _re
    from datetime import datetime as _dt_datetime
    from zoneinfo import ZoneInfo as _ZoneInfo

    now = _wx_alert_now()
    when_text = _wx_extract_when_text(alert)

    effective = _wx_alert_iso_parse(alert.get("effective"))
    expires = _wx_alert_iso_parse(alert.get("expires"))

    starts_at = None
    ends_at = None

    # Example: "From midnight tonight to 9 AM EDT Tuesday."
    m = _re.search(r'\bfrom\s+(.+?)\s+to\s+(.+?)(?:$|\.|\n)', when_text, flags=_re.I)
    if m:
        starts_at = _wx_parse_time_phrase(m.group(1), now)
        ends_at = _wx_parse_time_phrase(m.group(2), now)

    # Fallback from headline: "until May 12 at 9:00AM EDT"
    if not ends_at:
        headline = str(alert.get("headline") or "")
        m2 = _re.search(
            r'until\s+([A-Za-z]+\s+\d{1,2})\s+at\s+(\d{1,2})(?::(\d{2}))?\s*(AM|PM)',
            headline,
            flags=_re.I,
        )
        if m2:
            month_day = m2.group(1)
            hour = int(m2.group(2))
            minute = int(m2.group(3) or 0)
            ampm = m2.group(4).lower()
            if ampm == "pm" and hour != 12:
                hour += 12
            if ampm == "am" and hour == 12:
                hour = 0
            try:
                parsed = _dt_datetime.strptime(f"{now.year} {month_day}", "%Y %B %d")
                ends_at = _dt_datetime(
                    now.year,
                    parsed.month,
                    parsed.day,
                    hour,
                    minute,
                    tzinfo=_ZoneInfo("America/New_York"),
                )
            except Exception:
                pass

    if not starts_at:
        starts_at = effective

    # NWS expires can be product expiration, not hazard end. Use it only as fallback.
    if not ends_at:
        ends_at = expires

    if starts_at and ends_at and ends_at <= starts_at:
        from datetime import timedelta as _dt_timedelta
        ends_at = ends_at + _dt_timedelta(days=1)

    status = "unknown"
    if starts_at and ends_at:
        if now < starts_at:
            status = "upcoming"
        elif starts_at <= now <= ends_at:
            status = "active"
        else:
            status = "expired"
    elif ends_at:
        status = "active" if now <= ends_at else "expired"
    elif starts_at:
        status = "upcoming" if now < starts_at else "active"

    def mins_until(dt):
        if not dt:
            return None
        return int(round((dt - now).total_seconds() / 60))

    return {
        "when_text": when_text,
        "starts_at": _wx_alert_iso(starts_at),
        "ends_at": _wx_alert_iso(ends_at),
        "effective": _wx_alert_iso(effective),
        "expires": _wx_alert_iso(expires),
        "status": status,
        "minutes_until_start": mins_until(starts_at),
        "minutes_until_end": mins_until(ends_at),
    }


def _wx_severity_rank(severity):
    return {
        "minor": 1,
        "moderate": 2,
        "severe": 3,
        "extreme": 4,
    }.get(str(severity or "").lower(), 0)


def _wx_minutes_label(mins):
    if mins is None:
        return "unknown"
    try:
        mins = int(mins)
    except Exception:
        return "unknown"
    suffix = " ago" if mins < 0 else ""
    mins = abs(mins)
    h, m = divmod(mins, 60)
    if h and m:
        return f"{h}h {m}m{suffix}"
    if h:
        return f"{h}h{suffix}"
    return f"{m}m{suffix}"


def _wx_alerts_summary(alerts_payload):
    raw_alerts = []
    if isinstance(alerts_payload, dict):
        raw_alerts = alerts_payload.get("alerts") or []
    if not isinstance(raw_alerts, list):
        raw_alerts = []

    items = []
    for alert in raw_alerts:
        if not isinstance(alert, dict):
            continue
        item = dict(alert)
        if "status" not in item or "starts_at" not in item or "ends_at" not in item:
            item.update(_wx_normalize_single_alert(item))
        items.append(item)

    def sort_key(item):
        status_order = {"active": 0, "upcoming": 1, "unknown": 2, "expired": 3}
        return (
            status_order.get(item.get("status"), 2),
            -_wx_severity_rank(item.get("severity")),
            item.get("minutes_until_start") if item.get("minutes_until_start") is not None else 999999,
        )

    items.sort(key=sort_key)

    active = sum(1 for x in items if x.get("status") == "active")
    upcoming = sum(1 for x in items if x.get("status") == "upcoming")
    expired = sum(1 for x in items if x.get("status") == "expired")

    highest = None
    for item in items:
        sev = item.get("severity")
        if highest is None or _wx_severity_rank(sev) > _wx_severity_rank(highest):
            highest = sev

    return {
        "count": len(items),
        "active": active,
        "upcoming": upcoming,
        "expired": expired,
        "highest_severity": highest,
        "primary": items[0] if items else None,
        "items": items,
        "updated_at": alerts_payload.get("updated_at") if isinstance(alerts_payload, dict) else None,
        "cache_source": alerts_payload.get("cache_source") if isinstance(alerts_payload, dict) else None,
        "cache_age_seconds": alerts_payload.get("cache_age_seconds") if isinstance(alerts_payload, dict) else None,
        "stale": alerts_payload.get("stale") if isinstance(alerts_payload, dict) else None,
    }


def _wx_alert_summary_text(alerts_summary):
    if not isinstance(alerts_summary, dict):
        return None
    primary = alerts_summary.get("primary")
    if not primary:
        return None

    event = primary.get("event") or "Weather Alert"
    severity = primary.get("severity") or "Unknown"
    status = primary.get("status") or "unknown"

    if status == "upcoming":
        timing = "starts in " + _wx_minutes_label(primary.get("minutes_until_start"))
    elif status == "active":
        timing = "ends in " + _wx_minutes_label(primary.get("minutes_until_end"))
    elif status == "expired":
        timing = "expired"
    else:
        timing = "timing unknown"

    return f"{event} ({severity}) is {status}; {timing}."
# ===== END PHASE 2B-WX WEATHER ALERT TIMING HELPERS =====


def get_weather_alerts(force_refresh: bool = False):
    lat = 42.3298
    lon = -75.1987

    if not force_refresh and _cache_is_fresh(_weather_alerts_cache, WEATHER_ALERTS_CACHE_TTL_SECONDS):
        return _decorate_cache_payload(_weather_alerts_cache["payload"], _weather_alerts_cache, "memory_cache")

    if (
        not force_refresh
        and _weather_alerts_cache.get("payload") is not None
        and _failure_age_seconds(_weather_alerts_cache) < WEATHER_FAILURE_TTL_SECONDS
    ):
        return _decorate_cache_payload(_weather_alerts_cache["payload"], _weather_alerts_cache, "stale_cache")

    # Cold-start protection for NWS alerts too.
    if (
        not force_refresh
        and _weather_alerts_cache.get("payload") is None
        and _failure_age_seconds(_weather_alerts_cache) < WEATHER_FAILURE_TTL_SECONDS
    ):
        return {
            "count": 0,
            "alerts": [],
            "error": "alerts_unavailable",
            "last_error": _weather_alerts_cache.get("last_error"),
            "cache_source": "recent_failure",
            "cache_age_seconds": None,
            "failure_age_seconds": round(_failure_age_seconds(_weather_alerts_cache), 1),
            "stale": True,
        }

    try:
        r = requests.get(
            f"https://api.weather.gov/alerts/active?point={lat},{lon}",
            timeout=6,
            headers={
                "User-Agent": "ap3xservers-wall-ui/1.0",
                "Accept": "application/geo+json",
            },
        )
        r.raise_for_status()
        data = r.json()

        alerts = []
        for feature in data.get("features", []):
            props = feature.get("properties", {})
            alert = {
                "event": props.get("event"),
                "severity": props.get("severity"),
                "urgency": props.get("urgency"),
                "certainty": props.get("certainty"),
                "headline": props.get("headline"),
                "description": props.get("description"),
                "instruction": props.get("instruction"),
                "effective": props.get("effective"),
                "expires": props.get("expires"),
            }
            try:
                alert.update(_wx_normalize_single_alert(alert))
            except Exception as timing_exc:
                alert["timing_error"] = str(timing_exc)
            alerts.append(alert)

        payload = {
            "count": len(alerts),
            "alerts": alerts[:5],
            "updated_at": datetime.now(ZoneInfo("America/New_York")).isoformat(timespec="seconds"),
        }

        _weather_alerts_cache["payload"] = payload
        _weather_alerts_cache["fetched_at"] = time.monotonic()
        _weather_alerts_cache["last_error"] = None

        return _decorate_cache_payload(payload, _weather_alerts_cache, "live")

    except Exception as exc:
        _weather_alerts_cache["failed_at"] = time.monotonic()
        _weather_alerts_cache["last_error"] = str(exc)
        log.error("Weather alerts fetch failed: %s", exc)

        if _weather_alerts_cache.get("payload") is not None:
            payload = _decorate_cache_payload(_weather_alerts_cache["payload"], _weather_alerts_cache, "stale_cache")
            payload["warning"] = "weather_alerts_provider_unavailable_using_stale_cache"
            payload["last_error"] = str(exc)
            return payload

        return {
            "count": 0,
            "alerts": [],
            "error": "alerts_unavailable",
            "last_error": str(exc),
            "cache_source": "none",
            "cache_age_seconds": None,
            "stale": True,
        }


def check_http_detailed(url: str):
    start = time.monotonic()
    try:
        r = requests.get(url, timeout=5, allow_redirects=True, verify=False)
        elapsed_ms = round((time.monotonic() - start) * 1000)

        if r.status_code < 500:
            state = "online"
        else:
            state = "degraded"

        return {
            "state": state,
            "status_code": r.status_code,
            "latency_ms": elapsed_ms,
        }
    except Exception:
        return {
            "state": "offline",
            "status_code": None,
            "latency_ms": None,
        }


def get_external_health():
    """
    PASS ALPHA-A:
    Cached external health probe.

    External Cloudflare/HTTPS checks are intentionally slower than local state reads.
    Do not re-probe on every wall/operator poll; serve a short-lived cache instead.
    """
    ttl_seconds = 90.0
    now = time.monotonic()

    cache = globals().setdefault(
        "_EXTERNAL_HEALTH_CACHE",
        {
            "payload": None,
            "fetched_at": 0.0,
            "ttl_seconds": ttl_seconds,
        },
    )

    payload = cache.get("payload")
    fetched_at = float(cache.get("fetched_at") or 0.0)
    age = now - fetched_at if fetched_at else 999999.0

    if isinstance(payload, dict) and age < ttl_seconds:
        cached = {
            key: (value.copy() if isinstance(value, dict) else value)
            for key, value in payload.items()
        }
        cached["_cache"] = {
            "enabled": True,
            "hit": True,
            "age_seconds": round(age, 2),
            "ttl_seconds": ttl_seconds,
        }
        return cached

    targets = {
        "dashboard": "https://dashboard.ap3xservers.com",
        "status": "https://status.ap3xservers.com/status",
        "tracker": "https://tracker-ui.ap3xservers.com",
    }

    fresh = {
        name: check_http_detailed(url)
        for name, url in targets.items()
    }

    cache["payload"] = fresh
    cache["fetched_at"] = now
    cache["ttl_seconds"] = ttl_seconds

    returned = {
        key: (value.copy() if isinstance(value, dict) else value)
        for key, value in fresh.items()
    }
    returned["_cache"] = {
        "enabled": True,
        "hit": False,
        "age_seconds": 0.0,
        "ttl_seconds": ttl_seconds,
    }
    return returned

@app.get("/backups")
@app.get("/api/backups")
def backups():
    return get_backup_status()


@app.get("/games")
@app.get("/api/games")
def games():
    """
    ALPHA-G19A:
    Cached game telemetry.

    Serial offline port checks can take ~4s. Serve last-known-good immediately
    and refresh in the background when stale.
    """
    ttl_seconds = float(_ALPHA_GAMES_CACHE.get("ttl_seconds") or 45.0)
    now = time.monotonic()
    fetched_at = float(_ALPHA_GAMES_CACHE.get("fetched_at") or 0.0)
    age = now - fetched_at if fetched_at else 999999.0
    payload = _ALPHA_GAMES_CACHE.get("payload")

    if isinstance(payload, dict) and age < ttl_seconds:
        return _alpha_return_games_cached(age, stale=False, refreshing=False)

    if isinstance(payload, dict):
        refreshing = _alpha_spawn_refresh("games", lambda: _refresh_games_cache_g19a(return_payload=False))
        return _alpha_return_games_cached(age, stale=True, refreshing=refreshing)

    return _refresh_games_cache_g19a(return_payload=True)


@app.get("/threat")
@app.get("/api/threat")
def threat():
    """
    ALPHA-G19B:
    Cached threat telemetry.

    /threat is wall-polled and still costs ~1s after G19A. Serve
    last-known-good immediately and refresh in background when stale.
    """
    ttl_seconds = float(_ALPHA_THREAT_CACHE.get("ttl_seconds") or 20.0)
    now = time.monotonic()
    fetched_at = float(_ALPHA_THREAT_CACHE.get("fetched_at") or 0.0)
    age = now - fetched_at if fetched_at else 999999.0
    payload = _ALPHA_THREAT_CACHE.get("payload")

    if isinstance(payload, dict) and age < ttl_seconds:
        return _alpha_return_threat_cached(age, stale=False, refreshing=False)

    if isinstance(payload, dict):
        refreshing = _alpha_spawn_refresh("threat", lambda: _refresh_threat_cache_g19b(return_payload=False))
        return _alpha_return_threat_cached(age, stale=True, refreshing=refreshing)

    return _refresh_threat_cache_g19b(return_payload=True)

@app.get("/finance/daily")
@app.get("/api/finance/daily")
def finance_daily():
    return get_finance_daily()





@app.get("/api/operator/daily-report")
@app.get("/operator/daily-report")
def operator_daily_report_status():
    report_dir = Path("/home/ap3x/scripts/rh_tracker/state/daily_reports")
    backup_dir = Path("/home/ap3x/scripts/rh_tracker/state/priority_alpha_backups")
    agent_state_path = Path("/home/ap3x/scripts/rh_tracker/state/operator_agent_state.json")

    def latest_file(pattern):
        files = sorted(glob.glob(str(pattern)), key=lambda p: Path(p).stat().st_mtime, reverse=True)
        if not files:
            return None
        p = Path(files[0])
        return {
            "path": str(p),
            "name": p.name,
            "size_bytes": p.stat().st_size,
            "mtime": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
        }

    latest_json = latest_file(report_dir / "priority-alpha-daily-*.json")
    latest_md = latest_file(report_dir / "priority-alpha-daily-*.md")
    latest_backup = latest_file(backup_dir / "priority-alpha-full-*.tar.gz")

    report_payload = {}
    if latest_json:
        try:
            report_payload = json.loads(Path(latest_json["path"]).read_text())
        except Exception as e:
            report_payload = {"parse_error": str(e)}

    agent_payload = {}
    if agent_state_path.exists():
        try:
            agent_payload = json.loads(agent_state_path.read_text())
        except Exception as e:
            agent_payload = {"parse_error": str(e)}

    discord = (report_payload.get("agent", {}) or {}).get("discord", {}) if isinstance(report_payload, dict) else {}
    if not discord:
        discord = agent_payload.get("discord", {}) if isinstance(agent_payload, dict) else {}

    incident_memory = agent_payload.get("incident_memory", {}) if isinstance(agent_payload, dict) else {}
    incidents = incident_memory.get("incidents", {}) if isinstance(incident_memory, dict) else {}

    return {
        "available": bool(latest_json or latest_md),
        "latest_json": latest_json,
        "latest_markdown": latest_md,
        "latest_backup": latest_backup,
        "report": {
            "created_at": report_payload.get("created_at"),
            "alpha": report_payload.get("priority_alpha", {}),
            "agent": report_payload.get("agent", {}),
            "weather": report_payload.get("weather", {}),
            "finance": report_payload.get("finance", {}),
        },
        "discord": {
            "enabled": discord.get("enabled"),
            "last_success_at": discord.get("last_success_at"),
            "last_failure_at": discord.get("last_failure_at"),
            "last_error": discord.get("last_error"),
            "last_event_sent": discord.get("last_event_sent"),
        },
        "incidents": {
            "count": len(incidents),
            "items": incidents,
        },
        "schedule": {
            "timer": "priority-alpha-daily-report.timer",
            "daily_time": "08:30",
            "timezone": "America/New_York",
        },
    }

@app.get("/api/operator/agent")
@app.get("/operator/agent")
def operator_agent_status():
    path = Path("/home/ap3x/scripts/rh_tracker/state/operator_agent_state.json")
    if not path.exists():
        return {
            "available": False,
            "overall": "UNKNOWN",
            "summary": "Operator agent state file not found.",
            "recommendations": [],
            "discord": {
                "enabled": False,
                "last_success_at": None,
                "last_failure_at": None,
                "last_error": "state file missing"
            },
            "last_action": {
                "action": "unknown",
                "result": "state file missing",
                "timestamp": None
            }
        }

    try:
        data = json.loads(path.read_text())
        data["available"] = True
        return data
    except Exception as e:
        return {
            "available": False,
            "overall": "ERROR",
            "summary": f"Unable to parse operator agent state: {e}",
            "recommendations": [],
            "discord": {
                "enabled": False,
                "last_success_at": None,
                "last_failure_at": None,
                "last_error": str(e)
            },
            "last_action": {
                "action": "unknown",
                "result": "state parse failed",
                "timestamp": None
            }
        }

@app.get("/weather")
@app.get("/api/weather")
def weather():
    weather_payload = get_weather()
    try:
        weather_intel = build_weather_intelligence(weather_payload)
        if isinstance(weather_payload, dict):
            weather_payload["source_label"] = weather_intel.get("source_label")
            weather_payload["posture"] = weather_intel.get("posture")
            weather_payload["usable"] = weather_intel.get("usable")
    except Exception:
        pass
    return weather_payload

@app.get("/weather/alerts")
@app.get("/api/weather/alerts")
def weather_alerts():
    return get_weather_alerts()


@app.get("/external")
@app.get("/api/external")
def external_health():
    return get_external_health()

@app.get("/portfolio/value", response_model=PortfolioValueResponse)
def portfolio_value():
    shares_data = read_shares(DEFAULT_SHARES_FILE)

    voo_shares = shares_data["VOO"]
    nvda_shares = shares_data["NVDA"]

    voo_price = fetch_price("VOO")
    nvda_price = fetch_price("NVDA")

    if voo_price is None or nvda_price is None:
        raise HTTPException(
            status_code=503,
            detail="Could not fetch live market prices.",
        )

    voo_value = round(voo_shares * voo_price, 2)
    nvda_value = round(nvda_shares * nvda_price, 2)
    total = round(voo_value + nvda_value, 2)

    return PortfolioValueResponse(
        voo_shares=voo_shares,
        nvda_shares=nvda_shares,
        voo_price=voo_price,
        nvda_price=nvda_price,
        voo_value=voo_value,
        nvda_value=nvda_value,
        total_value=total,
        updated_at=datetime.now().isoformat(timespec="seconds"),
    )


@app.get("/robinhood/status")
def robinhood_status():
    return load_robinhood_snapshot()

def refresh_ops_state():
    """
    Priority Alpha Phase 1 helper:
    Refresh live operational state from existing API engines.

    This keeps Alpha state aligned with:
    - status telemetry
    - threat/weather engine
    - finance daily data
    - service availability
    - latency availability

    It intentionally does not perform Phase 2 trend analysis.
    """

    errors = []

    telemetry_ok = False
    weather_ok = False
    finance_ok = False
    latency_ok = False
    service_ok = False

    try:
        status_start = time.perf_counter()
        status_payload = status()
        status_latency_ms = (time.perf_counter() - status_start) * 1000

        update_latency_state_from_measurement(
            "status_engine",
            status_latency_ms,
            source="internal"
        )

        update_service_state_from_status_payload(status_payload)
        update_resource_state_from_status_payload(status_payload)

        telemetry_ok = isinstance(status_payload, dict) and len(status_payload) > 0
        service_ok = telemetry_ok
        latency_ok = telemetry_ok
    except Exception as e:
        errors.append(f"status unavailable: {type(e).__name__}: {e}")

    try:
        weather_payload = get_weather()
        weather_intel = build_weather_intelligence(weather_payload)
        update_weather_intelligence_state(weather_intel)
        weather_ok = bool(weather_intel.get("usable")) and weather_intel.get("posture") not in ("DEGRADED", "OFFLINE")

    except Exception as e:
        errors.append(f"weather unavailable: {type(e).__name__}: {e}")

    try:
        threat_payload = threat()

        if isinstance(threat_payload, dict) and "level" in threat_payload:
            update_threat_state_from_payload(threat_payload)

    except Exception as e:
        errors.append(f"threat unavailable: {type(e).__name__}: {e}")

    try:
        finance_payload = finance_daily()
        finance_ok = isinstance(finance_payload, dict) and len(finance_payload) > 0
    except Exception as e:
        errors.append(f"finance unavailable: {type(e).__name__}: {e}")

    update_data_quality(
        telemetry_available=telemetry_ok,
        weather_available=weather_ok,
        finance_available=finance_ok,
        latency_available=latency_ok,
        service_status_available=service_ok,
        errors=errors,
    )

    try:
        sync_data_quality_events_from_state()
    except Exception as e:
        errors.append(f"data_quality_event_sync_failed: {e}")

    try:
        sync_backup_events_from_payload(get_backup_status())
    except Exception as e:
        errors.append(f"backup_event_sync_failed: {e}")

    try:
        sync_service_events_from_state()
    except Exception as e:
        errors.append(f"service_event_sync_failed: {e}")

    try:
        sync_resource_events_from_state()
    except Exception as e:
        errors.append(f"resource_event_sync_failed: {e}")

    try:
        sync_latency_events_from_state()
    except Exception as e:
        errors.append(f"latency_event_sync_failed: {e}")

    sync_service_condition_into_current_state()
    sync_latency_condition_into_current_state()
    sync_resource_condition_into_current_state()

    return build_ops_summary()


@app.get("/api/ops/state")
def api_ops_state():
    return refresh_ops_state()

@app.get("/api/ops/history")
def api_ops_history():
    # Keep Alpha history aligned with live operational state before reporting memory stats.
    refresh_ops_state()
    return build_history_summary()

@app.get("/api/ops/trends")
def api_ops_trends():
    refresh_ops_state()
    return build_trend_summary()

@app.get("/api/ops/events")
def api_ops_events():
    refresh_ops_state()
    apply_event_lifecycle()
    return build_event_summary()


def _build_ops_feed_payload_g18b():
    refresh_ops_state()
    apply_event_lifecycle()
    feed = build_ops_feed()

    # PHASE 2B-WX: insert normalized weather alert item into tactical feed.
    try:
        alerts_payload = get_weather_alerts()
        alerts_summary = _wx_alerts_summary(alerts_payload)
        primary = alerts_summary.get("primary") if isinstance(alerts_summary, dict) else None

        if primary and primary.get("status") in ("active", "upcoming") and isinstance(feed, dict):
            event = primary.get("event") or "Weather Alert"
            severity = primary.get("severity") or "Unknown"
            status = primary.get("status") or "unknown"

            if status == "upcoming":
                timing = "starts in " + _wx_minutes_label(primary.get("minutes_until_start"))
            else:
                timing = "ends in " + _wx_minutes_label(primary.get("minutes_until_end"))

            summary = f"{event} ({severity}) is {status}; {timing}."

            item = {
                "priority": "WATCH" if str(severity).lower() in ("minor", "moderate") else "ALERT",
                "source": "weather",
                "title": event,
                "summary": summary,
                "status": status,
                "ttl_minutes": 90,
                "severity": severity,
                "starts_at": primary.get("starts_at"),
                "ends_at": primary.get("ends_at"),
                "minutes_until_start": primary.get("minutes_until_start"),
                "minutes_until_end": primary.get("minutes_until_end"),
                "instruction": primary.get("instruction"),
            }

            feed.setdefault("items", [])
            if isinstance(feed.get("items"), list):
                duplicate = any(
                    isinstance(x, dict)
                    and str(x.get("source", "")).lower() == "weather"
                    and x.get("title") == event
                    for x in feed["items"]
                )
                if not duplicate:
                    feed["items"].insert(0, item)

            feed.setdefault("counts", {})
            if isinstance(feed.get("counts"), dict):
                feed["counts"]["weather_alerts"] = alerts_summary.get("count", 0)
                feed["counts"]["weather_alerts_active"] = alerts_summary.get("active", 0)
                feed["counts"]["weather_alerts_upcoming"] = alerts_summary.get("upcoming", 0)
                feed["counts"]["total"] = len(feed.get("items", []))
                feed["counts"]["active"] = len([x for x in feed.get("items", []) if isinstance(x, dict) and x.get("status") != "recovered"])
                if item["priority"] == "WATCH":
                    feed["counts"]["watch"] = int(feed["counts"].get("watch", 0)) + 1
                elif item["priority"] == "ALERT":
                    feed["counts"]["alert"] = int(feed["counts"].get("alert", 0)) + 1

    except Exception as exc:
        if isinstance(feed, dict):
            feed["weather_alert_item_error"] = str(exc)

    return feed


@app.get("/api/ops/feed")
def api_ops_feed():
    """
    ALPHA-G18B:
    Stale-while-revalidate ONI tactical feed.

    If a previous payload exists, wall requests never block on expensive refresh.
    """
    ttl_seconds = 12.0
    now = time.monotonic()

    cache = globals().setdefault(
        "_OPS_FEED_CACHE",
        {
            "payload": None,
            "fetched_at": 0.0,
            "ttl_seconds": ttl_seconds,
        },
    )

    payload = cache.get("payload")
    fetched_at = float(cache.get("fetched_at") or 0.0)
    age = now - fetched_at if fetched_at else 999999.0

    if isinstance(payload, dict) and age < ttl_seconds:
        return _alpha_return_cached(cache, "ops_feed", ttl_seconds, age, stale=False, refreshing=False)

    # Stale cache exists: return immediately and refresh in background.
    if isinstance(payload, dict):
        refreshing = _alpha_spawn_refresh("ops_feed", lambda: _refresh_ops_feed_cache_g18b(cache, ttl_seconds))
        return _alpha_return_cached(cache, "ops_feed", ttl_seconds, age, stale=True, refreshing=refreshing)

    # Cold start: build once synchronously.
    return _refresh_ops_feed_cache_g18b(cache, ttl_seconds, return_payload=True)


def _refresh_ops_feed_cache_g18b(cache, ttl_seconds, return_payload=False):
    started = time.monotonic()
    feed = _build_ops_feed_payload_g18b()

    if isinstance(feed, dict):
        cache["payload"] = _alpha_deepcopy_payload(feed)
        cache["fetched_at"] = started
        cache["ttl_seconds"] = ttl_seconds

        feed["_cache"] = _alpha_cache_meta(
            layer="ops_feed",
            hit=False,
            age=0.0,
            ttl_seconds=ttl_seconds,
            stale=False,
            refreshing=False,
        )

    if return_payload:
        return feed
    return None


@app.get("/api/cache/warm")
def api_cache_warm():
    """
    ALPHA-G18B:
    Throttled manual cache warmer.

    Prevents high-frequency callers from repeatedly launching expensive warm cycles.
    """
    now = time.monotonic()
    ttl_seconds = float(_ALPHA_WARM_CACHE.get("ttl_seconds") or 20.0)
    fetched_at = float(_ALPHA_WARM_CACHE.get("fetched_at") or 0.0)
    age = now - fetched_at if fetched_at else 999999.0

    if _ALPHA_WARM_CACHE.get("in_progress"):
        payload = _ALPHA_WARM_CACHE.get("payload") or {
            "schema_version": "alpha-g18b.throttled_cache_warmer.1",
            "ok": True,
            "results": {},
        }
        out = _alpha_deepcopy_payload(payload)
        out["_cache"] = {
            "enabled": True,
            "hit": True,
            "stale": age >= ttl_seconds,
            "in_progress": True,
            "age_seconds": round(age, 2),
            "ttl_seconds": ttl_seconds,
            "layer": "cache_warm",
        }
        return out

    if isinstance(_ALPHA_WARM_CACHE.get("payload"), dict) and age < ttl_seconds:
        out = _alpha_deepcopy_payload(_ALPHA_WARM_CACHE["payload"])
        out["_cache"] = {
            "enabled": True,
            "hit": True,
            "stale": False,
            "in_progress": False,
            "age_seconds": round(age, 2),
            "ttl_seconds": ttl_seconds,
            "layer": "cache_warm",
        }
        return out

    started_at = time.monotonic()
    _ALPHA_WARM_CACHE["in_progress"] = True
    _ALPHA_WARM_CACHE["last_started_at"] = started_at
    results = {}

    def run_step(name, fn):
        step_start = time.monotonic()
        try:
            payload = fn()
            cache_meta = payload.get("_cache") if isinstance(payload, dict) else None
            results[name] = {
                "ok": True,
                "elapsed_ms": round((time.monotonic() - step_start) * 1000, 2),
                "cache": cache_meta,
            }
        except Exception as exc:
            results[name] = {
                "ok": False,
                "elapsed_ms": round((time.monotonic() - step_start) * 1000, 2),
                "error": f"{type(exc).__name__}: {exc}",
            }

    try:
        # Order matters: status/external feed the heavier Alpha summaries.
        run_step("status", status)
        run_step("external", external_health)
        run_step("ops_brief", api_ops_brief)
        run_step("ops_feed", api_ops_feed)

        ok = all(item.get("ok") for item in results.values())

        payload = {
            "schema_version": "alpha-g18b.throttled_cache_warmer.1",
            "ok": ok,
            "elapsed_ms": round((time.monotonic() - started_at) * 1000, 2),
            "results": results,
        }

        _ALPHA_WARM_CACHE["payload"] = _alpha_deepcopy_payload(payload)
        _ALPHA_WARM_CACHE["fetched_at"] = started_at
        _ALPHA_WARM_CACHE["last_finished_at"] = time.monotonic()

        payload["_cache"] = {
            "enabled": True,
            "hit": False,
            "stale": False,
            "in_progress": False,
            "age_seconds": 0.0,
            "ttl_seconds": ttl_seconds,
            "layer": "cache_warm",
        }
        return payload
    finally:
        _ALPHA_WARM_CACHE["in_progress"] = False


@app.get("/api/ops/services")
def api_ops_services():
    refresh_ops_state()
    return build_service_summary()

@app.get("/api/ops/latency")
def api_ops_latency():
    refresh_ops_state()
    return build_latency_summary()

@app.get("/api/ops/resources")
def api_ops_resources():
    refresh_ops_state()
    return build_resource_summary()

@app.get("/api/ops/overview")
def api_ops_overview():
    refresh_ops_state()
    apply_event_lifecycle()
    return build_alpha_overview()

def _build_alpha_brief_payload_g18b():
    refresh_ops_state()
    apply_event_lifecycle()
    brief = build_alpha_brief()

    # ALPHA-G1:
    # Restore embedded external telemetry into cached Alpha brief.
    try:
        if isinstance(brief, dict):
            brief["external"] = external_health()
    except Exception as exc:
        if isinstance(brief, dict):
            brief["external"] = {
                "error": str(exc)
            }

    # PHASE 2B-WX: merge normalized NWS alert timing into Alpha brief.
    try:
        alerts_payload = get_weather_alerts()
        alerts_summary = _wx_alerts_summary(alerts_payload)
        if isinstance(brief, dict):
            brief.setdefault("weather", {})
            if isinstance(brief.get("weather"), dict):
                brief["weather"]["alerts"] = alerts_summary
                alert_text = _wx_alert_summary_text(alerts_summary)
                if alert_text:
                    brief["weather"]["alert_summary"] = alert_text
    except Exception as exc:
        if isinstance(brief, dict):
            brief.setdefault("weather", {})
            if isinstance(brief.get("weather"), dict):
                brief["weather"]["alerts_error"] = str(exc)

    # CAM-I1B-R3: attach saved Camera Alpha health to Alpha brief.
    # Important: do not run the full live camera probe here. Snapshot checks can take several seconds.
    # The standalone /api/camera/alpha/status endpoint refreshes the saved state file.
    try:
        if isinstance(brief, dict):
            camera_state_path = _cam_Path("/home/ap3x/scripts/rh_tracker/state/camera_alpha/health_state.json")
            if camera_state_path.exists():
                camera_alpha = _cam_json.loads(camera_state_path.read_text())
                brief["camera_alpha"] = camera_alpha
            else:
                brief["camera_alpha"] = {
                    "name": "Rackmain Camera Alpha / OFFICE-CAM-1",
                    "id": "office_cam_1",
                    "posture": "UNKNOWN",
                    "online": False,
                    "mode": "snapshot_wall",
                    "issues": ["camera_health_state_missing"],
                }
    except Exception as exc:
        if isinstance(brief, dict):
            brief["camera_alpha"] = {
                "name": "Rackmain Camera Alpha / OFFICE-CAM-1",
                "id": "office_cam_1",
                "posture": "UNKNOWN",
                "online": False,
                "mode": "snapshot_wall",
                "issues": ["camera_health_state_error"],
                "error": str(exc),
            }

    return brief


@app.get("/api/ops/brief")
def api_ops_brief():
    """
    ALPHA-G18B:
    Stale-while-revalidate Alpha brief.

    If a previous payload exists, wall requests never block on expensive refresh.
    """
    ttl_seconds = 12.0
    now = time.monotonic()

    cache = globals().setdefault(
        "_OPS_BRIEF_CACHE",
        {
            "payload": None,
            "fetched_at": 0.0,
            "ttl_seconds": ttl_seconds,
        },
    )

    payload = cache.get("payload")
    fetched_at = float(cache.get("fetched_at") or 0.0)
    age = now - fetched_at if fetched_at else 999999.0

    if isinstance(payload, dict) and age < ttl_seconds:
        return _alpha_return_cached(cache, "ops_brief", ttl_seconds, age, stale=False, refreshing=False)

    # Stale cache exists: return immediately and refresh in background.
    if isinstance(payload, dict):
        refreshing = _alpha_spawn_refresh("ops_brief", lambda: _refresh_alpha_brief_cache_g18b(cache, ttl_seconds))
        return _alpha_return_cached(cache, "ops_brief", ttl_seconds, age, stale=True, refreshing=refreshing)

    # Cold start: build once synchronously.
    return _refresh_alpha_brief_cache_g18b(cache, ttl_seconds, return_payload=True)


def _refresh_alpha_brief_cache_g18b(cache, ttl_seconds, return_payload=False):
    started = time.monotonic()
    brief = _build_alpha_brief_payload_g18b()

    if isinstance(brief, dict):
        cache["payload"] = _alpha_deepcopy_payload(brief)
        cache["fetched_at"] = started
        cache["ttl_seconds"] = ttl_seconds

        brief["_cache"] = _alpha_cache_meta(
            layer="ops_brief",
            hit=False,
            age=0.0,
            ttl_seconds=ttl_seconds,
            stale=False,
            refreshing=False,
        )

    if return_payload:
        return brief
    return None


# =============================================================================
# ALPHA-G16A workload attribution endpoint
# Purpose:
#   Attribute temporary latency/resource volatility to known heavy workloads
#   such as Plex metadata/transcode activity, media imports, qBittorrent load,
#   ffmpeg jobs, or ARR processing.
#
# Safety:
#   Read-only. Does not change Alpha scoring, service posture, or threat state.
# =============================================================================

@app.get("/api/ops/workloads")
def api_ops_workloads():
    import subprocess
    from datetime import datetime, timezone

    generated_at = datetime.now(timezone.utc).isoformat()

    classifiers = [
        {
            "id": "plex_transcode",
            "label": "Plex transcode / media analysis",
            "match": ["plex transcoder"],
            "severity": "watch",
            "summary": "Plex is actively transcoding or performing video analysis.",
            "min_cpu": 1.0,
        },
        {
            "id": "plex_scanner",
            "label": "Plex media scanner",
            "match": ["plex media scanner", "plex media scan"],
            "severity": "watch",
            "summary": "Plex is scanning, matching metadata, thumbnails, intro/credits, or library changes.",
            "min_cpu": 1.0,
        },
        {
            "id": "ffmpeg",
            "label": "FFmpeg media processing",
            "match": ["ffmpeg"],
            "severity": "watch",
            "summary": "FFmpeg media processing is active.",
            "min_cpu": 1.0,
        },
        {
            "id": "sonarr",
            "label": "Sonarr import / rename activity",
            "match": ["sonarr"],
            "severity": "info",
            "summary": "Sonarr is active and may be importing, renaming, or refreshing metadata.",
            "min_cpu": 1.0,
        },
        {
            "id": "radarr",
            "label": "Radarr import / rename activity",
            "match": ["radarr"],
            "severity": "info",
            "summary": "Radarr is active and may be importing, renaming, or refreshing metadata.",
            "min_cpu": 1.0,
        },
        {
            "id": "qbittorrent",
            "label": "qBittorrent transfer activity",
            "match": ["qbittorrent", "qbittorrent-nox"],
            "severity": "info",
            "summary": "qBittorrent is active and may contribute network/disk load.",
            "min_cpu": 10.0,
        },
    ]

    try:
        result = subprocess.run(
            ["ps", "-eo", "pid,ppid,comm,%cpu,%mem,etime,args", "--sort=-%cpu"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        lines = result.stdout.splitlines()
    except Exception as exc:
        return {
            "schema_version": "0.1",
            "system_id": "rackmain1-wall-ui",
            "generated_at": generated_at,
            "active": False,
            "posture": "UNKNOWN",
            "summary": f"Unable to inspect workloads: {exc!r}",
            "primary": None,
            "counts": {"active": 0, "watch": 0, "info": 0},
            "items": [],
            "error": repr(exc),
        }

    items = []
    seen = set()

    for line in lines[1:80]:
        lower = line.lower()

        for classifier in classifiers:
            if not any(token in lower for token in classifier["match"]):
                continue

            parts = line.split(None, 6)
            if len(parts) < 7:
                continue

            pid, ppid, comm, cpu, mem, etime, args = parts

            try:
                cpu_value = float(cpu)
            except Exception:
                cpu_value = 0.0

            try:
                mem_value = float(mem)
            except Exception:
                mem_value = 0.0

            if cpu_value < float(classifier.get("min_cpu", 1.0)):
                continue

            key = (classifier["id"], pid)
            if key in seen:
                continue
            seen.add(key)

            items.append({
                "id": classifier["id"],
                "label": classifier["label"],
                "severity": classifier["severity"],
                "summary": classifier["summary"],
                "pid": pid,
                "ppid": ppid,
                "command": comm,
                "cpu_percent": cpu_value,
                "mem_percent": mem_value,
                "elapsed": etime,
                "args": args[:500],
            })

    items.sort(key=lambda x: x.get("cpu_percent", 0), reverse=True)

    watch_count = sum(1 for x in items if x.get("severity") == "watch")
    info_count = sum(1 for x in items if x.get("severity") == "info")
    active = bool(items)

    if not active:
        posture = "STABLE"
        primary = None
        summary = "No known heavy media/workload activity detected."
    else:
        primary = items[0]
        posture = "WATCH" if watch_count else "INFO"
        summary = f"Known workload active: {primary['label']} ({primary['cpu_percent']:.1f}% CPU)."

    return {
        "schema_version": "0.1",
        "system_id": "rackmain1-wall-ui",
        "generated_at": generated_at,
        "active": active,
        "posture": posture,
        "summary": summary,
        "primary": primary,
        "counts": {
            "active": len(items),
            "watch": watch_count,
            "info": info_count,
        },
        "items": items[:12],
        "error": None,
    }

# ===== CAMERA_ALPHA_HEALTH_V1 =====
# Camera Alpha / OFFICE-CAM-1 backend health telemetry.
# CAM-I1A: read-only health checks. No motion detection, no Discord, no alerts yet.

import os as _cam_os
import time as _cam_time
import socket as _cam_socket
import subprocess as _cam_subprocess
import urllib.request as _cam_urllib_request
from pathlib import Path as _cam_Path
import json as _cam_json


_CAMERA_ALPHA_NAME = "Rackmain Camera Alpha / OFFICE-CAM-1"
_CAMERA_ALPHA_IP = _cam_os.getenv("CAMERA_ALPHA_IP", "192.168.4.153")
_CAMERA_ALPHA_RTSP_PORT = int(_cam_os.getenv("CAMERA_ALPHA_RTSP_PORT", "554"))
_CAMERA_ALPHA_ONVIF_PORT = int(_cam_os.getenv("CAMERA_ALPHA_ONVIF_PORT", "8000"))
_CAMERA_ALPHA_STREAM = _cam_os.getenv("CAMERA_ALPHA_STREAM", "office_cam_wall")
_CAMERA_ALPHA_GO2RTC_BASE = _cam_os.getenv("CAMERA_ALPHA_GO2RTC_BASE", "http://127.0.0.1:1984")
_CAMERA_ALPHA_PROXY_BASE = _cam_os.getenv("CAMERA_ALPHA_PROXY_BASE", "http://127.0.0.1:3098/go2rtc")
_CAMERA_ALPHA_STATE_DIR = _cam_Path("/home/ap3x/scripts/rh_tracker/state/camera_alpha")
_CAMERA_ALPHA_STATE_FILE = _CAMERA_ALPHA_STATE_DIR / "health_state.json"


def _camera_alpha_iso(ts=None):
    if ts is None:
        ts = _cam_time.time()
    return _cam_time.strftime("%Y-%m-%dT%H:%M:%S%z", _cam_time.localtime(ts))


def _camera_alpha_ping(host, timeout_seconds=1):
    try:
        result = _cam_subprocess.run(
            ["ping", "-c", "1", "-W", str(timeout_seconds), host],
            stdout=_cam_subprocess.DEVNULL,
            stderr=_cam_subprocess.DEVNULL,
            timeout=timeout_seconds + 1,
        )
        return result.returncode == 0
    except Exception:
        return False


def _camera_alpha_tcp(host, port, timeout_seconds=2):
    started = _cam_time.time()
    try:
        with _cam_socket.create_connection((host, int(port)), timeout=timeout_seconds):
            return {
                "ok": True,
                "latency_ms": round((_cam_time.time() - started) * 1000, 1),
                "error": None,
            }
    except Exception as exc:
        return {
            "ok": False,
            "latency_ms": None,
            "error": str(exc),
        }


def _camera_alpha_http_json(url, timeout_seconds=4):
    started = _cam_time.time()
    try:
        req = _cam_urllib_request.Request(url, headers={"User-Agent": "PriorityAlpha-CameraHealth/1.0"})
        with _cam_urllib_request.urlopen(req, timeout=timeout_seconds) as resp:
            raw = resp.read(2_000_000)
            status = getattr(resp, "status", None)
            content_type = resp.headers.get("Content-Type", "")
        return {
            "ok": 200 <= int(status or 0) < 300,
            "status_code": status,
            "latency_ms": round((_cam_time.time() - started) * 1000, 1),
            "content_type": content_type,
            "data": _cam_json.loads(raw.decode("utf-8", errors="replace")) if raw else None,
            "error": None,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status_code": None,
            "latency_ms": None,
            "content_type": None,
            "data": None,
            "error": str(exc),
        }


def _camera_alpha_snapshot_check(url, timeout_seconds=6):
    started = _cam_time.time()
    try:
        req = _cam_urllib_request.Request(url, headers={"User-Agent": "PriorityAlpha-CameraHealth/1.0"})
        with _cam_urllib_request.urlopen(req, timeout=timeout_seconds) as resp:
            sample = resp.read(4096)
            status = getattr(resp, "status", None)
            content_type = resp.headers.get("Content-Type", "")
            length = resp.headers.get("Content-Length")

        jpeg_magic = sample.startswith(b"\xff\xd8")
        ok = 200 <= int(status or 0) < 300 and ("image/jpeg" in content_type.lower() or jpeg_magic)

        return {
            "ok": ok,
            "status_code": status,
            "content_type": content_type,
            "jpeg_magic": jpeg_magic,
            "content_length": int(length) if str(length or "").isdigit() else None,
            "latency_ms": round((_cam_time.time() - started) * 1000, 1),
            "error": None,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status_code": None,
            "content_type": None,
            "jpeg_magic": False,
            "content_length": None,
            "latency_ms": None,
            "error": str(exc),
        }


def _camera_alpha_read_previous_state():
    try:
        if _CAMERA_ALPHA_STATE_FILE.exists():
            return _cam_json.loads(_CAMERA_ALPHA_STATE_FILE.read_text())
    except Exception:
        pass
    return {}


def _camera_alpha_write_state(payload):
    try:
        _CAMERA_ALPHA_STATE_DIR.mkdir(parents=True, exist_ok=True)
        _CAMERA_ALPHA_STATE_FILE.write_text(_cam_json.dumps(payload, indent=2, sort_keys=True))
    except Exception:
        pass


def get_camera_alpha_health():
    now = _cam_time.time()
    now_iso = _camera_alpha_iso(now)

    prev = _camera_alpha_read_previous_state()
    prev_last_good = prev.get("last_good_frame_at")

    ping_ok = _camera_alpha_ping(_CAMERA_ALPHA_IP)

    rtsp = _camera_alpha_tcp(_CAMERA_ALPHA_IP, _CAMERA_ALPHA_RTSP_PORT)
    onvif = _camera_alpha_tcp(_CAMERA_ALPHA_IP, _CAMERA_ALPHA_ONVIF_PORT)
    go2rtc_tcp = _camera_alpha_tcp("127.0.0.1", 1984)

    streams_url = f"{_CAMERA_ALPHA_GO2RTC_BASE.rstrip('/')}/api/streams"
    streams = _camera_alpha_http_json(streams_url)

    stream_present = False
    try:
        stream_present = _CAMERA_ALPHA_STREAM in (streams.get("data") or {})
    except Exception:
        stream_present = False

    direct_snapshot_url = f"{_CAMERA_ALPHA_GO2RTC_BASE.rstrip('/')}/api/frame.jpeg?src={_CAMERA_ALPHA_STREAM}"
    proxy_snapshot_url = f"{_CAMERA_ALPHA_PROXY_BASE.rstrip('/')}/api/frame.jpeg?src={_CAMERA_ALPHA_STREAM}"

    direct_snapshot = _camera_alpha_snapshot_check(direct_snapshot_url)
    proxy_snapshot = _camera_alpha_snapshot_check(proxy_snapshot_url)

    snapshot_ok = bool(direct_snapshot.get("ok") or proxy_snapshot.get("ok"))

    last_good_frame_at = prev_last_good
    if snapshot_ok:
        last_good_frame_at = now_iso

    frame_age_seconds = None
    if last_good_frame_at:
        try:
            # The timestamp is local ISO-ish without colon in offset; keep this simple and robust.
            last_epoch = prev.get("last_good_frame_epoch")
            if snapshot_ok:
                last_epoch = now
            if last_epoch:
                frame_age_seconds = round(now - float(last_epoch), 1)
        except Exception:
            frame_age_seconds = None

    if snapshot_ok and rtsp.get("ok") and go2rtc_tcp.get("ok"):
        posture = "ONLINE"
    elif ping_ok or rtsp.get("ok") or onvif.get("ok") or go2rtc_tcp.get("ok"):
        posture = "DEGRADED"
    else:
        posture = "OFFLINE"

    online = posture == "ONLINE"

    issues = []
    if not ping_ok:
        issues.append("camera_ping_failed")
    if not rtsp.get("ok"):
        issues.append("rtsp_unreachable")
    if not onvif.get("ok"):
        issues.append("onvif_unreachable")
    if not go2rtc_tcp.get("ok"):
        issues.append("go2rtc_api_unreachable")
    if not streams.get("ok"):
        issues.append("go2rtc_stream_inventory_failed")
    if not stream_present:
        issues.append("office_cam_wall_stream_missing")
    if not snapshot_ok:
        issues.append("snapshot_unavailable")

    payload = {
        "name": _CAMERA_ALPHA_NAME,
        "id": "office_cam_1",
        "ip": _CAMERA_ALPHA_IP,
        "stream": _CAMERA_ALPHA_STREAM,
        "mode": "snapshot_wall",
        "posture": posture,
        "online": online,
        "updated_at": now_iso,
        "ping_ok": ping_ok,
        "rtsp": {
            "port": _CAMERA_ALPHA_RTSP_PORT,
            **rtsp,
        },
        "onvif": {
            "port": _CAMERA_ALPHA_ONVIF_PORT,
            **onvif,
        },
        "go2rtc": {
            "online": bool(go2rtc_tcp.get("ok")),
            "api": go2rtc_tcp,
            "streams_ok": bool(streams.get("ok")),
            "stream_present": stream_present,
            "streams_url": streams_url,
        },
        "snapshot": {
            "ok": snapshot_ok,
            "direct_url": direct_snapshot_url,
            "proxy_url": "/go2rtc/api/frame.jpeg?src=" + _CAMERA_ALPHA_STREAM,
            "direct": direct_snapshot,
            "proxy": proxy_snapshot,
            "last_good_frame_at": last_good_frame_at,
            "last_good_frame_epoch": now if snapshot_ok else prev.get("last_good_frame_epoch"),
            "frame_age_seconds": frame_age_seconds,
        },
        "issues": issues,
    }

    _camera_alpha_write_state(payload)
    return payload


@app.get("/api/camera/alpha/status")
def api_camera_alpha_status():
    return get_camera_alpha_health()


@app.get("/camera/alpha/status")
def camera_alpha_status_alias():
    return get_camera_alpha_health()

# ===== END CAMERA_ALPHA_HEALTH_V1 =====


# ===== MG-SENTINEL-1A API ENDPOINTS =====
# Read-only MarketGlass Sentinel state bridge.
# Safety policy: exposes files only; performs no broker writes and no order placement.

def _mg_sent_path(name: str):
    from pathlib import Path
    base = Path("/home/ap3x/scripts/market_sentinel/state")
    return base / name

def _mg_sent_read_json(name: str, default):
    import json
    p = _mg_sent_path(name)
    try:
        if not p.exists():
            return default
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "available": False,
            "error": f"{type(exc).__name__}: {exc}",
            "file": str(p),
            "default": default
        }

def _mg_sent_read_jsonl_tail(name: str, limit: int = 25):
    import json
    p = _mg_sent_path(name)
    if not p.exists():
        return []
    try:
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        out = []
        for line in lines[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
        return out
    except Exception:
        return []

def _mg_sent_file_age_seconds(name: str):
    import time
    p = _mg_sent_path(name)
    if not p.exists():
        return None
    try:
        return round(time.time() - p.stat().st_mtime, 1)
    except Exception:
        return None


# ===== MG-SENTINEL-1B RECENT SIGNAL MEMORY HELPERS =====
# Read-only enrichment for /api/market/sentinel.
# Parses signals.jsonl only; performs no broker writes and no order actions.
def _mg_sent_read_jsonl_all(name: str, max_lines: int = 5000):
    import json
    p = _mg_sent_path(name)
    if not p.exists():
        return []
    try:
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        if max_lines and len(lines) > max_lines:
            lines = lines[-max_lines:]
        out = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
        return out
    except Exception:
        return []


def _mg_sent_local_date_prefix():
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


def _mg_sent_minutes_since(timestamp: str):
    if not timestamp:
        return None
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/New_York")
        raw = str(timestamp).replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        now = datetime.now(tz)
        return round((now - dt.astimezone(tz)).total_seconds() / 60.0, 1)
    except Exception:
        return None


def _mg_sent_compact_signal(sig):
    if not isinstance(sig, dict):
        return None
    return {
        "timestamp": sig.get("timestamp"),
        "symbol": sig.get("symbol"),
        "setup": sig.get("setup"),
        "window": sig.get("window"),
        "score": sig.get("score"),
        "risk": sig.get("risk"),
        "move_pct": sig.get("move_pct"),
        "price": sig.get("price"),
        "session": sig.get("session"),
        "action": sig.get("action", "CONFIRM_MANUALLY"),
        "live_transaction_performed": False,
        "mode": "read_only"
    }


def _mg_sent_signal_memory(limit: int = 10):
    signals = _mg_sent_read_jsonl_all("signals.jsonl", max_lines=5000)
    today = _mg_sent_local_date_prefix()

    today_signals = [
        s for s in signals
        if isinstance(s, dict) and str(s.get("timestamp", "")).startswith(today)
    ]

    last_signal = today_signals[-1] if today_signals else (signals[-1] if signals else None)
    recent = list(reversed(today_signals[-limit:])) if today_signals else list(reversed(signals[-limit:]))

    by_symbol = {}
    by_setup = {}
    for sig in today_signals:
        sym = sig.get("symbol") or "UNKNOWN"
        setup = sig.get("setup") or "UNKNOWN"
        by_symbol[sym] = by_symbol.get(sym, 0) + 1
        by_setup[setup] = by_setup.get(setup, 0) + 1

    top_symbol = None
    if by_symbol:
        sym, count = sorted(by_symbol.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        top_symbol = {"symbol": sym, "count": count}

    compact_last = _mg_sent_compact_signal(last_signal) if last_signal else None

    return {
        "schema_version_recent": "MG-SENTINEL-1B.recent_memory",
        "today_signal_count": len(today_signals),
        "lifetime_signal_count": len(signals),
        "last_signal": compact_last,
        "last_signal_minutes_ago": _mg_sent_minutes_since(compact_last.get("timestamp")) if compact_last else None,
        "recent_signals": [
            c for c in (_mg_sent_compact_signal(s) for s in recent)
            if c is not None
        ],
        "today_signal_by_symbol": [
            {"symbol": k, "count": v}
            for k, v in sorted(by_symbol.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        "today_signal_by_setup": [
            {"setup": k, "count": v}
            for k, v in sorted(by_setup.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        "top_signal_symbol_today": top_symbol
    }

@app.get("/api/market/sentinel")
def market_sentinel():
    latest = _mg_sent_read_json("latest.json", {
        "schema_version": "MG-SENTINEL-1A.latest",
        "status": "UNAVAILABLE",
        "mode": "read_only",
        "broker_writes_enabled": False,
        "live_trading_enabled": False,
        "signals": [],
        "best_signal": None
    })
    if isinstance(latest, dict):
        latest.setdefault("mode", "read_only")
        latest["broker_writes_enabled"] = False
        latest["live_trading_enabled"] = False
        latest.setdefault("health", {})
        latest["health"]["state_age_seconds"] = _mg_sent_file_age_seconds("latest.json")
        latest.update(_mg_sent_signal_memory(limit=10))
    return latest

@app.get("/api/market/signals")
def market_signals(limit: int = 25):
    limit = max(1, min(int(limit), 100))
    return {
        "schema_version": "MG-SENTINEL-1A.signals",
        "mode": "read_only",
        "broker_writes_enabled": False,
        "live_trading_enabled": False,
        "signals": _mg_sent_read_jsonl_tail("signals.jsonl", limit=limit)
    }

@app.get("/api/market/health")
def market_health():
    latest_age = _mg_sent_file_age_seconds("latest.json")
    heartbeat_age = _mg_sent_file_age_seconds("heartbeat.json")
    if heartbeat_age is None:
        posture = "UNAVAILABLE"
    elif heartbeat_age <= 900:
        posture = "LIVE"
    elif heartbeat_age <= 1800:
        posture = "STALE"
    else:
        posture = "DEGRADED"

    return {
        "schema_version": "MG-SENTINEL-1A.health",
        "posture": posture,
        "mode": "read_only",
        "worker": "OK" if posture == "LIVE" else posture,
        "latest_state_age_seconds": latest_age,
        "last_heartbeat_age_seconds": heartbeat_age,
        "broker_writes_enabled": False,
        "live_trading_enabled": False,
        "allowed_actions": [
            "read_market_data",
            "read_robinhood_snapshot",
            "create_paper_trade",
            "send_alert"
        ],
        "blocked_actions": [
            "place_order",
            "cancel_order",
            "transfer_cash",
            "enable_margin",
            "trade_options",
            "trade_futures"
        ]
    }

@app.get("/api/market/watchlist")
def market_watchlist():
    return _mg_sent_read_json("watchlist_status.json", {
        "schema_version": "MG-SENTINEL-1A.watchlist_status",
        "available": False,
        "watchlist": []
    })

@app.get("/api/market/paper")
def market_paper(limit: int = 25):
    limit = max(1, min(int(limit), 100))
    entries = _mg_sent_read_jsonl_tail("paper_trades.jsonl", limit=limit)
    return {
        "schema_version": "MG-SENTINEL-1A.paper",
        "mode": "paper_only",
        "broker_writes_enabled": False,
        "live_trading_enabled": False,
        "recent": entries,
        "count_returned": len(entries)
    }



# ===== MG-SENTINEL-1C-C NOTIFICATION OBSERVABILITY API =====
# Read-only notification policy and decision visibility.
# Safety policy: parses policy/signals only; performs no Discord sends, broker writes, or order placement.

def _mg_sent_policy_path():
    from pathlib import Path
    return Path("/home/ap3x/scripts/market_sentinel/notification_policy.json")


def _mg_sent_read_policy():
    import json
    p = _mg_sent_policy_path()
    default = {
        "schema_version": "MG-SENTINEL-1C.notification_policy.unavailable",
        "available": False,
        "enabled": False,
        "mode": "read_only_notify_only"
    }
    try:
        if not p.exists():
            return default
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return default
        # Never expose secrets here. This file should not contain a webhook, but keep this defensive.
        data = dict(data)
        for k in list(data.keys()):
            if "webhook" in str(k).lower() or "token" in str(k).lower() or "secret" in str(k).lower():
                data[k] = "[REDACTED]"
        data["available"] = True
        return data
    except Exception as exc:
        return {
            **default,
            "error": f"{type(exc).__name__}: {exc}"
        }


def _mg_sent_notification_tier_sort(tier):
    order = {"HIGH": 0, "ACTIONABLE": 1, "WATCH": 2, "INFO": 3, "UNKNOWN": 4}
    return order.get(str(tier or "UNKNOWN").upper(), 9)


def _mg_sent_notification_compact(sig):
    if not isinstance(sig, dict):
        return None
    tier = sig.get("notification_tier")
    if not tier:
        # Legacy rows before 1C-B do not have notification metadata.
        tier = "LEGACY"
    return {
        "timestamp": sig.get("timestamp"),
        "symbol": sig.get("symbol"),
        "setup": sig.get("setup"),
        "window": sig.get("window"),
        "score": sig.get("score"),
        "risk": sig.get("risk"),
        "move_pct": sig.get("move_pct"),
        "move_direction": sig.get("move_direction"),
        "threshold_pct": sig.get("threshold_pct"),
        "threshold_multiple": sig.get("threshold_multiple"),
        "session": sig.get("session"),
        "notification_tier": tier,
        "notification_should_send": sig.get("notification_should_send"),
        "notification_reason": sig.get("notification_reason"),
        "notification_suppressed_reason": sig.get("notification_suppressed_reason"),
        "discord_sent": sig.get("discord_sent"),
        "paper_trade_created": sig.get("paper_trade_created"),
        "live_transaction_performed": False,
        "mode": "read_only"
    }


def _mg_sent_count_values(items, key, missing_label="MISSING"):
    counts = {}
    for item in items:
        val = item.get(key) if isinstance(item, dict) else None
        if val is None or val == "":
            val = missing_label
        val = str(val)
        counts[val] = counts.get(val, 0) + 1
    return [
        {"value": k, "count": v}
        for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def _mg_sent_count_bool(items, key):
    out = {"true": 0, "false": 0, "missing": 0}
    for item in items:
        if not isinstance(item, dict) or key not in item or item.get(key) is None:
            out["missing"] += 1
        elif bool(item.get(key)):
            out["true"] += 1
        else:
            out["false"] += 1
    return out


def _mg_sent_notifications_summary(limit: int = 25):
    signals = _mg_sent_read_jsonl_all("signals.jsonl", max_lines=5000)
    today = _mg_sent_local_date_prefix()
    today_signals = [
        s for s in signals
        if isinstance(s, dict) and str(s.get("timestamp", "")).startswith(today)
    ]

    notification_rows = [
        s for s in today_signals
        if isinstance(s, dict) and s.get("notification_schema_version")
    ]
    legacy_rows = [
        s for s in today_signals
        if isinstance(s, dict) and not s.get("notification_schema_version")
    ]

    recent_source = today_signals if today_signals else signals
    recent = [
        c for c in (_mg_sent_notification_compact(s) for s in reversed(recent_source[-limit:]))
        if c is not None
    ]

    tier_counts_raw = {}
    for sig in notification_rows:
        tier = str(sig.get("notification_tier") or "UNKNOWN").upper()
        tier_counts_raw[tier] = tier_counts_raw.get(tier, 0) + 1

    tier_counts = [
        {"tier": k, "count": v}
        for k, v in sorted(tier_counts_raw.items(), key=lambda kv: (_mg_sent_notification_tier_sort(kv[0]), kv[0]))
    ]

    suppressed = [
        s for s in notification_rows
        if s.get("notification_suppressed_reason")
    ]

    return {
        "schema_version": "MG-SENTINEL-1C-C.notifications",
        "mode": "read_only",
        "broker_writes_enabled": False,
        "live_trading_enabled": False,
        "policy": _mg_sent_read_policy(),
        "today": today,
        "today_signal_count": len(today_signals),
        "today_notification_metadata_count": len(notification_rows),
        "today_legacy_missing_notification_metadata_count": len(legacy_rows),
        "lifetime_signal_count": len(signals),
        "recent_count": len(recent),
        "recent_notifications": recent,
        "today_tier_counts": tier_counts,
        "today_should_send_counts": _mg_sent_count_bool(notification_rows, "notification_should_send"),
        "today_discord_sent_counts": _mg_sent_count_bool(notification_rows, "discord_sent"),
        "today_suppressed_reason_counts": _mg_sent_count_values(suppressed, "notification_suppressed_reason", missing_label="NONE"),
        "today_direction_counts": _mg_sent_count_values(notification_rows, "move_direction", missing_label="UNKNOWN"),
        "notes": [
            "Legacy rows were created before MG-SENTINEL-1C-B and may not contain notification metadata.",
            "This endpoint is read-only and does not send Discord notifications.",
            "Broker and live trading flags are forced false."
        ]
    }


@app.get("/api/market/notifications")
def market_notifications(limit: int = 25):
    limit = max(1, min(int(limit), 100))
    return _mg_sent_notifications_summary(limit=limit)




# ===== MG-SENTINEL-1E PAPER OUTCOMES API =====
# Read-only paper evaluator visibility. No broker writes, no live trading.

def _mg_sent_read_paper_summary():
    import json
    p = _mg_sent_path("paper_outcomes_summary.json")
    default = {
        "schema_version": "MG-SENTINEL-1E.paper_outcomes_summary.unavailable",
        "available": False,
        "mode": "paper_only",
        "broker_writes_enabled": False,
        "live_trading_enabled": False
    }
    try:
        if not p.exists():
            return default
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return default
        data.setdefault("available", True)
        data["broker_writes_enabled"] = False
        data["live_trading_enabled"] = False
        return data
    except Exception as exc:
        return {
            **default,
            "error": f"{type(exc).__name__}: {exc}"
        }


def _mg_sent_compact_paper_outcome(o):
    if not isinstance(o, dict):
        return None
    return {
        "generated_at": o.get("generated_at"),
        "source_trade_timestamp": o.get("source_trade_timestamp"),
        "symbol": o.get("symbol"),
        "setup": o.get("setup"),
        "window_minutes": o.get("window_minutes"),
        "entry_price": o.get("entry_price"),
        "exit_price": o.get("exit_price"),
        "return_pct": o.get("return_pct"),
        "outcome": o.get("outcome"),
        "observed_at": o.get("observed_at"),
        "side_lens": o.get("side_lens"),
        "source_signal_score": o.get("source_signal_score"),
        "mode": "paper_only",
        "live_transaction_performed": False,
        "broker_writes_enabled": False,
        "live_trading_enabled": False,
        "error": o.get("error")
    }


@app.get("/api/market/paper/outcomes")
def market_paper_outcomes(limit: int = 50):
    limit = max(1, min(int(limit), 200))
    outcomes = _mg_sent_read_jsonl_tail("paper_outcomes.jsonl", limit=limit)
    compact = [
        c for c in (_mg_sent_compact_paper_outcome(o) for o in outcomes)
        if c is not None
    ]
    return {
        "schema_version": "MG-SENTINEL-1E.paper_outcomes",
        "mode": "paper_only",
        "broker_writes_enabled": False,
        "live_trading_enabled": False,
        "summary": _mg_sent_read_paper_summary(),
        "count_returned": len(compact),
        "recent": compact,
        "notes": [
            "Paper outcomes evaluate signal usefulness only.",
            "No live transactions are performed."
        ]
    }




# ===== MG-SENTINEL-1F PAPER PERFORMANCE API =====
# Outcome-aware signal quality. Read-only. No broker writes, no live trading.

def _mg_sent_float(value, default=None):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _mg_sent_int(value, default=0):
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default


def _mg_sent_performance_label(row):
    count = int(row.get("count", 0) or 0)
    win_rate = _mg_sent_float(row.get("win_rate_pct"), 0.0) or 0.0
    avg_return = _mg_sent_float(row.get("avg_return_pct"), 0.0) or 0.0
    loss_rate = _mg_sent_float(row.get("loss_rate_pct"), 0.0) or 0.0

    if count < 3:
        return "INSUFFICIENT_DATA"
    if win_rate >= 50 and avg_return > 0:
        return "APPROVED_CANDIDATE"
    if win_rate >= 35 and avg_return >= -0.10:
        return "WATCHLIST_CANDIDATE"
    if loss_rate >= 55 or avg_return <= -0.35:
        return "DOWNGRADE_CANDIDATE"
    return "NEEDS_MORE_DATA"


def _mg_sent_performance_recommendation(label):
    if label == "APPROVED_CANDIDATE":
        return "Keep eligible for actionable alerts, but still require manual confirmation."
    if label == "WATCHLIST_CANDIDATE":
        return "Keep visible but prefer WATCH tier until more outcomes accumulate."
    if label == "DOWNGRADE_CANDIDATE":
        return "Consider suppressing or downgrading this setup in notification policy."
    if label == "INSUFFICIENT_DATA":
        return "Do not make policy changes yet; collect more outcomes."
    return "Continue collecting outcomes before changing alert behavior."


def _mg_sent_make_perf_row(key, rows):
    count = len(rows)
    wins = 0
    flats = 0
    losses = 0
    unavailable = 0
    total_ret = 0.0
    valid_count = 0

    best = None
    worst = None

    for r in rows:
        outcome = str(r.get("outcome") or "UNKNOWN")
        ret = _mg_sent_float(r.get("return_pct"))

        if outcome == "UNAVAILABLE" or ret is None:
            unavailable += 1
            continue

        valid_count += 1
        total_ret += ret

        if outcome in ("STRONG_FOLLOW_THROUGH", "FOLLOW_THROUGH"):
            wins += 1
        elif outcome == "FLAT":
            flats += 1
        else:
            losses += 1

        if best is None or ret > best.get("return_pct", -999):
            best = r
        if worst is None or ret < worst.get("return_pct", 999):
            worst = r

    avg = round(total_ret / valid_count, 4) if valid_count else None
    win_rate = round((wins / valid_count) * 100.0, 2) if valid_count else None
    flat_rate = round((flats / valid_count) * 100.0, 2) if valid_count else None
    loss_rate = round((losses / valid_count) * 100.0, 2) if valid_count else None

    row = {
        "key": key,
        "count": count,
        "valid_count": valid_count,
        "wins": wins,
        "flats": flats,
        "losses": losses,
        "unavailable": unavailable,
        "avg_return_pct": avg,
        "win_rate_pct": win_rate,
        "flat_rate_pct": flat_rate,
        "loss_rate_pct": loss_rate,
        "best_return_pct": best.get("return_pct") if best else None,
        "worst_return_pct": worst.get("return_pct") if worst else None,
        "latest_generated_at": rows[-1].get("generated_at") if rows else None,
        "mode": "paper_only",
        "broker_writes_enabled": False,
        "live_trading_enabled": False
    }

    label = _mg_sent_performance_label(row)
    row["quality_label"] = label
    row["recommendation"] = _mg_sent_performance_recommendation(label)
    return row


def _mg_sent_group_outcomes(outcomes, key_fields):
    groups = {}
    for o in outcomes:
        if not isinstance(o, dict):
            continue
        if o.get("mode") != "paper_only":
            continue
        key_parts = [str(o.get(field, "UNKNOWN")) for field in key_fields]
        key = "::".join(key_parts)
        groups.setdefault(key, []).append(o)

    rows = [_mg_sent_make_perf_row(key, rows) for key, rows in groups.items()]
    rows.sort(
        key=lambda r: (
            0 if r.get("quality_label") == "APPROVED_CANDIDATE" else
            1 if r.get("quality_label") == "WATCHLIST_CANDIDATE" else
            2 if r.get("quality_label") == "NEEDS_MORE_DATA" else
            3 if r.get("quality_label") == "DOWNGRADE_CANDIDATE" else
            4,
            -(_mg_sent_float(r.get("win_rate_pct"), -1) or -1),
            -(_mg_sent_float(r.get("avg_return_pct"), -999) or -999),
            r.get("key", "")
        )
    )
    return rows


def _mg_sent_performance_counts(rows):
    counts = {}
    for r in rows:
        label = r.get("quality_label") or "UNKNOWN"
        counts[label] = counts.get(label, 0) + 1
    return [
        {"quality_label": k, "count": v}
        for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


@app.get("/api/market/paper/performance")
def market_paper_performance(limit: int = 50):
    limit = max(1, min(int(limit), 200))

    outcomes = _mg_sent_read_jsonl_tail("paper_outcomes.jsonl", limit=5000)
    ready = [
        o for o in outcomes
        if isinstance(o, dict)
        and o.get("outcome") != "UNAVAILABLE"
        and o.get("return_pct") is not None
    ]

    by_symbol_setup_window = _mg_sent_group_outcomes(ready, ["symbol", "setup", "window_minutes"])
    by_symbol_setup = _mg_sent_group_outcomes(ready, ["symbol", "setup"])
    by_symbol = _mg_sent_group_outcomes(ready, ["symbol"])
    by_setup = _mg_sent_group_outcomes(ready, ["setup"])
    by_window = _mg_sent_group_outcomes(ready, ["window_minutes"])

    downgrade_candidates = [
        r for r in by_symbol_setup_window
        if r.get("quality_label") == "DOWNGRADE_CANDIDATE"
    ][:limit]

    approved_candidates = [
        r for r in by_symbol_setup_window
        if r.get("quality_label") in ("APPROVED_CANDIDATE", "WATCHLIST_CANDIDATE")
    ][:limit]

    return {
        "schema_version": "MG-SENTINEL-1F.paper_performance",
        "mode": "paper_only",
        "broker_writes_enabled": False,
        "live_trading_enabled": False,
        "outcome_count_loaded": len(outcomes),
        "ready_outcome_count": len(ready),
        "group_counts": {
            "symbol_setup_window": len(by_symbol_setup_window),
            "symbol_setup": len(by_symbol_setup),
            "symbol": len(by_symbol),
            "setup": len(by_setup),
            "window": len(by_window)
        },
        "quality_counts_symbol_setup_window": _mg_sent_performance_counts(by_symbol_setup_window),
        "approved_or_watchlist_candidates": approved_candidates,
        "downgrade_candidates": downgrade_candidates,
        "by_symbol_setup_window": by_symbol_setup_window[:limit],
        "by_symbol_setup": by_symbol_setup[:limit],
        "by_symbol": by_symbol[:limit],
        "by_setup": by_setup[:limit],
        "by_window": by_window[:limit],
        "notes": [
            "This endpoint evaluates paper-only signal usefulness.",
            "It does not change notification policy automatically.",
            "Use downgrade/approved labels as recommendations, not execution instructions.",
            "Broker and live trading flags are forced false."
        ]
    }




# ===== MG-SENTINEL-1G POLICY RECOMMENDATIONS API =====
# Read-only recommendation layer. Does not alter active notification_policy.json.

def _mg_sent_policy_recommendations_path():
    from pathlib import Path
    return Path("/home/ap3x/scripts/market_sentinel/notification_policy_recommendations.json")


def _mg_sent_read_policy_recommendations():
    import json
    p = _mg_sent_policy_recommendations_path()
    default = {
        "schema_version": "MG-SENTINEL-1G.policy_recommendations.unavailable",
        "available": False,
        "mode": "recommendation_only",
        "active_policy_modified": False,
        "broker_writes_enabled": False,
        "live_trading_enabled": False
    }
    try:
        if not p.exists():
            return default
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return default
        data["available"] = True
        data["active_policy_modified"] = False
        data["broker_writes_enabled"] = False
        data["live_trading_enabled"] = False
        return data
    except Exception as exc:
        return {
            **default,
            "error": f"{type(exc).__name__}: {exc}"
        }


@app.get("/api/market/policy/recommendations")
def market_policy_recommendations():
    return _mg_sent_read_policy_recommendations()


@app.get("/api/market/broker/state")
def market_broker_state():
    return {
        "schema_version": "MG-SENTINEL-1A.broker_state",
        "broker_layer_present": True,
        "mode": "read_only",
        "broker_writes_enabled": False,
        "live_trading_enabled": False,
        "robinhood_writes_enabled": False,
        "alpaca_writes_enabled": False,
        "paper_trading_enabled": True,
        "allowed_actions": [
            "read_market_data",
            "read_robinhood_snapshot",
            "create_paper_trade",
            "send_alert"
        ],
        "blocked_actions": [
            "place_order",
            "cancel_order",
            "transfer_cash",
            "enable_margin",
            "trade_options",
            "trade_futures"
        ],
        "safety_note": "Phase 1A is read-only. No live transaction path is enabled."
    }

# ===== Plex Update Sentinel API bridge =====
PLEX_UPDATE_SENTINEL_STATE = Path("/home/ap3x/scripts/plex_update_sentinel/state/plex_update_state.json")


@app.get("/api/plex/update-state")
def api_plex_update_state():
    """
    Read-only bridge for the wall UI.

    Source of truth:
    /home/ap3x/scripts/plex_update_sentinel/state/plex_update_state.json

    This endpoint does not touch Plex, does not expose tokens, and does not write state.
    """
    try:
        with PLEX_UPDATE_SENTINEL_STATE.open("r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            raise ValueError("state file did not contain a JSON object")

        data.setdefault("service", "plex")
        data.setdefault("status", "unknown")
        data.setdefault("wall_severity", "blue")
        data.setdefault("recommended_action", "none")
        return data

    except Exception as exc:
        return {
            "service": "plex",
            "status": "unknown",
            "wall_severity": "red",
            "operator_summary": "Plex Sentinel state file unavailable or unreadable.",
            "update_available": None,
            "service_active": None,
            "port_32400_ok": None,
            "web_ok": None,
            "session_check_ok": False,
            "session_check_reason": f"api_state_read_error:{type(exc).__name__}",
            "active_streams": None,
            "active_transcodes": None,
            "recommended_action": "manual_review",
            "state_path": str(PLEX_UPDATE_SENTINEL_STATE),
        }

