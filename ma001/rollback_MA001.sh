#!/usr/bin/env bash
set -Eeuo pipefail

PACKAGE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PAYLOAD="$PACKAGE/payload"

TRACKER_DIR="/home/ap3x/scripts/rh_tracker"
TRACKER="$TRACKER_DIR/tracker_api.py"
MEDIA_MODULE="$TRACKER_DIR/media_activity.py"
BASE="/home/ap3x/stacks/media-automation/wall-ui"
HTML="$BASE/local-ops-canary.html"
CORE_JS="$BASE/local-ops-canary.js"

BASELINE_TRACKER_SHA256="29f4b36a7eaf92e53375c4604389cf1046d462d7be4f1c804198eee2c0409cf1"
BASELINE_HTML_SHA256="603cfd9527c9192d098fd8971dbe4df0524f35442f2f79b0cdacef0d7a89bf1f"
BASELINE_JS_SHA256="52b1771b497cf0fa8a5ca1f12079076d9459c10b2c68b71bb588c92524fd440d"
TARGET_TRACKER_SHA256="c4dbd89261f6d3876d0962d355895f010dfdbe5c3a80ed1c2433fc344137dd0e"
TARGET_MODULE_SHA256="31bf2ab0e9bc71fbb388ff6c410a43af43804c171aa4b004bd955675ddfdca09"
TARGET_HTML_SHA256="a302be6e740115d2223405cea8589992e32706a646bcdb6bd5d75722affdedfe"
TARGET_JS_SHA256="2901eb10a00011ee68c96f093569278d493009438be9dae89c4b8eaf4f2126cb"

[[ $# -eq 1 ]] || {
  echo "usage: $0 /home/ap3x/system_fix_backups/media-activity-ma001-TIMESTAMP"
  exit 2
}

BACKUP="$(realpath -e -- "$1")"
case "$BACKUP" in
  /home/ap3x/system_fix_backups/media-activity-ma001-*) ;;
  *) echo "FAIL: rollback backup is outside the MA001 backup boundary"; exit 1 ;;
esac

WORK="$(mktemp -d /tmp/ap3x-ma001-rollback.XXXXXX)"
MUTATED=0

hash_of() {
  sha256sum "$1" | awk '{print $1}'
}

require_hash() {
  local path="$1"
  local expected="$2"
  local actual
  [[ -f "$path" ]] || { echo "FAIL: missing file: $path"; exit 1; }
  actual="$(hash_of "$path")"
  [[ "$actual" == "$expected" ]] || {
    echo "FAIL: hash mismatch: $path"
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
  temporary="$(mktemp "${destination}.ma001-rollback.XXXXXX")"
  install -m "$mode" "$source" "$temporary"
  mv -- "$temporary" "$destination"
}

wait_for_api() {
  local attempt
  for attempt in $(seq 1 24); do
    if curl --fail --silent --show-error --max-time 3 \
      http://127.0.0.1:8000/api/ops/workloads \
      --output "$WORK/workloads.json" 2>/dev/null; then
      return 0
    fi
    sleep 1
  done
  return 1
}

cleanup_and_reapply() {
  local rc=$?
  trap - EXIT
  if [[ "$rc" -ne 0 && "$MUTATED" == "1" ]]; then
    echo
    echo "===== AUTOMATIC MA001 REAPPLY ====="
    set +e
    atomic_install "$PAYLOAD/media_activity.py" "$MEDIA_MODULE" 0644
    module_rc=$?
    atomic_install "$PAYLOAD/tracker_api.py" "$TRACKER" 0644
    tracker_rc=$?
    atomic_install "$PAYLOAD/local-ops-canary.js" "$CORE_JS" 0644
    js_rc=$?
    atomic_install "$PAYLOAD/local-ops-canary.html" "$HTML" 0644
    html_rc=$?
    sudo systemctl restart rh-tracker-api.service
    service_rc=$?
    if [[ "$module_rc" == "0" && "$tracker_rc" == "0" && \
          "$js_rc" == "0" && "$html_rc" == "0" && "$service_rc" == "0" ]]; then
      echo "automatic_reapply=PASS"
    else
      echo "automatic_reapply=FAILED"
      echo "package=$PACKAGE"
    fi
    set -e
  fi
  case "$WORK" in
    /tmp/ap3x-ma001-rollback.*) rm -rf -- "$WORK" ;;
  esac
  exit "$rc"
}
trap cleanup_and_reapply EXIT

echo "===== AP3X MEDIA ACTIVITY MA001 ROLLBACK ====="
echo "backup=$BACKUP"

echo
echo "===== TARGET AND BACKUP GUARDS ====="
require_hash "$TRACKER" "$TARGET_TRACKER_SHA256"
require_hash "$MEDIA_MODULE" "$TARGET_MODULE_SHA256"
require_hash "$HTML" "$TARGET_HTML_SHA256"
require_hash "$CORE_JS" "$TARGET_JS_SHA256"
(cd "$BACKUP" && sha256sum --check BACKUP.sha256)
require_hash "$BACKUP/tracker_api.py" "$BASELINE_TRACKER_SHA256"
require_hash "$BACKUP/local-ops-canary.html" "$BASELINE_HTML_SHA256"
require_hash "$BACKUP/local-ops-canary.js" "$BASELINE_JS_SHA256"
[[ -f "$BACKUP/media_activity.was-absent" ]] || {
  echo "FAIL: backup does not certify that media_activity.py was absent"
  exit 1
}
echo "PASS: exact MA001 target and baseline backup"

echo
echo "===== RESTORE BACKEND ====="
MUTATED=1
atomic_install "$BACKUP/tracker_api.py" "$TRACKER" 0644
require_hash "$TRACKER" "$BASELINE_TRACKER_SHA256"
sudo systemctl restart rh-tracker-api.service
wait_for_api
python3 - "$WORK/workloads.json" <<'PY'
import json
import sys
from pathlib import Path
payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert payload.get("contract") != "MA001"
assert payload.get("schema_version") == "0.1"
print("legacy_workload_contract=RESTORED")
PY
echo "PASS: legacy endpoint restored"

echo
echo "===== RESTORE LOCAL OPS RENDERER ====="
atomic_install "$BACKUP/local-ops-canary.js" "$CORE_JS" 0644
atomic_install "$BACKUP/local-ops-canary.html" "$HTML" 0644
require_hash "$CORE_JS" "$BASELINE_JS_SHA256"
require_hash "$HTML" "$BASELINE_HTML_SHA256"
echo "PASS: AH001 renderer and cache token restored"

echo
echo "===== RETIRE MA001 MODULE RECOVERABLY ====="
REMOVED_COPY="$BACKUP/media_activity.ma001-removed.py"
[[ ! -e "$REMOVED_COPY" ]] || {
  echo "FAIL: rollback destination already exists: $REMOVED_COPY"
  exit 1
}
require_hash "$MEDIA_MODULE" "$TARGET_MODULE_SHA256"
mv -- "$MEDIA_MODULE" "$REMOVED_COPY"
[[ ! -e "$MEDIA_MODULE" ]]
require_hash "$REMOVED_COPY" "$TARGET_MODULE_SHA256"
echo "removed_module_copy=$REMOVED_COPY"

echo
echo "===== ROLLBACK SERVE CERTIFICATION ====="
curl --fail --silent --show-error --max-time 10 \
  "http://127.0.0.1:3098/local-ops-canary.js?v=ah001&rollback=$(date +%s)" \
  --output "$WORK/served.js"
curl --fail --silent --show-error --max-time 10 \
  "http://127.0.0.1:3098/local-ops-canary.html?ma001-rollback=$(date +%s)" \
  --output "$WORK/served.html"
cmp --silent "$CORE_JS" "$WORK/served.js"
cmp --silent "$HTML" "$WORK/served.html"
grep -q '/local-ops-canary.js?v=ah001' "$HTML"
systemctl is-active --quiet rh-tracker-api.service

echo "result=PASS"
echo "restored=AH001_plus_legacy_workload_endpoint"
echo "module_recovery_copy=$REMOVED_COPY"
echo "kiosk_restart_required=false"
echo "activation=next_local_ops_rotation"
