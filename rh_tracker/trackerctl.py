#!/usr/bin/env python3
"""
trackerctl.py — CLI management tool for rh-tracker share values

Commands:
    trackerctl.py show
    trackerctl.py set <voo> <nvda> [--restart]
    trackerctl.py update [--voo <value>] [--nvda <value>] [--restart]
    trackerctl.py prompt [--restart]

Options:
    --file <path>       Override default .tracker_shares path
    --service <name>    Override default systemd service name
    --restart           Restart rh-tracker service after writing
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_SHARES_FILE = Path.home() / "scripts" / "rh_tracker" / ".tracker_shares"
SERVICE_NAME        = "rh-tracker"


# ── Validation ────────────────────────────────────────────────────────────────

def parse_non_negative_float(value: str) -> float:
    try:
        num = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid number: {value!r}")
    if num < 0:
        raise argparse.ArgumentTypeError(f"value cannot be negative: {num}")
    return num


# ── File I/O ──────────────────────────────────────────────────────────────────

def read_shares(shares_file: Path) -> dict[str, float]:
    """
    Read VOO and NVDA values from shares_file.
    Raises FileNotFoundError or ValueError with clear messages on failure.
    """
    if not shares_file.exists():
        raise FileNotFoundError(
            f"shares file not found: {shares_file}\n"
            "  Create it with:  trackerctl.py set <voo> <nvda>"
        )

    text = shares_file.read_text(encoding="utf-8")

    voo_match  = re.search(r"^VOO=(.+)$",  text, flags=re.MULTILINE)
    nvda_match = re.search(r"^NVDA=(.+)$", text, flags=re.MULTILINE)

    if not voo_match or not nvda_match:
        raise ValueError(
            f"malformed shares file: {shares_file}\n"
            "  Expected format:\n"
            "    VOO=<number>\n"
            "    NVDA=<number>"
        )

    try:
        voo  = float(voo_match.group(1).strip())
        nvda = float(nvda_match.group(1).strip())
    except ValueError:
        raise ValueError(
            f"non-numeric values in shares file: {shares_file}\n"
            "  Both VOO and NVDA must be valid numbers."
        )

    if voo < 0 or nvda < 0:
        raise ValueError(
            f"negative values in shares file: VOO={voo}, NVDA={nvda}\n"
            "  Share quantities must be >= 0."
        )

    return {"VOO": voo, "NVDA": nvda}


def write_shares(shares_file: Path, voo: float, nvda: float) -> None:
    """
    Write VOO and NVDA to shares_file atomically via a temp file,
    so a failed write never leaves a partial or empty file behind.
    """
    tmp = shares_file.with_suffix(".tmp")
    try:
        tmp.write_text(f"VOO={voo}\nNVDA={nvda}\n", encoding="utf-8")
        tmp.replace(shares_file)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


# ── Service control ───────────────────────────────────────────────────────────

def restart_service(service_name: str) -> bool:
    """
    Restart the systemd service. Returns True on success, False on failure.
    Prints a clear status message either way.
    """
    result = subprocess.run(
        ["sudo", "systemctl", "restart", service_name],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print(f"  Service '{service_name}' restarted successfully.")
        return True
    else:
        msg = result.stderr.strip() or result.stdout.strip() or "no output captured"
        print(f"  [error] Failed to restart '{service_name}': {msg}", file=sys.stderr)
        return False


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_show(args: argparse.Namespace) -> int:
    shares = read_shares(args.file)
    print(f"VOO:  {shares['VOO']}")
    print(f"NVDA: {shares['NVDA']}")
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    write_shares(args.file, args.voo, args.nvda)
    print(f"  Shares updated — VOO: {args.voo}  |  NVDA: {args.nvda}")
    if args.restart:
        return 0 if restart_service(args.service) else 1
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    if args.voo is None and args.nvda is None:
        print(
            "[error] --update requires at least one of --voo or --nvda.",
            file=sys.stderr,
        )
        return 2

    shares = read_shares(args.file)
    voo  = args.voo  if args.voo  is not None else shares["VOO"]
    nvda = args.nvda if args.nvda is not None else shares["NVDA"]

    write_shares(args.file, voo, nvda)
    print(f"  Shares updated — VOO: {voo}  |  NVDA: {nvda}")

    if args.restart:
        return 0 if restart_service(args.service) else 1
    return 0


def cmd_prompt(args: argparse.Namespace) -> int:
    shares = read_shares(args.file)

    print("  Press Enter to keep the current value.\n")
    try:
        voo_input  = input(f"  VOO  [{shares['VOO']}]: ").strip()
        nvda_input = input(f"  NVDA [{shares['NVDA']}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n  Aborted.")
        return 1

    voo_raw  = voo_input  or str(shares["VOO"])
    nvda_raw = nvda_input or str(shares["NVDA"])

    try:
        voo  = parse_non_negative_float(voo_raw)
        nvda = parse_non_negative_float(nvda_raw)
    except argparse.ArgumentTypeError as exc:
        print(f"  [error] {exc}", file=sys.stderr)
        return 2

    write_shares(args.file, voo, nvda)
    print(f"\n  Shares updated — VOO: {voo}  |  NVDA: {nvda}")

    if args.restart:
        return 0 if restart_service(args.service) else 1
    return 0


# ── Argument parser ───────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trackerctl",
        description="Manage VOO/NVDA share values for rh-tracker.",
    )
    parser.add_argument(
        "--file", type=Path, default=DEFAULT_SHARES_FILE,
        metavar="PATH",
        help="path to .tracker_shares (default: %(default)s)",
    )
    parser.add_argument(
        "--service", default=SERVICE_NAME,
        metavar="NAME",
        help="systemd service name (default: %(default)s)",
    )

    sub = parser.add_subparsers(dest="cmd", required=True)

    # show
    sub.add_parser("show", help="print current share values")

    # set
    set_cmd = sub.add_parser("set", help="overwrite both share values")
    set_cmd.add_argument("voo",  type=parse_non_negative_float, metavar="VOO")
    set_cmd.add_argument("nvda", type=parse_non_negative_float, metavar="NVDA")
    set_cmd.add_argument("--restart", action="store_true", help="restart service after writing")

    # update
    upd = sub.add_parser("update", help="update one or both share values")
    upd.add_argument("--voo",  type=parse_non_negative_float, metavar="VALUE")
    upd.add_argument("--nvda", type=parse_non_negative_float, metavar="VALUE")
    upd.add_argument("--restart", action="store_true", help="restart service after writing")

    # prompt
    prm = sub.add_parser("prompt", help="interactively update share values")
    prm.add_argument("--restart", action="store_true", help="restart service after writing")

    return parser


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = build_parser()
    args   = parser.parse_args()

    try:
        dispatch = {
            "show":   cmd_show,
            "set":    cmd_set,
            "update": cmd_update,
            "prompt": cmd_prompt,
        }
        exit_code = dispatch[args.cmd](args)
        sys.exit(exit_code)

    except FileNotFoundError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        sys.exit(1)
    except ValueError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        sys.exit(1)
    except PermissionError as exc:
        print(f"[error] permission denied: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"[error] unexpected error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
