#!/usr/bin/env python3

from __future__ import annotations

import difflib
import importlib.util
import re
import sys
import unittest
from pathlib import Path


PACKAGE = Path(__file__).resolve().parents[1]
PAYLOAD = PACKAGE / "payload"
FIXTURES = PACKAGE / "tests" / "fixtures"
TOOLS = PACKAGE / "tools"

SPEC = importlib.util.spec_from_file_location("ma001_patch_tracker", TOOLS / "patch_tracker.py")
assert SPEC and SPEC.loader
patch_tracker = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = patch_tracker
SPEC.loader.exec_module(patch_tracker)


def function_span(source: str, function_name: str) -> tuple[int, int]:
    pattern = re.compile(rf"^  function {re.escape(function_name)}\([^\n]*\) \{{", re.MULTILINE)
    match = pattern.search(source)
    if not match:
        raise AssertionError(f"function not found: {function_name}")
    start = match.start()
    depth = 0
    in_string = None
    escaped = False
    for index in range(match.start(), len(source)):
        char = source[index]
        if escaped:
            escaped = False
            continue
        if char == "\\" and in_string:
            escaped = True
            continue
        if in_string:
            if char == in_string:
                in_string = None
            continue
        if char in {'"', "'", "`"}:
            in_string = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return start, index + 1
    raise AssertionError(f"unterminated function: {function_name}")


class StaticContractTests(unittest.TestCase):
    def test_tracker_target_is_exact_single_section_rewrite(self):
        baseline = (FIXTURES / "tracker_api.baseline.py").read_text(encoding="utf-8")
        target = (PAYLOAD / "tracker_api.py").read_text(encoding="utf-8")
        self.assertEqual(patch_tracker.classify(baseline), "baseline")
        self.assertEqual(patch_tracker.classify(target), "patched")
        self.assertEqual(patch_tracker.apply_patch(baseline), target)
        section = target[target.index(patch_tracker.TARGET_MARKER):target.index(patch_tracker.END_MARKER)]
        self.assertNotIn("ffmpeg", section.lower())
        self.assertNotIn("min_cpu", section)
        self.assertIn("from media_activity import collect_media_activity", section)

    def test_frontend_delta_is_only_media_renderer(self):
        baseline = (FIXTURES / "local-ops-canary.ah001.js").read_text(encoding="utf-8")
        target = (PAYLOAD / "local-ops-canary.js").read_text(encoding="utf-8")
        baseline_span = function_span(baseline, "renderWorkloads")
        target_span = function_span(target, "renderWorkloads")
        baseline_without = baseline[:baseline_span[0]] + baseline[baseline_span[1]:]
        target_without = target[:target_span[0]] + target[target_span[1]:]
        self.assertEqual(baseline_without, target_without)
        self.assertNotEqual(baseline[baseline_span[0]:baseline_span[1]], target[target_span[0]:target_span[1]])

    def test_html_delta_is_only_cache_token(self):
        baseline = (FIXTURES / "local-ops-canary.ah001.html").read_text(encoding="utf-8")
        target = (PAYLOAD / "local-ops-canary.html").read_text(encoding="utf-8")
        expected = baseline.replace(
            '/local-ops-canary.js?v=ah001',
            '/local-ops-canary.js?v=ma001',
        )
        self.assertEqual(target, expected)
        delta = list(difflib.unified_diff(baseline.splitlines(), target.splitlines()))
        changed = [line for line in delta if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))]
        self.assertEqual(len(changed), 2)

    def test_media_module_has_no_write_or_control_actions(self):
        source = (PAYLOAD / "media_activity.py").read_text(encoding="utf-8")
        forbidden = (
            "torrents/delete",
            "torrents/pause",
            "torrents/resume",
            "queue/grab",
            "queue/delete",
            "systemctl",
            "docker restart",
            "PlexOnlineToken",
        )
        for token in forbidden:
            self.assertNotIn(token, source)
        self.assertIn('ACTIVE_DOWNLOAD_STATES = frozenset({"downloading", "forcedDL", "metaDL"})', source)
        self.assertIn('STALLED_DOWNLOAD_STATE = "stalledDL"', source)

    def test_airspace_and_rotation_contracts_remain_present(self):
        js = (PAYLOAD / "local-ops-canary.js").read_text(encoding="utf-8")
        html = (PAYLOAD / "local-ops-canary.html").read_text(encoding="utf-8")
        self.assertIn('src="/local-ops-airspace.js?v=as001"', html)
        self.assertIn('href="/local-ops-airspace.css?v=as001"', html)
        self.assertIn("renderMapContract(contract, priority)", js)
        self.assertIn("renderWeather(data.opsBrief || {}, contract)", js)


if __name__ == "__main__":
    unittest.main(verbosity=2)
