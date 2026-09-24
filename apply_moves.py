#!/usr/bin/env python3
"""Move the files confirmed in a declutter move list into category subfolders.

    python apply_moves.py declutter_move_list.json            # shows the plan, asks to confirm
    python apply_moves.py declutter_move_list.json --dry-run  # only show the plan
    python apply_moves.py declutter_move_list.json --dest ~/Organized
    python apply_moves.py --undo "~/Declutter Reports/declutter_move_log_....json"

Only items listed in the exported file are touched, and only if they are still
unchanged since the scan. Nothing is ever overwritten.
"""

import argparse
import sys

from declutter.actions import ListError, apply_moves, load_list, plan_moves, undo_moves
from declutter.report import human_size


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("move_list", nargs="?", help="declutter_move_list.json exported from the report")
    ap.add_argument("--dest", help="put category folders here instead of '<scanned folder>/Declutter Organized'")
    ap.add_argument("--dry-run", action="store_true", help="show what would happen, change nothing")
    ap.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    ap.add_argument("--undo", metavar="MOVE_LOG", help="move files from a previous run back to where they were")
    a = ap.parse_args(argv)

    try:
        if a.undo:
            restored, failed = undo_moves(a.undo)
            print(f"Restored {restored} file(s).")
            for m, why in failed:
                print(f"  could not restore {m['to']}: {why}")
            return 1 if failed else 0
        if not a.move_list:
            ap.error("a move list is required (or --undo MOVE_LOG)")
        plan = plan_moves(load_list(a.move_list, "move"), a.dest)
    except ListError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    for it, dest in plan.ok:
        print(f"MOVE  {it['path']}\n  ->  {dest}")
    for it, why in plan.skipped:
        print(f"SKIP  {it.get('path')}  ({why})")
    print(f"\n{len(plan.ok)} file(s) to move ({human_size(plan.total_bytes)}), {len(plan.skipped)} skipped.")
    if not plan.ok or a.dry_run:
        return 0
    if not a.yes and input("Type 'yes' to move these files: ").strip().lower() != "yes":
        print("Cancelled - nothing was moved.")
        return 1
    moved, failed, log = apply_moves(plan)
    print(f"Moved {moved} file(s).")
    for it, why in failed:
        print(f"  failed: {it['path']}: {why}")
    if log:
        print(f"Undo log: {log}\n  (undo with: python apply_moves.py --undo \"{log}\")")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
