#!/usr/bin/env python3
"""Deterministically replace only the legacy workload endpoint section."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


BASELINE_MARKER = "# ALPHA-G16A workload attribution endpoint"
TARGET_MARKER = "# MA001 media activity endpoint"
END_MARKER = "# ===== CAMERA_ALPHA_HEALTH_V1 ====="

TARGET_SECTION = '''# =============================================================================
# MA001 media activity endpoint
# Purpose:
#   Report Plex transcodes, real qBittorrent downloads, ARR-tracked stalled
#   downloads, and import backlogs without classifying Camera Alpha/go2rtc.
#
# Safety:
#   Read-only. Credentials remain internal to media_activity.py and are never
#   returned by the endpoint. Alpha scoring and threat state are unchanged.
# =============================================================================

@app.get("/api/ops/workloads")
def api_ops_workloads():
    from media_activity import collect_media_activity

    return collect_media_activity()

'''


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def classify(text: str) -> str:
    baseline_count = text.count(BASELINE_MARKER)
    target_count = text.count(TARGET_MARKER)
    route_count = text.count('@app.get("/api/ops/workloads")')
    function_count = text.count("def api_ops_workloads():")
    if route_count != 1 or function_count != 1:
        return "invalid"
    if baseline_count == 1 and target_count == 0:
        return "baseline"
    if baseline_count == 0 and target_count == 1:
        return "patched"
    return "invalid"


def apply_patch(text: str) -> str:
    if classify(text) != "baseline":
        raise ValueError("input is not the exact structural baseline state")
    marker_index = text.index(BASELINE_MARKER)
    start = text.rfind("# =============================================================================", 0, marker_index)
    end = text.index(END_MARKER, marker_index)
    if start < 0 or end <= start:
        raise ValueError("workload endpoint boundary not found")
    output = text[:start] + TARGET_SECTION + text[end:]
    if classify(output) != "patched":
        raise ValueError("generated target failed structural classification")
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("classify", "apply"))
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--expect-input-sha256")
    parser.add_argument("--expect-output-sha256")
    args = parser.parse_args()

    text = args.input.read_text(encoding="utf-8")
    if args.action == "classify":
        print(f"state={classify(text)}")
        print(f"sha256={digest(text)}")
        return 0

    if args.output is None:
        parser.error("apply requires --output")
    if args.expect_input_sha256 and digest(text) != args.expect_input_sha256:
        raise SystemExit("input hash does not match guarded baseline")
    output = apply_patch(text)
    if args.expect_output_sha256 and digest(output) != args.expect_output_sha256:
        raise SystemExit("output hash does not match guarded target")
    args.output.write_text(output, encoding="utf-8")
    print("state=patched")
    print(f"sha256={digest(output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
