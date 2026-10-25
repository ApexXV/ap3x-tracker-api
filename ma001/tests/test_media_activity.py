#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest import mock


PACKAGE = Path(__file__).resolve().parents[1]
MODULE_PATH = PACKAGE / "payload" / "media_activity.py"
SPEC = importlib.util.spec_from_file_location("ma001_media_activity", MODULE_PATH)
assert SPEC and SPEC.loader
media_activity = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = media_activity
SPEC.loader.exec_module(media_activity)


def plex(transcodes=0, state="LIVE"):
    return {
        "state": state,
        "method": "fixture",
        "transcodes": transcodes,
        "streams": transcodes,
        "reason": None if state == "LIVE" else "fixture_partial",
    }


def qbit(torrents=None, state="LIVE"):
    torrents = list(torrents or [])
    return {
        "state": state,
        "reason": None if state == "LIVE" else "fixture_partial",
        "torrent_count": len(torrents) if state == "LIVE" else None,
        "state_counts": {},
        "global_download_speed_bytes": 0,
        "_torrents": torrents,
    }


def arr(records=None, state="LIVE"):
    records = list(records or [])
    return {
        "state": state,
        "reason": None if state == "LIVE" else "fixture_partial",
        "queue_records": len(records) if state == "LIVE" else None,
        "_records": records,
    }


class MediaActivityContractTests(unittest.TestCase):
    def collect(self, *, plex_snapshot=None, qbit_snapshot=None, sonarr=None, radarr=None):
        with (
            mock.patch.object(media_activity, "_plex_snapshot", return_value=plex_snapshot or plex()),
            mock.patch.object(media_activity, "_qbit_snapshot", return_value=qbit_snapshot or qbit()),
            mock.patch.object(
                media_activity,
                "_arr_snapshot",
                side_effect=[sonarr or arr(), radarr or arr()],
            ),
        ):
            return media_activity._collect_uncached()

    def test_idle_ignores_paused_queued_completed_seeding_and_missing(self):
        torrents = [
            {"hash": "a", "state": "pausedDL", "dlspeed": 0},
            {"hash": "b", "state": "queuedDL", "dlspeed": 0},
            {"hash": "c", "state": "pausedUP", "dlspeed": 0},
            {"hash": "d", "state": "uploading", "dlspeed": 0},
            {"hash": "e", "state": "missingFiles", "dlspeed": 0},
        ]
        payload = self.collect(qbit_snapshot=qbit(torrents))
        self.assertEqual(payload["posture"], "IDLE")
        self.assertFalse(payload["active"])
        self.assertEqual(payload["display"]["title"], "MEDIA IDLE")
        self.assertEqual(payload["counts"]["downloads"], 0)

    def test_real_download_states_and_plex_are_activity(self):
        torrents = [
            {"hash": "sonarr-hash", "state": "downloading", "dlspeed": 2_000_000},
            {"hash": "manual-hash", "state": "forcedDL", "dlspeed": 1_000_000},
            {"hash": "meta-hash", "state": "metaDL", "dlspeed": 0},
        ]
        sonarr_records = [{"downloadId": "SONARR-HASH", "trackedDownloadState": "downloading", "sizeleft": 10}]
        payload = self.collect(
            plex_snapshot=plex(1),
            qbit_snapshot=qbit(torrents),
            sonarr=arr(sonarr_records),
        )
        self.assertEqual(payload["posture"], "ACTIVE")
        self.assertEqual(payload["counts"]["plex_transcodes"], 1)
        self.assertEqual(payload["counts"]["downloads"], 3)
        self.assertEqual(payload["activity"]["download_speed_bytes"], 3_000_000)
        self.assertEqual(payload["sources"]["qbittorrent"]["active_by_owner"]["sonarr"], 1)
        self.assertEqual(payload["sources"]["qbittorrent"]["active_by_owner"]["unattributed"], 2)
        self.assertEqual(payload["display"]["title"], "MEDIA ACTIVE")

    def test_only_arr_tracked_stalled_downloads_raise_attention(self):
        torrents = [
            {"hash": "tracked", "state": "stalledDL", "dlspeed": 0},
            {"hash": "abandoned", "state": "stalledDL", "dlspeed": 0},
        ]
        radarr_records = [{"downloadId": "TRACKED", "trackedDownloadState": "downloading", "sizeleft": 99}]
        payload = self.collect(qbit_snapshot=qbit(torrents), radarr=arr(radarr_records))
        self.assertEqual(payload["posture"], "ATTENTION")
        self.assertEqual(payload["counts"]["stalled_downloads"], 1)
        self.assertEqual(payload["display"]["detail"], "1 STALLED")

    def test_import_pending_is_deduplicated_and_not_called_downloading(self):
        records = [
            {"id": 1, "downloadId": "same", "trackedDownloadState": "importPending", "sizeleft": 0},
            {"id": 2, "downloadId": "SAME", "trackedDownloadState": "importPending", "sizeleft": 0},
            {"id": 3, "downloadId": "other", "trackedDownloadState": "importPending", "sizeleft": 0},
        ]
        payload = self.collect(sonarr=arr(records))
        self.assertEqual(payload["counts"]["downloads"], 0)
        self.assertEqual(payload["counts"]["import_pending"], 2)
        self.assertEqual(payload["attention"]["import_pending_by_app"], {"sonarr": 2})
        self.assertIn("2 IMPORT PENDING", payload["display"]["detail"])

    def test_partial_source_never_claims_idle(self):
        payload = self.collect(plex_snapshot=plex(0, "PARTIAL"))
        self.assertEqual(payload["posture"], "PARTIAL")
        self.assertEqual(payload["display"]["title"], "MEDIA PARTIAL")
        self.assertIn("PLEX", payload["display"]["detail"])
        self.assertEqual(payload["error"], "source_partial")

    def test_attention_precedes_activity_but_preserves_activity_counts(self):
        torrents = [
            {"hash": "active", "state": "downloading", "dlspeed": 500_000},
            {"hash": "stalled", "state": "stalledDL", "dlspeed": 0},
        ]
        records = [
            {"downloadId": "active", "trackedDownloadState": "downloading", "sizeleft": 50},
            {"downloadId": "stalled", "trackedDownloadState": "downloading", "sizeleft": 50},
        ]
        payload = self.collect(
            plex_snapshot=plex(1),
            qbit_snapshot=qbit(torrents),
            radarr=arr(records),
        )
        self.assertEqual(payload["posture"], "ATTENTION")
        self.assertEqual(payload["counts"]["downloads"], 1)
        self.assertEqual(payload["counts"]["plex_transcodes"], 1)
        self.assertIn("DL 1", payload["display"]["detail"])
        self.assertIn("PLEX 1", payload["display"]["detail"])

    def test_public_contract_contains_no_hashes_titles_or_credentials(self):
        secret = "0123456789abcdef0123456789abcdef"
        torrents = [{"hash": secret, "name": "Private Title", "state": "downloading", "dlspeed": 1}]
        records = [{"downloadId": secret, "title": "Private Title", "trackedDownloadState": "downloading", "sizeleft": 1}]
        payload = self.collect(qbit_snapshot=qbit(torrents), sonarr=arr(records))
        rendered = json.dumps(payload)
        self.assertNotIn(secret, rendered)
        self.assertNotIn("Private Title", rendered)
        self.assertNotIn("ApiKey", rendered)

    def test_unexpected_collector_failure_returns_partial_contract(self):
        media_activity.reset_cache()
        with mock.patch.object(media_activity, "_collect_uncached", side_effect=RuntimeError("fixture")):
            payload = media_activity.collect_media_activity(force=True)
        self.assertEqual(payload["contract"], "MA001")
        self.assertEqual(payload["posture"], "PARTIAL")
        self.assertFalse(payload["active"])
        self.assertNotEqual(payload["display"]["title"], "MEDIA IDLE")


if __name__ == "__main__":
    unittest.main(verbosity=2)
