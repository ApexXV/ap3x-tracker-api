#!/usr/bin/env bash
set -Eeuo pipefail

PACKAGE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BASE="/home/ap3x/stacks/media-automation/wall-ui"
TRACKER_DIR="/home/ap3x/scripts/rh_tracker"

TRACKER="$TRACKER_DIR/tracker_api.py"
MEDIA_MODULE="$TRACKER_DIR/media_activity.py"
HTML="$BASE/local-ops-canary.html"
CORE_JS="$BASE/local-ops-canary.js"
CORE_CSS="$BASE/local-ops-canary.css"
ROTATION="$BASE/rotation-canary.html"
LOCAL_COLLECTOR="/home/ap3x/scripts/local_ops_map/local_ops_collect.py"
AIR_ROOT="/home/ap3x/scripts/local_ops_airspace"
AIR_COLLECTOR="$AIR_ROOT/airspace_collect.py"
AIR_JS="$BASE/local-ops-airspace.js"
AIR_CSS="$BASE/local-ops-airspace.css"
AIR_SERVICE="/etc/systemd/system/local-ops-airspace-refresh.service"
AIR_TIMER="/etc/systemd/system/local-ops-airspace-refresh.timer"

TARGET_TRACKER_SHA256="c4dbd89261f6d3876d0962d355895f010dfdbe5c3a80ed1c2433fc344137dd0e"
TARGET_MODULE_SHA256="31bf2ab0e9bc71fbb388ff6c410a43af43804c171aa4b004bd955675ddfdca09"
TARGET_HTML_SHA256="a302be6e740115d2223405cea8589992e32706a646bcdb6bd5d75722affdedfe"
TARGET_JS_SHA256="2901eb10a00011ee68c96f093569278d493009438be9dae89c4b8eaf4f2126cb"
CORE_CSS_SHA256="167325e14252920d4522f91855621dc8777b9088ae64147fecee427eb950b21d"
ROTATION_SHA256="e7fe64eacb1ff72b46ef873baf548383bc9efdd102be77ef0aa4355215ee04c6"
LOCAL_COLLECTOR_SHA256="39e58d9b0551f7d4ae719f7fd2c8acf7c81053d19ab397cd0f7f297c2bb1ffaf"
AIR_JS_SHA256="54823e0037851679737eb896f99625687ed67a1c72e6a9885f48313b25113ea1"
AIR_CSS_SHA256="5c270a86a3e3dd0bd76db30f4816c7a0eedd3c8d42d94f76845ecb3423c9c45b"
AIR_COLLECTOR_SHA256="d24871bf5d4f00167f2e534e11f80129f783601f87527fc8a125d53520114db3"
AIR_SERVICE_SHA256="33913c0815c70c41bb9efc112a86254b3d78020fd359edce868d5e8cc77046ed"
AIR_TIMER_SHA256="ab4a273daa4741b08cf534df0738257d1d32aa85514dc92e6d39333537dcac2d"

WORK="$(mktemp -d /tmp/ap3x-ma001-certify.XXXXXX)"
cleanup() {
  case "$WORK" in
    /tmp/ap3x-ma001-certify.*) rm -rf -- "$WORK" ;;
  esac
}
trap cleanup EXIT

require_hash() {
  local path="$1"
  local expected="$2"
  local actual
  [[ -f "$path" ]] || { echo "FAIL: missing file: $path"; exit 1; }
  actual="$(sha256sum "$path" | awk '{print $1}')"
  [[ "$actual" == "$expected" ]] || {
    echo "FAIL: hash mismatch: $path"
    echo "expected_sha256=$expected"
    echo "actual_sha256=$actual"
    exit 1
  }
}

echo "===== MA001 SERVE CERTIFICATION ====="

require_hash "$TRACKER" "$TARGET_TRACKER_SHA256"
require_hash "$MEDIA_MODULE" "$TARGET_MODULE_SHA256"
require_hash "$HTML" "$TARGET_HTML_SHA256"
require_hash "$CORE_JS" "$TARGET_JS_SHA256"
require_hash "$CORE_CSS" "$CORE_CSS_SHA256"
require_hash "$ROTATION" "$ROTATION_SHA256"
require_hash "$LOCAL_COLLECTOR" "$LOCAL_COLLECTOR_SHA256"
require_hash "$AIR_JS" "$AIR_JS_SHA256"
require_hash "$AIR_CSS" "$AIR_CSS_SHA256"
require_hash "$AIR_COLLECTOR" "$AIR_COLLECTOR_SHA256"
require_hash "$AIR_SERVICE" "$AIR_SERVICE_SHA256"
require_hash "$AIR_TIMER" "$AIR_TIMER_SHA256"

systemctl is-active --quiet rh-tracker-api.service
systemctl is-active --quiet local-ops-airspace-refresh.timer

curl --fail --silent --show-error --max-time 12 \
  "http://127.0.0.1:8000/api/ops/workloads?ma001-certify=$(date +%s)" \
  --output "$WORK/workloads.json"
curl --fail --silent --show-error --max-time 10 \
  "http://127.0.0.1:3098/local-ops-canary.js?v=ma001&certify=$(date +%s)" \
  --output "$WORK/served.js"
curl --fail --silent --show-error --max-time 10 \
  "http://127.0.0.1:3098/local-ops-canary.html?ma001-certify=$(date +%s)" \
  --output "$WORK/served.html"
cmp --silent "$CORE_JS" "$WORK/served.js"
cmp --silent "$HTML" "$WORK/served.html"

python3 - "$WORK/workloads.json" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert payload.get("schema_version") == "0.2"
assert payload.get("contract") == "MA001"
assert payload.get("posture") in {"IDLE", "ACTIVE", "ATTENTION", "PARTIAL"}
display = payload.get("display")
assert isinstance(display, dict)
assert display.get("title") in {"MEDIA IDLE", "MEDIA ACTIVE", "MEDIA ATTENTION", "MEDIA PARTIAL"}
assert isinstance(display.get("detail"), str) and display["detail"]
counts = payload.get("counts")
assert isinstance(counts, dict)
for field in ("plex_transcodes", "downloads", "stalled_downloads", "import_pending", "partial_sources"):
    assert isinstance(counts.get(field), int) and counts[field] >= 0
sources = payload.get("sources")
assert isinstance(sources, dict)
for name in ("plex", "qbittorrent", "sonarr", "radarr"):
    assert name in sources
    assert sources[name].get("state") in {"LIVE", "PARTIAL", "OFFLINE"}

forbidden_keys = {"hash", "downloadId", "title", "api_key", "ApiKey", "token", "args", "pid", "command"}
allowed_key_paths = {("display", "title")}
def walk(value, path=()):
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = path + (str(key),)
            if key in forbidden_keys and child_path not in allowed_key_paths:
                raise AssertionError(f"forbidden public key at {'.'.join(child_path)}")
            walk(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            walk(child, path + (str(index),))
walk(payload)

print(f"media_posture={payload['posture']}")
print(f"media_title={display['title']}")
print(f"media_detail={display['detail']}")
print(f"plex_transcodes={counts['plex_transcodes']}")
print(f"active_downloads={counts['downloads']}")
print(f"tracked_stalled_downloads={counts['stalled_downloads']}")
print(f"import_pending={counts['import_pending']}")
print(f"partial_sources={counts['partial_sources']}")
for name in ("plex", "qbittorrent", "sonarr", "radarr"):
    print(f"source_{name}={sources[name]['state']}")
PY

grep -q '/local-ops-canary.js?v=ma001' "$HTML"
grep -q '/local-ops-airspace.js?v=as001' "$HTML"
grep -q '/local-ops-airspace.css?v=as001' "$HTML"
grep -q '/finance-canary.html' "$ROTATION"
grep -q '/markets-canary.html?release=MC003' "$ROTATION"
grep -q '/local-ops-canary.html?release=LOC003' "$ROTATION"
grep -q 'searchParams.set("wallv"' "$ROTATION"

echo "go2rtc_generic_watch_removed=true"
echo "plex_transcode_visibility=true"
echo "managed_download_visibility=true"
echo "stale_downloads_are_attention_not_activity=true"
echo "as001_unchanged=true"
echo "ah001_alert_header_preserved=true"
echo "rotation_controller_unchanged=true"
echo "kiosk_restart_required=false"
echo "activation=next_local_ops_rotation"
echo "result=PASS"
