#!/usr/bin/env bash
set -u

RH_ROOT="/home/ap3x/scripts/rh_tracker"
WALL_ROOT="/home/ap3x/stacks/media-automation/wall-ui"
BACKUP_ROOT="$RH_ROOT/state/priority_alpha_backups"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$BACKUP_ROOT/priority-alpha-full-$STAMP"

mkdir -p "$BACKUP_DIR"/{api,wall-ui,state,served,validation,systemd,docker,logs}

echo "===== Priority Alpha full backup ====="
echo "BACKUP_DIR=$BACKUP_DIR"

echo
echo "===== 1) Copy backend source ====="
cp -av "$RH_ROOT/tracker_api.py" "$BACKUP_DIR/api/" 2>/dev/null || true
cp -av "$RH_ROOT/ops_state.py" "$BACKUP_DIR/api/" 2>/dev/null || true
cp -av "$RH_ROOT/voo_nvda_tracker.py" "$BACKUP_DIR/api/" 2>/dev/null || true
cp -av "$RH_ROOT/run_tracker.sh" "$BACKUP_DIR/api/" 2>/dev/null || true
cp -av "$RH_ROOT/run_tracker_api.sh" "$BACKUP_DIR/api/" 2>/dev/null || true
cp -av "$RH_ROOT/robinhood_sync.py" "$BACKUP_DIR/api/" 2>/dev/null || true

echo
echo "===== 2) Copy Wall UI source ====="
cp -av "$WALL_ROOT/index.html" "$BACKUP_DIR/wall-ui/" 2>/dev/null || true
cp -av "$WALL_ROOT/app.js" "$BACKUP_DIR/wall-ui/" 2>/dev/null || true
cp -av "$WALL_ROOT/style.css" "$BACKUP_DIR/wall-ui/" 2>/dev/null || true
cp -av "$WALL_ROOT/radar.html" "$BACKUP_DIR/wall-ui/" 2>/dev/null || true
cp -av "$WALL_ROOT/docker-compose.yml" "$BACKUP_DIR/wall-ui/" 2>/dev/null || true
cp -av "$WALL_ROOT/nginx" "$BACKUP_DIR/wall-ui/" 2>/dev/null || true

echo
echo "===== 3) Copy state files ====="
for f in \
  state/current_state.json \
  state/threat_state.json \
  state/event_log.json \
  state/service_state.json \
  state/latency_state.json \
  state/resource_state.json \
  state/snapshots/snapshot_latest.json \
  robinhood_snapshot.json
do
  if [ -f "$RH_ROOT/$f" ]; then
    cp -av "$RH_ROOT/$f" "$BACKUP_DIR/state/" 2>/dev/null || true
  else
    echo "MISSING: $RH_ROOT/$f"
  fi
done

echo
echo "===== 4) Capture served Wall UI assets ====="
curl -sS --max-time 15 http://127.0.0.1:3098/ > "$BACKUP_DIR/served/index.html" || true
curl -sS --max-time 15 http://127.0.0.1:3098/app.js > "$BACKUP_DIR/served/app.js" || true
curl -sS --max-time 15 http://127.0.0.1:3098/style.css > "$BACKUP_DIR/served/style.css" || true

echo
echo "===== 5) Capture API validation ====="
curl -sS --max-time 20 http://127.0.0.1:8000/api/weather \
  | tee "$BACKUP_DIR/validation/backend_weather.json" \
  | jq '{provider, source_label, posture, usable, cache_source, cache_age_seconds, stale, updated_at, precip_timeline}' || true

curl -sS --max-time 20 http://127.0.0.1:3098/api/weather \
  | tee "$BACKUP_DIR/validation/wall_weather.json" \
  | jq '{provider, source_label, posture, usable, cache_source, cache_age_seconds, stale, updated_at, precip_timeline}' || true

curl -sS --max-time 20 http://127.0.0.1:3098/api/status \
  | tee "$BACKUP_DIR/validation/wall_status.json" \
  | jq '{services, system}' || true

curl -sS --max-time 20 http://127.0.0.1:3098/api/external \
  | tee "$BACKUP_DIR/validation/wall_external.json" \
  | jq '.' || true

curl -sS --max-time 20 http://127.0.0.1:3098/api/ops/brief \
  | tee "$BACKUP_DIR/validation/wall_ops_brief.json" \
  | jq '{
      state: .state.overall_state,
      threat: .state.threat_level,
      score: .state.score,
      services: .services,
      latency: .latency,
      resources: .resources,
      weather: .weather,
      events: .events,
      data_quality: .data_quality
    }' || true

curl -sS --max-time 20 http://127.0.0.1:3098/api/ops/feed \
  | tee "$BACKUP_DIR/validation/wall_ops_feed.json" \
  | jq '{counts, items}' || true

echo
echo "===== 6) Capture service/container status ====="
{
  echo "===== timestamp ====="
  date -Is

  echo
  echo "===== rh-tracker-api.service ====="
  systemctl status rh-tracker-api.service --no-pager || true

  echo
  echo "===== rh-tracker.service ====="
  systemctl status rh-tracker.service --no-pager || true

  echo
  echo "===== robinhood-sync.timer ====="
  systemctl status robinhood-sync.timer --no-pager || true

  echo
  echo "===== port 8000 owner ====="
  ss -tulnp | grep ':8000' || true
} > "$BACKUP_DIR/systemd/status.txt" 2>&1

{
  echo "===== docker ps ====="
  docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'

  echo
  echo "===== wall-ui logs tail ====="
  docker logs --tail 120 wall-ui 2>&1 || true
} > "$BACKUP_DIR/docker/docker_status.txt" 2>&1

echo
echo "===== 7) Compile checks ====="
{
  cd "$RH_ROOT"
  python3 -m py_compile tracker_api.py && echo "tracker_api.py OK"
  python3 -m py_compile ops_state.py && echo "ops_state.py OK"
  python3 -m py_compile voo_nvda_tracker.py && echo "voo_nvda_tracker.py OK"
} > "$BACKUP_DIR/validation/compile_check.txt" 2>&1 || true

echo
echo "===== 8) Served marker checks ====="
{
  echo "===== app.js markers ====="
  grep -nE "formatFreshness|normalizeWeatherSource|weatherTimelineDrivers|service-health-grid|external-merged|postureHistoryReasons|renderOpsFeed|loadThreat" "$BACKUP_DIR/served/app.js" || true

  echo
  echo "===== style.css markers ====="
  grep -nE "Phase 10B|Phase 10D|Phase 10D.1|Phase 10D.2|Phase 10D.3|Phase 10D.4|weather-timeline|service-health-grid|external-merged|service-card" "$BACKUP_DIR/served/style.css" || true
} > "$BACKUP_DIR/validation/served_markers.txt" 2>&1

echo
echo "===== 9) Write manifest ====="
cat > "$BACKUP_DIR/MANIFEST.txt" <<MANIFEST
PRIORITY ALPHA FULL BACKUP

Created:
  $(date -Is)

Backup dir:
  $BACKUP_DIR

Scope:
  - rh_tracker backend source
  - ops_state event/state engine
  - Wall UI source
  - served Wall UI files
  - current operational state files
  - weather/status/external/ops validation
  - systemd status
  - Docker status/logs
  - compile checks
  - served marker checks

Current design assumptions:
  - /api/ops/brief is live operational truth.
  - /api/ops/feed is tactical event history.
  - Weather timeline is informational unless backend posture says otherwise.
  - Passive Weather intel does not drive warning style by itself.
  - External Health is merged into Homelab Status.
  - Homelab Status displays service/resource/latency/API health.
MANIFEST

echo
echo "===== 10) Build archive ====="
cd "$BACKUP_ROOT"
tar -czf "$BACKUP_DIR.tar.gz" "$(basename "$BACKUP_DIR")"

echo
echo "===== Priority Alpha backup complete ====="
echo "BACKUP_DIR=$BACKUP_DIR"
echo "ARCHIVE=$BACKUP_DIR.tar.gz"
ls -lh "$BACKUP_DIR.tar.gz"
