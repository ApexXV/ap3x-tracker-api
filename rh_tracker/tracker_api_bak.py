#!/usr/bin/env python3
"""
tracker_api.py — Production API for rh-tracker share management
================================================================
Exposes share value management over HTTP for dashboard integration.
Designed to run behind Cloudflare Tunnel + Zero Trust.

Run:
    uvicorn tracker_api:app --host 127.0.0.1 --port 8000

Endpoints:
    GET  /health          — uptime/health check
    GET  /shares          — return current VOO/NVDA values
    POST /shares/update   — update one or both values, optionally restart
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Optional

# ── Import trackerctl logic directly (no subprocess) ─────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
from trackerctl import (
    DEFAULT_SHARES_FILE,
    SERVICE_NAME,
    read_shares,
    write_shares,
    restart_service,
)

try:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel, field_validator
except ImportError:
    raise SystemExit(
        "\n  [error] FastAPI not installed.\n"
        "  Run:  pip install fastapi uvicorn\n"
    )

# ── Logging setup ─────────────────────────────────────────────────────────────
# Simple structured logging to stdout — systemd/journald will capture this.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("tracker_api")

# ── App setup ─────────────────────────────────────────────────────────────────
app = FastAPI(
    title="rh-tracker API",
    version="2.0",
    docs_url="/docs",   # disable in prod by setting to None if preferred
    redoc_url=None,
)

# ── CORS — disabled by default, enable if your dashboard origin needs it ──────
# Uncomment and set allow_origins to your dashboard URL if required:
#
# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["https://your-dashboard.domain.com"],
#     allow_methods=["GET", "POST"],
#     allow_headers=["*"],
# )

# ── In-memory rate limiting ───────────────────────────────────────────────────
# Prevents accidental double-submits from a dashboard button.
# Tracks last request time per endpoint per client IP.
_last_request: dict[str, float] = {}
RATE_LIMIT_SECONDS = 2


def check_rate_limit(key: str) -> None:
    """Raise HTTP 429 if the same key was called within RATE_LIMIT_SECONDS."""
    now  = time.monotonic()
    last = _last_request.get(key, 0.0)
    if now - last < RATE_LIMIT_SECONDS:
        remaining = round(RATE_LIMIT_SECONDS - (now - last), 1)
        raise HTTPException(
            status_code=429,
            detail=f"Too many requests. Try again in {remaining}s.",
        )
    _last_request[key] = now


# ── Request/response models ───────────────────────────────────────────────────

class UpdateRequest(BaseModel):
    voo:     Optional[float] = None
    nvda:    Optional[float] = None
    restart: bool            = False

    @field_validator("voo", "nvda", mode="before")
    @classmethod
    def must_be_non_negative(cls, v):
        if v is not None and float(v) < 0:
            raise ValueError("share quantities cannot be negative")
        return v


class SharesResponse(BaseModel):
    voo:       float
    nvda:      float
    restarted: Optional[bool] = None   # None = restart not requested


class HealthResponse(BaseModel):
    status: str


# ── Request logging middleware ────────────────────────────────────────────────

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start   = time.monotonic()
    response = await call_next(request)
    elapsed  = round((time.monotonic() - start) * 1000, 1)
    log.info(
        "%s %s — %s (%sms) — client: %s",
        request.method,
        request.url.path,
        response.status_code,
        elapsed,
        request.client.host if request.client else "unknown",
    )
    return response


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse)
def health():
    """Uptime check for Cloudflare / monitoring."""
    return HealthResponse(status="ok")


@app.get("/shares", response_model=SharesResponse)
def get_shares():
    """Return current VOO and NVDA share values."""
    log.info("GET /shares — reading %s", DEFAULT_SHARES_FILE)
    try:
        shares = read_shares(DEFAULT_SHARES_FILE)
    except FileNotFoundError as exc:
        log.error("GET /shares — file not found: %s", exc)
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        log.error("GET /shares — malformed file: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    return SharesResponse(voo=shares["VOO"], nvda=shares["NVDA"])


@app.post("/shares/update", response_model=SharesResponse)
def update_shares(req: UpdateRequest, request: Request):
    """
    Update one or both share values.
    Optionally restart the rh-tracker service after writing.
    """
    client_ip = request.client.host if request.client else "unknown"

    # Rate limit per client IP
    check_rate_limit(f"update:{client_ip}")

    if req.voo is None and req.nvda is None:
        log.warning("POST /shares/update — rejected: no values provided")
        raise HTTPException(
            status_code=422,
            detail="At least one of 'voo' or 'nvda' must be provided.",
        )

    log.info(
        "POST /shares/update — requested: voo=%s nvda=%s restart=%s — client: %s",
        req.voo, req.nvda, req.restart, client_ip,
    )

    # Read current values to fill in whichever field wasn't supplied
    try:
        current = read_shares(DEFAULT_SHARES_FILE)
    except FileNotFoundError as exc:
        log.error("POST /shares/update — file not found: %s", exc)
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        log.error("POST /shares/update — malformed file: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    new_voo  = req.voo  if req.voo  is not None else current["VOO"]
    new_nvda = req.nvda if req.nvda is not None else current["NVDA"]

    # Write
    try:
        write_shares(DEFAULT_SHARES_FILE, new_voo, new_nvda)
        log.info("POST /shares/update — written: VOO=%s NVDA=%s", new_voo, new_nvda)
    except Exception as exc:
        log.error("POST /shares/update — write failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to write shares: {exc}")

    # Optional restart
    restarted = None
    if req.restart:
        log.info("POST /shares/update — restarting service '%s'", SERVICE_NAME)
        restarted = restart_service(SERVICE_NAME)
        if restarted:
            log.info("POST /shares/update — service restarted successfully")
        else:
            log.error("POST /shares/update — service restart failed")
            raise HTTPException(
                status_code=500,
                detail="Shares updated but service restart failed.",
            )

    return SharesResponse(voo=new_voo, nvda=new_nvda, restarted=restarted)
