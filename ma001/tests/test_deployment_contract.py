#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PACKAGE = Path(__file__).resolve().parents[1]
PAYLOAD = PACKAGE / "payload"
FIXTURES = PACKAGE / "tests" / "fixtures"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def constants(path: Path) -> dict[str, str]:
    source = path.read_text(encoding="utf-8")
    return dict(re.findall(r'^([A-Z][A-Z0-9_]+)="([0-9a-f]{64})"$', source, re.MULTILINE))


class DeploymentContractTests(unittest.TestCase):
    def test_deploy_hashes_match_exact_baseline_and_targets(self):
        values = constants(PACKAGE / "deploy_MA001.sh")
        self.assertEqual(values["BASELINE_TRACKER_SHA256"], sha256(FIXTURES / "tracker_api.baseline.py"))
        self.assertEqual(values["BASELINE_JS_SHA256"], sha256(FIXTURES / "local-ops-canary.ah001.js"))
        self.assertEqual(values["BASELINE_HTML_SHA256"], sha256(FIXTURES / "local-ops-canary.ah001.html"))
        self.assertEqual(values["TARGET_TRACKER_SHA256"], sha256(PAYLOAD / "tracker_api.py"))
        self.assertEqual(values["TARGET_MODULE_SHA256"], sha256(PAYLOAD / "media_activity.py"))
        self.assertEqual(values["TARGET_JS_SHA256"], sha256(PAYLOAD / "local-ops-canary.js"))
        self.assertEqual(values["TARGET_HTML_SHA256"], sha256(PAYLOAD / "local-ops-canary.html"))

    def test_certifier_and_rollback_target_hashes_match_deployer(self):
        deploy = constants(PACKAGE / "deploy_MA001.sh")
        certify = constants(PACKAGE / "certify_MA001.sh")
        rollback = constants(PACKAGE / "rollback_MA001.sh")
        for name in (
            "TARGET_TRACKER_SHA256",
            "TARGET_MODULE_SHA256",
            "TARGET_JS_SHA256",
            "TARGET_HTML_SHA256",
        ):
            self.assertEqual(certify[name], deploy[name])
            self.assertEqual(rollback[name], deploy[name])
        for name in ("BASELINE_TRACKER_SHA256", "BASELINE_JS_SHA256", "BASELINE_HTML_SHA256"):
            self.assertEqual(rollback[name], deploy[name])

    def test_transaction_has_restore_restart_and_exact_module_retirement(self):
        deploy = (PACKAGE / "deploy_MA001.sh").read_text(encoding="utf-8")
        rollback = (PACKAGE / "rollback_MA001.sh").read_text(encoding="utf-8")
        self.assertIn("cleanup_and_restore", deploy)
        self.assertIn('sudo systemctl restart rh-tracker-api.service', deploy)
        self.assertIn('mv -- "$MEDIA_MODULE" "$BACKUP/media_activity.failed-install.py"', deploy)
        self.assertIn("cleanup_and_reapply", rollback)
        self.assertIn('require_hash "$MEDIA_MODULE" "$TARGET_MODULE_SHA256"', rollback)
        self.assertIn('mv -- "$MEDIA_MODULE" "$REMOVED_COPY"', rollback)
        self.assertNotIn("rm -f -- \"$MEDIA_MODULE\"", deploy + rollback)

    def test_backup_creation_delegates_only_the_timestamped_leaf(self):
        deploy = (PACKAGE / "deploy_MA001.sh").read_text(encoding="utf-8")
        self.assertIn('BACKUP_ROOT="/home/ap3x/system_fix_backups"', deploy)
        self.assertIn('BACKUP="$BACKUP_ROOT/media-activity-ma001-$STAMP"', deploy)
        self.assertIn('[[ -d "$BACKUP_ROOT" && ! -L "$BACKUP_ROOT" ]]', deploy)
        self.assertIn('sudo install -d -m 0750 -o ap3x -g ap3x "$BACKUP"', deploy)
        self.assertIn('[[ -d "$BACKUP" && -O "$BACKUP" && -w "$BACKUP" ]]', deploy)
        self.assertNotIn('mkdir -p "$BACKUP"', deploy)
        self.assertNotRegex(deploy, r'(?:chown|chmod)[^\n]*\$BACKUP_ROOT')

    def test_certifier_accepts_display_title_but_rejects_private_title_keys(self):
        certifier = (PACKAGE / "certify_MA001.sh").read_text(encoding="utf-8")
        match = re.search(
            r'python3 - "\$WORK/workloads\.json" <<\'PY\'\n(?P<code>.*?)\nPY\n',
            certifier,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        validator = match.group("code")
        payload = {
            "schema_version": "0.2",
            "contract": "MA001",
            "posture": "ATTENTION",
            "display": {"title": "MEDIA ATTENTION", "detail": "1 STALLED"},
            "counts": {
                "plex_transcodes": 0,
                "downloads": 0,
                "stalled_downloads": 1,
                "import_pending": 0,
                "partial_sources": 0,
            },
            "sources": {
                "plex": {"state": "LIVE"},
                "qbittorrent": {"state": "LIVE"},
                "sonarr": {"state": "LIVE"},
                "radarr": {"state": "LIVE"},
            },
            "items": [],
        }

        with tempfile.TemporaryDirectory() as temporary:
            snapshot = Path(temporary) / "workloads.json"
            snapshot.write_text(json.dumps(payload), encoding="utf-8")
            valid = subprocess.run(
                [sys.executable, "-c", validator, str(snapshot)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(valid.returncode, 0, valid.stderr)

            payload["items"] = [{"title": "Private media title"}]
            snapshot.write_text(json.dumps(payload), encoding="utf-8")
            private_title = subprocess.run(
                [sys.executable, "-c", validator, str(snapshot)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(private_title.returncode, 0)
            self.assertIn("forbidden public key at items.0.title", private_title.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
