#!/usr/bin/env bash
# One-command release of the rh_tracker source on rackmain1 (alias: tracker-release).
#
#   deploy/rackmain1-release.sh          # fetch main, test, plan, ask, apply, restart if needed, verify
#   deploy/rackmain1-release.sh --yes    # no confirmation prompts (the restart still needs sudo)
#   deploy/rackmain1-release.sh --plan   # show the plan only
#
# Replaces a live file only when it matches some version of that file in this
# repo's history, so edits made on the host are never overwritten. Backs up
# every replaced file. When an API module changes, restarts rh-tracker-api
# (sudo) and checks /health, /status, and /api/ops/workloads; if the API does
# not come back healthy, it restores the backups, restarts again, and stops.
# Scripts run by other units (Priority Alpha agent, report, backup, tracker)
# take effect at their next scheduled run.
main() {
  set -uo pipefail
  local repo; repo="$(cd "$(dirname "$0")/.." && pwd)"
  local live="$HOME/scripts/rh_tracker"
  local releases="$HOME/tracker-releases"
  local api="http://127.0.0.1:8000"
  local mode="ask"
  case "${1:-}" in
    "") ;; --yes|-y) mode="yes" ;; --plan) mode="plan" ;;
    -h|--help) sed -n '2,15p' "$0"; return 0 ;;
    *) echo "Unknown option: $1 (use --yes, --plan, or --help)" >&2; return 2 ;;
  esac
  say() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
  fail() { printf '\n\033[1;31mSTOPPED: %s\033[0m\n' "$*" >&2; exit 1; }
  [ "$(id -un)" != "root" ] || fail "run as ap3x, not root"
  export GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh -i $HOME/.ssh/ap3x_tracker_api -o IdentitiesOnly=yes}"
  # Modules the API process imports; a change to any of them needs a restart.
  local api_modules=" tracker_api.py ops_state.py media_activity.py trackerctl.py voo_nvda_tracker.py "

  if [ -z "${AP3X_RELEASE_REEXEC:-}" ]; then
    say "Fetching latest main"
    git -C "$repo" fetch --quiet origin main || fail "could not fetch from GitHub"
    git -C "$repo" checkout --quiet --detach origin/main || fail "could not check out origin/main"
    AP3X_RELEASE_REEXEC=1 exec bash "$repo/deploy/rackmain1-release.sh" "$@"
  fi
  local sha; sha="$(git -C "$repo" rev-parse --short HEAD)"
  echo "Commit: $(git -C "$repo" log -1 --format='%h %s' HEAD)"

  say "Tests"
  local file
  for file in "$repo"/rh_tracker/*.py; do python3 -m py_compile "$file" 2>/dev/null || fail "$(basename "$file") does not compile"; done
  for file in "$repo"/rh_tracker/*.sh; do bash -n "$file" || fail "$(basename "$file") has a shell syntax error"; done
  local results; results="$(cd "$repo" && python3 -m unittest discover -s ma001/tests 2>&1)"
  printf '%s\n' "$results" | tail -1 | grep -q '^OK' || { printf '%s\n' "$results" | tail -15; fail "MA001 tests failed"; }
  echo "compile, shell syntax, MA001 tests: OK"

  say "Plan"
  local changes=() restart=0 src name have new known
  for src in "$repo"/rh_tracker/*.py "$repo"/rh_tracker/*.sh; do
    name="$(basename "$src")"
    new="$(git -C "$repo" hash-object "$src")"
    if [ ! -e "$live/$name" ]; then echo "NEW       $name"; changes+=("$name"); continue; fi
    have="$(git -C "$repo" hash-object "$live/$name")"
    if [ "$have" = "$new" ]; then echo "CURRENT   $name"; continue; fi
    known="$(git -C "$repo" log --format=%H HEAD -- "rh_tracker/$name" | while read -r c; do git -C "$repo" rev-parse -q --verify "$c:rh_tracker/$name" 2>/dev/null; done | grep -cx "$have")"
    [ "$known" -gt 0 ] || fail "$live/$name differs from every version in this repo (edited on the host?); commit it to the repo first. Nothing changed."
    echo "REPLACE   $name"; changes+=("$name")
  done
  for name in "${changes[@]}"; do [[ "$api_modules" == *" $name "* ]] && restart=1; done
  echo "${#changes[@]} change(s) from $sha$([ "$restart" = 1 ] && echo "; rh-tracker-api will be restarted")"
  [ "$mode" = "plan" ] && { say "Plan only; nothing applied"; return 0; }
  [ "${#changes[@]}" -gt 0 ] || { say "Already current"; return 0; }
  if [ "$mode" = "ask" ]; then
    printf '\nApply these %s change(s)%s? [y/N] ' "${#changes[@]}" "$([ "$restart" = 1 ] && echo " and restart the tracker API")"
    local reply; read -r reply
    case "$reply" in y|Y|yes|YES) ;; *) say "Cancelled; nothing applied"; return 0 ;; esac
  fi

  say "Applying"
  local backup; backup="$releases/backups/$(date -u +%Y%m%dT%H%M%SZ)-$sha"
  install -d -m 700 "$backup"
  local undo="" restore=()
  for name in "${changes[@]}"; do
    if [ -e "$live/$name" ]; then
      cp -p "$live/$name" "$backup/$name" || fail "backup failed: $name"
      undo+="cp -p '$backup/$name' '$live/$name'; "; restore+=("$name")
    else
      undo+="rm -f '$live/$name'; "
    fi
    local tmp; tmp="$(mktemp "$live/.release.XXXXXX")"
    cp "$repo/rh_tracker/$name" "$tmp" && chmod "$([ -e "$live/$name" ] && stat -c %a "$live/$name" || echo 755)" "$tmp" && mv "$tmp" "$live/$name" ||
      { rm -f "$tmp"; fail "could not replace $name; to undo so far: $undo"; }
    echo "installed $name"
  done
  [ "$restart" = 1 ] && undo+="sudo systemctl restart rh-tracker-api; "
  printf '%s\n' "$undo" > "$backup/UNDO.sh"

  healthy() {
    local i
    for i in $(seq 1 30); do
      if curl -fsS -o /dev/null --max-time 5 "$api/health" && curl -fsS -o /dev/null --max-time 15 "$api/status" &&
         curl -fsS -o /dev/null --max-time 15 "$api/api/ops/workloads"; then return 0; fi
      sleep 2
    done
    return 1
  }
  if [ "$restart" = 1 ]; then
    say "Restarting rh-tracker-api"
    sudo systemctl restart rh-tracker-api || fail "restart failed. To undo: $undo"
    if healthy; then
      echo "API healthy: /health, /status, /api/ops/workloads answer"
    else
      say "API did not come back healthy; rolling back"
      for name in "${restore[@]}"; do cp -p "$backup/$name" "$live/$name"; done
      for name in "${changes[@]}"; do [[ " ${restore[*]} " == *" $name "* ]] || rm -f "$live/$name"; done
      sudo systemctl restart rh-tracker-api
      healthy && fail "rolled back to the previous files; the API is healthy again. Check: journalctl -u rh-tracker-api -n 50" ||
        fail "rolled back, but the API is still not healthy. Check: journalctl -u rh-tracker-api -n 50"
    fi
  fi

  say "Done: $sha is live"
  echo "To undo:  $undo"
  echo "(also saved in $backup/UNDO.sh)"
}

main "$@"
exit
