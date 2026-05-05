"""Run immediate Ozon API sync in one command."""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import List


ROOT = Path(__file__).resolve().parent


def run_step(python_exe: str, args: List[str]) -> None:
    cmd = [python_exe, "-m", "src.main", *args]
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] RUN: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Immediate Ozon API sync for dashboard data."
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python executable to use (default: current interpreter).",
    )
    parser.add_argument(
        "--days-back",
        type=int,
        default=30,
        help="Days back for transactions sync (default: 30).",
    )
    parser.add_argument(
        "--profile",
        choices=["finance", "full"],
        default="finance",
        help="finance: only steps needed for finance report; full: include full sync first.",
    )
    args = parser.parse_args()

    if args.profile == "full":
        run_step(args.python, ["--mode", "full"])
    else:
        run_step(args.python, ["--mode", "report_compensation"])

    run_step(args.python, ["--mode", "transactions", "--days-back", str(args.days_back)])
    run_step(args.python, ["--mode", "normalize_finance"])

    # Post-sync check: detect new expense articles not explicitly mapped.
    month = datetime.now().strftime("%Y-%m")
    check_cmd = [args.python, "scripts/check_unmapped_expenses.py", "--month", month]
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] RUN: {' '.join(check_cmd)}")
    subprocess.run(check_cmd, cwd=str(ROOT))

    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Sync completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
