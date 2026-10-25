#!/usr/bin/env python3
"""Read-only media activity contract for the AP3X Local Operations wall.

MA001 intentionally distinguishes current work from work that needs attention:

* Plex transcodes and qBittorrent's real download states are activity.
* ARR-tracked stalled downloads and completed imports waiting in an ARR queue
  are attention conditions.
* Paused, queued, completed, seeding, and abandoned qBittorrent entries are not
  activity.

No credential is returned or logged. Sonarr and Radarr API keys are read only
inside the process from their containers and are used only as request headers.
"""

from __future__ import annotations

import copy
import json
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any


SCHEMA_VERSION = "0.2"
CONTRACT_ID = "MA001"
CACHE_SECONDS = 12.0
QBIT_BASE = "http://127.0.0.1:8090/api/v2"
PLEX_SESSION_HELPER = "/usr/local/sbin/plex-session-count-json"
ACTIVE_DOWNLOAD_STATES = frozenset({"downloading", "forcedDL", "metaDL"})
STALLED_DOWNLOAD_STATE = "stalledDL"

_CACHE_LOCK = threading.Lock()
_CACHE_MONOTONIC = 0.0
_CACHE_PAYLOAD: dict[str, Any] | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return default


def _run(command: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _safe_reason(error: BaseException) -> str:
    if isinstance(error, urllib.error.HTTPError):
        return f"http_{error.code}"
    if isinstance(error, urllib.error.URLError):
        return "connection_error"
    if isinstance(error, subprocess.TimeoutExpired):
        return "command_timeout"
    return type(error).__name__.lower()


def _fetch_json(url: str, headers: dict[str, str] | None = None, timeout: float = 5.0) -> Any:
    request_headers = {
        "Accept": "application/json",
        "User-Agent": "AP3X-Media-Activity-MA001/1.0",
    }
    request_headers.update(headers or {})
    request = urllib.request.Request(url, headers=request_headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _last_json_line(text: str) -> dict[str, Any]:
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            return payload
        break
    raise ValueError("json_object_not_found")


def _plex_snapshot() -> dict[str, Any]:
    helper_reason = "helper_unavailable"
    try:
        result = _run(["sudo", "-n", PLEX_SESSION_HELPER], timeout=5)
        if result.returncode == 0:
            payload = _last_json_line(result.stdout)
            if payload.get("session_check_ok"):
                return {
                    "state": "LIVE",
                    "method": "token_safe_helper",
                    "transcodes": _safe_int(payload.get("active_transcodes")),
                    "streams": _safe_int(payload.get("active_streams")),
                    "reason": None,
                }
            helper_reason = str(payload.get("session_check_reason") or "session_check_failed")[:80]
        else:
            helper_reason = f"helper_rc_{result.returncode}"
    except Exception as error:  # token-safe fallback intentionally remains available
        helper_reason = _safe_reason(error)

    try:
        result = _run(["ps", "-eo", "pid=,comm=,args="], timeout=3)
        if result.returncode != 0:
            raise RuntimeError(f"ps_rc_{result.returncode}")
        transcodes = sum(
            1
            for line in result.stdout.splitlines()
            if "plex transcoder" in line.lower()
        )
        return {
            "state": "PARTIAL",
            "method": "process_fallback",
            "transcodes": transcodes,
            "streams": None,
            "reason": helper_reason,
        }
    except Exception as error:
        return {
            "state": "OFFLINE",
            "method": "unavailable",
            "transcodes": 0,
            "streams": None,
            "reason": _safe_reason(error),
        }


def _qbit_snapshot() -> dict[str, Any]:
    headers = {"Referer": "http://127.0.0.1:8090"}
    try:
        torrents = _fetch_json(f"{QBIT_BASE}/torrents/info", headers=headers)
        transfer = _fetch_json(f"{QBIT_BASE}/transfer/info", headers=headers)
        if not isinstance(torrents, list) or not isinstance(transfer, dict):
            raise ValueError("invalid_qbittorrent_contract")
        state_counts = Counter(
            str(item.get("state") or "unknown")
            for item in torrents
            if isinstance(item, dict)
        )
        return {
            "state": "LIVE",
            "reason": None,
            "torrent_count": len(torrents),
            "state_counts": dict(sorted(state_counts.items())),
            "global_download_speed_bytes": _safe_int(transfer.get("dl_info_speed")),
            "_torrents": [item for item in torrents if isinstance(item, dict)],
        }
    except Exception as error:
        return {
            "state": "OFFLINE",
            "reason": _safe_reason(error),
            "torrent_count": None,
            "state_counts": {},
            "global_download_speed_bytes": 0,
            "_torrents": [],
        }


def _arr_api_key(container: str) -> str:
    result = _run(["docker", "exec", container, "cat", "/config/config.xml"], timeout=5)
    if result.returncode != 0:
        raise RuntimeError(f"docker_exec_rc_{result.returncode}")
    root = ET.fromstring(result.stdout)
    key = (root.findtext("ApiKey") or "").strip()
    if not key:
        raise RuntimeError("api_key_missing")
    return key


def _arr_snapshot(name: str, port: int, include_argument: str) -> dict[str, Any]:
    try:
        api_key = _arr_api_key(name)
        query = urllib.parse.urlencode(
            {
                "page": 1,
                "pageSize": 250,
                "sortKey": "timeleft",
                "sortDirection": "ascending",
                include_argument: "true",
            }
        )
        payload = _fetch_json(
            f"http://127.0.0.1:{port}/api/v3/queue?{query}",
            headers={"X-Api-Key": api_key},
            timeout=8,
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
            raise ValueError("invalid_arr_queue_contract")
        records = [item for item in payload["records"] if isinstance(item, dict)]
        total_records = _safe_int(payload.get("totalRecords"), len(records))
        state = "LIVE" if total_records <= len(records) else "PARTIAL"
        reason = None if state == "LIVE" else "queue_page_truncated"
        return {
            "state": state,
            "reason": reason,
            "queue_records": total_records,
            "_records": records,
        }
    except Exception as error:
        return {
            "state": "OFFLINE",
            "reason": _safe_reason(error),
            "queue_records": None,
            "_records": [],
        }


def _record_key(record: dict[str, Any]) -> str:
    download_id = str(record.get("downloadId") or "").strip().lower()
    if download_id:
        return download_id
    return "record:" + str(record.get("id") or record.get("title") or id(record))


def _format_rate(bytes_per_second: int) -> str:
    if bytes_per_second >= 1024 * 1024:
        return f"{bytes_per_second / (1024 * 1024):.1f} MB/S"
    if bytes_per_second >= 1024:
        return f"{bytes_per_second / 1024:.0f} KB/S"
    return f"{bytes_per_second} B/S"


def _display_contract(
    *,
    plex_transcodes: int,
    active_downloads: int,
    download_speed_bytes: int,
    stalled_downloads: int,
    import_pending: int,
    partial_sources: list[str],
) -> tuple[str, str, str, str]:
    activity_count = plex_transcodes + active_downloads
    attention_count = stalled_downloads + import_pending

    if attention_count:
        detail_parts = []
        if stalled_downloads:
            detail_parts.append(f"{stalled_downloads} STALLED")
        if import_pending:
            detail_parts.append(f"{import_pending} IMPORT PENDING")
        if active_downloads:
            detail_parts.append(f"DL {active_downloads}")
        if plex_transcodes:
            detail_parts.append(f"PLEX {plex_transcodes}")
        if partial_sources:
            detail_parts.append("SOURCE PARTIAL")
        return (
            "ATTENTION",
            "MEDIA ATTENTION",
            " · ".join(detail_parts),
            "Managed media work requires attention.",
        )

    if activity_count:
        detail_parts = []
        if plex_transcodes:
            detail_parts.append(f"PLEX {plex_transcodes}")
        if active_downloads:
            detail_parts.append(f"DOWNLOADS {active_downloads}")
        if active_downloads and download_speed_bytes:
            detail_parts.append(_format_rate(download_speed_bytes))
        if partial_sources:
            detail_parts.append("SOURCE PARTIAL")
        return (
            "ACTIVE",
            "MEDIA ACTIVE",
            " · ".join(detail_parts),
            "Media processing or downloads are active.",
        )

    if partial_sources:
        return (
            "PARTIAL",
            "MEDIA PARTIAL",
            "UNAVAILABLE · " + " · ".join(name.upper() for name in partial_sources),
            "One or more media sources are unavailable; idle is not asserted.",
        )

    return (
        "IDLE",
        "MEDIA IDLE",
        "NO TRANSCODES OR DOWNLOADS",
        "No Plex transcodes or qBittorrent downloads are active.",
    )


def _collect_uncached() -> dict[str, Any]:
    generated_at = _utc_now()

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="ma001") as executor:
        futures = {
            "plex": executor.submit(_plex_snapshot),
            "qbittorrent": executor.submit(_qbit_snapshot),
            "sonarr": executor.submit(_arr_snapshot, "sonarr", 8989, "includeUnknownSeriesItems"),
            "radarr": executor.submit(_arr_snapshot, "radarr", 7878, "includeUnknownMovieItems"),
        }
        snapshots = {name: future.result() for name, future in futures.items()}

    plex = snapshots["plex"]
    qbit = snapshots["qbittorrent"]
    arr_snapshots = {name: snapshots[name] for name in ("sonarr", "radarr")}

    download_owners: dict[str, set[str]] = defaultdict(set)
    import_pending_keys: dict[str, set[str]] = defaultdict(set)
    for app_name, snapshot in arr_snapshots.items():
        for record in snapshot.get("_records", []):
            record_key = _record_key(record)
            download_id = str(record.get("downloadId") or "").strip().lower()
            if download_id:
                download_owners[download_id].add(app_name)
            tracked_state = str(record.get("trackedDownloadState") or "").strip().lower()
            size_left = _safe_int(record.get("sizeleft"))
            if tracked_state == "importpending" and size_left == 0:
                import_pending_keys[app_name].add(record_key)

    active_torrents: list[dict[str, Any]] = []
    stalled_tracked_hashes: set[str] = set()
    active_by_owner: Counter[str] = Counter()
    for torrent in qbit.get("_torrents", []):
        state = str(torrent.get("state") or "")
        torrent_hash = str(torrent.get("hash") or "").strip().lower()
        owners = download_owners.get(torrent_hash, set())
        if state in ACTIVE_DOWNLOAD_STATES:
            active_torrents.append(torrent)
            if owners:
                for owner in owners:
                    active_by_owner[owner] += 1
            else:
                active_by_owner["unattributed"] += 1
        elif state == STALLED_DOWNLOAD_STATE and torrent_hash and owners:
            stalled_tracked_hashes.add(torrent_hash)

    active_downloads = len(active_torrents)
    download_speed_bytes = sum(_safe_int(item.get("dlspeed")) for item in active_torrents)
    plex_transcodes = _safe_int(plex.get("transcodes"))
    stalled_downloads = len(stalled_tracked_hashes)
    import_pending_by_app = {
        name: len(keys) for name, keys in sorted(import_pending_keys.items())
    }
    import_pending = sum(import_pending_by_app.values())

    for app_name, snapshot in arr_snapshots.items():
        snapshot["active_downloads"] = active_by_owner.get(app_name, 0)
        snapshot["import_pending"] = import_pending_by_app.get(app_name, 0)

    partial_sources = [
        name
        for name, snapshot in snapshots.items()
        if str(snapshot.get("state") or "OFFLINE") != "LIVE"
    ]
    posture, display_title, display_detail, summary = _display_contract(
        plex_transcodes=plex_transcodes,
        active_downloads=active_downloads,
        download_speed_bytes=download_speed_bytes,
        stalled_downloads=stalled_downloads,
        import_pending=import_pending,
        partial_sources=partial_sources,
    )

    items: list[dict[str, Any]] = []
    if stalled_downloads:
        items.append({
            "id": "arr_tracked_stalled_downloads",
            "label": "ARR-tracked downloads stalled",
            "severity": "watch",
            "count": stalled_downloads,
            "summary": "qBittorrent reports stalledDL while Sonarr or Radarr still tracks the request.",
        })
    for app_name, count in import_pending_by_app.items():
        if count:
            items.append({
                "id": f"{app_name}_import_pending",
                "label": f"{app_name.title()} imports pending",
                "severity": "watch",
                "count": count,
                "summary": "Completed queue items are waiting for import and are not counted as downloads.",
            })
    if plex_transcodes:
        items.append({
            "id": "plex_transcodes",
            "label": "Plex transcoding",
            "severity": "info",
            "count": plex_transcodes,
            "summary": "Plex has active video transcodes.",
        })
    if active_downloads:
        items.append({
            "id": "qbittorrent_downloads",
            "label": "qBittorrent downloads",
            "severity": "info",
            "count": active_downloads,
            "summary": "qBittorrent reports real active download states.",
        })

    public_sources: dict[str, Any] = {}
    for name, snapshot in snapshots.items():
        public_sources[name] = {
            key: value
            for key, value in snapshot.items()
            if not key.startswith("_")
        }
    public_sources["qbittorrent"]["active_downloads"] = active_downloads
    public_sources["qbittorrent"]["download_speed_bytes"] = download_speed_bytes
    public_sources["qbittorrent"]["active_by_owner"] = dict(sorted(active_by_owner.items()))

    activity_count = plex_transcodes + active_downloads
    attention_count = stalled_downloads + import_pending
    return {
        "schema_version": SCHEMA_VERSION,
        "contract": CONTRACT_ID,
        "system_id": "rackmain1-wall-ui",
        "generated_at": generated_at,
        "active": bool(activity_count or attention_count),
        "posture": posture,
        "summary": summary,
        "display": {
            "title": display_title,
            "detail": display_detail,
        },
        "primary": items[0] if items else None,
        "counts": {
            "active": activity_count + attention_count,
            "watch": attention_count,
            "info": activity_count,
            "plex_transcodes": plex_transcodes,
            "downloads": active_downloads,
            "stalled_downloads": stalled_downloads,
            "import_pending": import_pending,
            "partial_sources": len(partial_sources),
        },
        "activity": {
            "plex_transcodes": plex_transcodes,
            "downloads": active_downloads,
            "download_speed_bytes": download_speed_bytes,
        },
        "attention": {
            "stalled_downloads": stalled_downloads,
            "import_pending": import_pending,
            "import_pending_by_app": import_pending_by_app,
        },
        "sources": public_sources,
        "items": items[:12],
        "error": "source_partial" if partial_sources else None,
    }


def reset_cache() -> None:
    """Clear the short duplicate-request cache (used by tests and certification)."""
    global _CACHE_MONOTONIC, _CACHE_PAYLOAD
    with _CACHE_LOCK:
        _CACHE_MONOTONIC = 0.0
        _CACHE_PAYLOAD = None


def collect_media_activity(*, force: bool = False) -> dict[str, Any]:
    """Return a safe, grouped media activity snapshot."""
    global _CACHE_MONOTONIC, _CACHE_PAYLOAD
    now_monotonic = time.monotonic()
    with _CACHE_LOCK:
        if (
            not force
            and _CACHE_PAYLOAD is not None
            and now_monotonic - _CACHE_MONOTONIC < CACHE_SECONDS
        ):
            return copy.deepcopy(_CACHE_PAYLOAD)

        try:
            payload = _collect_uncached()
        except Exception as error:
            payload = {
                "schema_version": SCHEMA_VERSION,
                "contract": CONTRACT_ID,
                "system_id": "rackmain1-wall-ui",
                "generated_at": _utc_now(),
                "active": False,
                "posture": "PARTIAL",
                "summary": "Media activity collection failed; idle is not asserted.",
                "display": {
                    "title": "MEDIA PARTIAL",
                    "detail": "COLLECTOR UNAVAILABLE",
                },
                "primary": None,
                "counts": {
                    "active": 0,
                    "watch": 0,
                    "info": 0,
                    "plex_transcodes": 0,
                    "downloads": 0,
                    "stalled_downloads": 0,
                    "import_pending": 0,
                    "partial_sources": 4,
                },
                "activity": {
                    "plex_transcodes": 0,
                    "downloads": 0,
                    "download_speed_bytes": 0,
                },
                "attention": {
                    "stalled_downloads": 0,
                    "import_pending": 0,
                    "import_pending_by_app": {},
                },
                "sources": {},
                "items": [],
                "error": _safe_reason(error),
            }

        _CACHE_MONOTONIC = time.monotonic()
        _CACHE_PAYLOAD = copy.deepcopy(payload)
        return payload


if __name__ == "__main__":
    print(json.dumps(collect_media_activity(force=True), indent=2, sort_keys=True))
