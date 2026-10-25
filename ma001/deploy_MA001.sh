#!/usr/bin/env bash
set -Eeuo pipefail

PACKAGE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PAYLOAD="$PACKAGE/payload"
TOOLS="$PACKAGE/tools"
TESTS="$PACKAGE/tests"

TRACKER_DIR="/home/ap3x/scripts/rh_tracker"
TRACKER="$TRACKER_DIR/tracker_api.py"
MEDIA_MODULE="$TRACKER_DIR/media_activity.py"
BASE="/home/ap3x/stacks/media-automation/wall-ui"
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

BASELINE_TRACKER_SHA256="29f4b36a7eaf92e53375c4604389cf1046d462d7be4f1c804198eee2c0409cf1"
BASELINE_HTML_SHA256="603cfd9527c9192d098fd8971dbe4df0524f35442f2f79b0cdacef0d7a89bf1f"
BASELINE_JS_SHA256="52b1771b497cf0fa8a5ca1f12079076d9459c10b2c68b71bb588c92524fd440d"
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

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_ROOT="/home/ap3x/system_fix_backups"
BACKUP="$BACKUP_ROOT/media-activity-ma001-$STAMP"
WORK="$(mktemp -d /tmp/ap3x-ma001-deploy.XXXXXX)"
MUTATED=0

hash_of() {
  sha256sum "$1" | awk '{print $1}'
}

require_hash() {
  local path="$1"
  local expected="$2"
  local actual
  [[ -f "$path" ]] || { echo "FAIL: missing guarded file: $path"; exit 1; }
  actual="$(hash_of "$path")"
  [[ "$actual" == "$expected" ]] || {
    echo "FAIL: guarded file drifted: $path"
    echo "expected_sha256=$expected"
    echo "actual_sha256=$actual"
    exit 1
  }
}

atomic_install() {
  local source="$1"
  local destination="$2"
  local mode="$3"
  local temporary
  temporary="$(mktemp "${destination}.ma001.XXXXXX")"
  install -m "$mode" "$source" "$temporary"
  mv -- "$temporary" "$destination"
}

wait_for_api() {
  local attempt
  for attempt in $(seq 1 24); do
    if curl --fail --silent --show-error --max-time 3 \
      http://127.0.0.1:8000/api/ops/workloads \
      --output "$WORK/workloads-live.json" 2>/dev/null; then
      return 0
    fi
    sleep 1
  done
  return 1
}

cleanup_and_restore() {
  local rc=$?
  trap - EXIT
  if [[ "$rc" -ne 0 && "$MUTATED" == "1" && -d "$BACKUP" ]]; then
    echo
    echo "===== AUTOMATIC RESTORE ====="
    set +e
    atomic_install "$BACKUP/tracker_api.py" "$TRACKER" 0644
    tracker_restore_rc=$?
    atomic_install "$BACKUP/local-ops-canary.js" "$CORE_JS" 0644
    js_restore_rc=$?
    atomic_install "$BACKUP/local-ops-canary.html" "$HTML" 0644
    html_restore_rc=$?
    module_restore_rc=0
    if [[ -f "$MEDIA_MODULE" ]]; then
      if [[ "$(hash_of "$MEDIA_MODULE" 2>/dev/null)" == "$TARGET_MODULE_SHA256" ]]; then
        mv -- "$MEDIA_MODULE" "$BACKUP/media_activity.failed-install.py"
        module_restore_rc=$?
      else
        module_restore_rc=1
      fi
    fi
    sudo systemctl restart rh-tracker-api.service
    service_restore_rc=$?
    if [[ "$tracker_restore_rc" == "0" && "$js_restore_rc" == "0" && \
          "$html_restore_rc" == "0" && "$module_restore_rc" == "0" && \
          "$service_restore_rc" == "0" ]]; then
      echo "automatic_restore=PASS"
    else
      echo "automatic_restore=FAILED"
      echo "backup=$BACKUP"
    fi
    set -e
  fi
  case "$WORK" in
    /tmp/ap3x-ma001-deploy.*) rm -rf -- "$WORK" ;;
  esac
  exit "$rc"
}
trap cleanup_and_restore EXIT

echo "===== AP3X MEDIA ACTIVITY MA001 ====="
echo "package_revision=MA001R2"
echo "scope=tracker-workload-endpoint-and-local-ops-renderer"
echo "go2rtc_generic_watch=false"
echo "plex_transcodes=true"
echo "qbittorrent_state_detection=true"
echo "arr_attribution=true"
echo "credential_output=false"
echo "kiosk_restart_planned=false"

echo
echo "===== PACKAGE PREFLIGHT ====="
(cd "$PACKAGE" && sha256sum --check MANIFEST.sha256)
(cd "$PACKAGE" && sha256sum --check TARGETS.sha256)
(cd "$PACKAGE" && sha256sum --check BASELINE_GUARDS.sha256)
bash -n "$PACKAGE/deploy_MA001.sh" "$PACKAGE/rollback_MA001.sh" "$PACKAGE/certify_MA001.sh"
PYTHONPYCACHEPREFIX="$WORK/pycache" python3 -m py_compile \
  "$PAYLOAD/media_activity.py" "$PAYLOAD/tracker_api.py" "$TOOLS/patch_tracker.py"
node --check "$PAYLOAD/local-ops-canary.js"
PYTHONPYCACHEPREFIX="$WORK/pycache" python3 -m unittest discover -s "$TESTS" -p 'test_*.py' -v
node "$TESTS/test_frontend_contract.js"
echo "PASS: package hashes, syntax, and 19 offline tests"

echo
echo "===== LIVE BASELINE BOUNDARY ====="
require_hash "$CORE_CSS" "$CORE_CSS_SHA256"
require_hash "$ROTATION" "$ROTATION_SHA256"
require_hash "$LOCAL_COLLECTOR" "$LOCAL_COLLECTOR_SHA256"
require_hash "$AIR_JS" "$AIR_JS_SHA256"
require_hash "$AIR_CSS" "$AIR_CSS_SHA256"
require_hash "$AIR_COLLECTOR" "$AIR_COLLECTOR_SHA256"
require_hash "$AIR_SERVICE" "$AIR_SERVICE_SHA256"
require_hash "$AIR_TIMER" "$AIR_TIMER_SHA256"

tracker_hash="$(hash_of "$TRACKER")"
html_hash="$(hash_of "$HTML")"
js_hash="$(hash_of "$CORE_JS")"
mode=""
if [[ "$tracker_hash" == "$BASELINE_TRACKER_SHA256" && \
      "$html_hash" == "$BASELINE_HTML_SHA256" && \
      "$js_hash" == "$BASELINE_JS_SHA256" && ! -e "$MEDIA_MODULE" ]]; then
  mode="fresh"
  python3 "$TOOLS/patch_tracker.py" classify "$TRACKER" | grep -q '^state=baseline$'
elif [[ "$tracker_hash" == "$TARGET_TRACKER_SHA256" && \
        "$html_hash" == "$TARGET_HTML_SHA256" && \
        "$js_hash" == "$TARGET_JS_SHA256" && \
        -f "$MEDIA_MODULE" && "$(hash_of "$MEDIA_MODULE")" == "$TARGET_MODULE_SHA256" ]]; then
  mode="installed"
  python3 "$TOOLS/patch_tracker.py" classify "$TRACKER" | grep -q '^state=patched$'
else
  echo "FAIL: unsupported or partial MA001 state"
  echo "tracker_sha256=$tracker_hash"
  echo "local_ops_html_sha256=$html_hash"
  echo "local_ops_js_sha256=$js_hash"
  echo "media_module_present=$([[ -e "$MEDIA_MODULE" ]] && echo true || echo false)"
  exit 1
fi
echo "baseline_mode=$mode"
echo "PASS: AH001, AS001R1, LOC003, and WR003 guards"

if [[ "$mode" == "installed" ]]; then
  echo
  echo "===== IDEMPOTENT CERTIFICATION ====="
  "$PACKAGE/certify_MA001.sh"
  echo
  echo "result=ALREADY_INSTALLED"
  exit 0
fi

echo
echo "===== READ-ONLY SOURCE PREFLIGHT ====="
systemctl is-active --quiet rh-tracker-api.service
systemctl is-active --quiet local-ops-airspace-refresh.timer
PYTHONPATH="$PAYLOAD" python3 - "$WORK/source-probe.json" <<'PY'
import json
import sys
from pathlib import Path
from media_activity import collect_media_activity

payload = collect_media_activity(force=True)
Path(sys.argv[1]).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
assert payload.get("contract") == "MA001"
sources = payload.get("sources") or {}
for name in ("qbittorrent", "sonarr", "radarr"):
    assert sources.get(name, {}).get("state") in {"LIVE", "PARTIAL"}, f"{name} source unavailable"
assert sources.get("plex", {}).get("state") in {"LIVE", "PARTIAL"}, "Plex source unavailable"
counts = payload["counts"]
print(f"probe_posture={payload['posture']}")
print(f"probe_plex_transcodes={counts['plex_transcodes']}")
print(f"probe_active_downloads={counts['downloads']}")
print(f"probe_tracked_stalled_downloads={counts['stalled_downloads']}")
print(f"probe_import_pending={counts['import_pending']}")
for name in ("plex", "qbittorrent", "sonarr", "radarr"):
    print(f"probe_source_{name}={sources[name]['state']}")
PY
curl --fail --silent --show-error --max-time 10 \
  http://127.0.0.1:8000/api/ops/workloads --output "$WORK/legacy-workloads.json"
curl --fail --silent --show-error --max-time 10 \
  "http://127.0.0.1:3098/local-ops-canary.js?ma001-preflight=$STAMP" \
  --output "$WORK/served-baseline.js"
curl --fail --silent --show-error --max-time 10 \
  "http://127.0.0.1:3098/local-ops-canary.html?ma001-preflight=$STAMP" \
  --output "$WORK/served-baseline.html"
cmp --silent "$CORE_JS" "$WORK/served-baseline.js"
cmp --silent "$HTML" "$WORK/served-baseline.html"
echo "PASS: all four read-only sources and served baseline are available"

echo
echo "===== BACKUP ====="
[[ -d "$BACKUP_ROOT" && ! -L "$BACKUP_ROOT" ]] || {
  echo "FAIL: protected backup root is missing, not a directory, or a symlink: $BACKUP_ROOT"
  exit 1
}
[[ ! -e "$BACKUP" ]] || {
  echo "FAIL: timestamped backup destination already exists: $BACKUP"
  exit 1
}
sudo install -d -m 0750 -o ap3x -g ap3x "$BACKUP"
[[ -d "$BACKUP" && -O "$BACKUP" && -w "$BACKUP" ]] || {
  echo "FAIL: timestamped backup directory was not delegated safely to ap3x: $BACKUP"
  exit 1
}
install -m 0644 "$TRACKER" "$BACKUP/tracker_api.py"
install -m 0644 "$CORE_JS" "$BACKUP/local-ops-canary.js"
install -m 0644 "$HTML" "$BACKUP/local-ops-canary.html"
touch "$BACKUP/media_activity.was-absent"
(
  cd "$BACKUP"
  sha256sum tracker_api.py local-ops-canary.js local-ops-canary.html > BACKUP.sha256
)
printf '%s\n' \
  "core_css=$CORE_CSS_SHA256" \
  "rotation=$ROTATION_SHA256" \
  "local_collector=$LOCAL_COLLECTOR_SHA256" \
  "airspace_js=$AIR_JS_SHA256" \
  "airspace_css=$AIR_CSS_SHA256" \
  "airspace_collector=$AIR_COLLECTOR_SHA256" \
  "airspace_service=$AIR_SERVICE_SHA256" \
  "airspace_timer=$AIR_TIMER_SHA256" \
  > "$BACKUP/PRESERVED.sha256"
echo "backup=$BACKUP"

echo
echo "===== STAGE AND VERIFY TARGETS ====="
require_hash "$PAYLOAD/tracker_api.py" "$TARGET_TRACKER_SHA256"
require_hash "$PAYLOAD/media_activity.py" "$TARGET_MODULE_SHA256"
require_hash "$PAYLOAD/local-ops-canary.js" "$TARGET_JS_SHA256"
require_hash "$PAYLOAD/local-ops-canary.html" "$TARGET_HTML_SHA256"
python3 "$TOOLS/patch_tracker.py" apply "$TRACKER" \
  --output "$WORK/tracker_api.py" \
  --expect-input-sha256 "$BASELINE_TRACKER_SHA256" \
  --expect-output-sha256 "$TARGET_TRACKER_SHA256"
cmp --silent "$WORK/tracker_api.py" "$PAYLOAD/tracker_api.py"
echo "PASS: target tracker is the exact one-section rewrite of the live baseline"

echo
echo "===== ATOMIC BACKEND ACTIVATION ====="
MUTATED=1
atomic_install "$PAYLOAD/media_activity.py" "$MEDIA_MODULE" 0644
atomic_install "$PAYLOAD/tracker_api.py" "$TRACKER" 0644
require_hash "$MEDIA_MODULE" "$TARGET_MODULE_SHA256"
require_hash "$TRACKER" "$TARGET_TRACKER_SHA256"
sudo systemctl restart rh-tracker-api.service
wait_for_api
python3 - "$WORK/workloads-live.json" <<'PY'
import json
import sys
from pathlib import Path
payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert payload.get("schema_version") == "0.2"
assert payload.get("contract") == "MA001"
assert payload.get("display", {}).get("title") in {
    "MEDIA IDLE", "MEDIA ACTIVE", "MEDIA ATTENTION", "MEDIA PARTIAL"
}
sources = payload.get("sources") or {}
for name in ("plex", "qbittorrent", "sonarr", "radarr"):
    assert sources.get(name, {}).get("state") in {"LIVE", "PARTIAL"}, f"{name} unavailable in service context"
print(f"backend_posture={payload.get('posture')}")
print(f"backend_display={payload.get('display', {}).get('title')}")
for name in ("plex", "qbittorrent", "sonarr", "radarr"):
    print(f"backend_source_{name}={sources[name]['state']}")
PY
echo "PASS: MA001 endpoint is live"

echo
echo "===== ATOMIC LOCAL OPS ACTIVATION ====="
atomic_install "$PAYLOAD/local-ops-canary.js" "$CORE_JS" 0644
require_hash "$CORE_JS" "$TARGET_JS_SHA256"
curl --fail --silent --show-error --max-time 10 \
  "http://127.0.0.1:3098/local-ops-canary.js?v=ma001&stage=$STAMP" \
  --output "$WORK/served-target.js"
cmp --silent "$CORE_JS" "$WORK/served-target.js"
atomic_install "$PAYLOAD/local-ops-canary.html" "$HTML" 0644
require_hash "$HTML" "$TARGET_HTML_SHA256"
echo "PASS: Local Ops requests the MA001 renderer"

echo
echo "===== POST-INSTALL CERTIFICATION ====="
"$PACKAGE/certify_MA001.sh"

echo
echo "===== MA001 DEPLOYMENT COMPLETE ====="
echo "result=PASS"
echo "backup=$BACKUP"
echo "rollback_command=$PACKAGE/rollback_MA001.sh $BACKUP"
echo "tracker_service_restart_required=true"
echo "tracker_service_restart_completed=true"
echo "go2rtc_generic_watch_removed=true"
echo "plex_transcode_visibility=true"
echo "managed_download_visibility=true"
echo "as001_unchanged=true"
echo "ah001_preserved=true"
echo "rotation_controller_unchanged=true"
echo "kiosk_restart_required=false"
echo "activation=next_local_ops_rotation"
