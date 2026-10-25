#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
#  VOO & NVDA Tracker — launcher v4.1
#
#  Usage:
#    bash run_tracker.sh          # watch mode (refreshes every 6 hrs)
#    bash run_tracker.sh --once   # single snapshot then exit
#
#  First run:  prompts for VOO/NVDA shares, saves to .tracker_shares
#  After that: reads .tracker_shares silently (systemd-safe)
#
#  To update share quantities, edit .tracker_shares directly or
#  delete it and re-run this script to be prompted again.
#
#  To change your venv path, edit VENV_PYTHON below.
# ─────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TRACKER="$SCRIPT_DIR/voo_nvda_tracker.py"
SHARES_FILE="$SCRIPT_DIR/.tracker_shares"

# ── Python interpreter — edit this path to match your venv ───────
VENV_PYTHON="${VENV_PYTHON:-$HOME/rh_env/bin/python}"

# ── Verify interpreter exists ─────────────────────────────────────
if [[ ! -x "$VENV_PYTHON" ]]; then
    echo ""
    echo "  [error] Python interpreter not found at: $VENV_PYTHON"
    echo "  Edit VENV_PYTHON in this script to point to your venv."
    echo "  Example:  VENV_PYTHON=\"\$HOME/my_env/bin/python\""
    echo ""
    exit 1
fi

# ── Verify tracker script exists ──────────────────────────────────
if [[ ! -f "$TRACKER" ]]; then
    echo ""
    echo "  [error] Tracker script not found at: $TRACKER"
    echo "  Make sure voo_nvda_tracker.py is in the same folder as this script."
    echo ""
    exit 1
fi

echo ""
echo "  ╔══════════════════════════════════════╗"
echo "  ║     VOO & NVDA Portfolio Tracker     ║"
echo "  ╚══════════════════════════════════════╝"
echo ""

# ── Load shares: non-interactive if .tracker_shares exists ────────
if [[ -f "$SHARES_FILE" ]]; then

    # File exists — read silently, do NOT prompt
    VOO_SHARES=$(grep  "^VOO="  "$SHARES_FILE" | cut -d= -f2)
    NVDA_SHARES=$(grep "^NVDA=" "$SHARES_FILE" | cut -d= -f2)

    # Fail clearly if either value is missing or blank
    if [[ -z "$VOO_SHARES" || -z "$NVDA_SHARES" ]]; then
        echo "  [error] .tracker_shares exists but is missing or malformed."
        echo "  Expected format:"
        echo "    VOO=<number>"
        echo "    NVDA=<number>"
        echo "  Fix the file or delete it to be prompted on next run."
        echo ""
        exit 1
    fi

    echo "  Using saved shares from .tracker_shares (non-interactive mode)"
    echo "  VOO: $VOO_SHARES   |   NVDA: $NVDA_SHARES"

else

    # First run — prompt interactively and save
    echo "  First run: enter your share quantities."
    echo "  These will be saved to .tracker_shares for future runs."
    echo "  To update them later, edit that file or delete it to be prompted again."
    echo ""

    read -rp "  VOO  shares: " VOO_SHARES
    read -rp "  NVDA shares: " NVDA_SHARES

    if [[ -z "$VOO_SHARES" || -z "$NVDA_SHARES" ]]; then
        echo ""
        echo "  [error] Share quantities cannot be empty."
        exit 1
    fi

    printf "VOO=%s\nNVDA=%s\n" "$VOO_SHARES" "$NVDA_SHARES" > "$SHARES_FILE"
    echo ""
    echo "  Shares saved to .tracker_shares."

fi

# ── Launch ────────────────────────────────────────────────────────
echo "  Using interpreter: $VENV_PYTHON"
echo "  (Ctrl+C at any time to stop)"
echo ""

# Collect allowed flags into a bash array (safe forwarding)
EXTRA_ARGS=()
for arg in "$@"; do
    if [[ "$arg" == "--once" || "$arg" == "--install-deps" ]]; then
        EXTRA_ARGS+=("$arg")
    fi
done

"$VENV_PYTHON" "$TRACKER" "$VOO_SHARES" "$NVDA_SHARES" "${EXTRA_ARGS[@]}"
