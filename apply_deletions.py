#!/usr/bin/env python3
"""Delete the files confirmed in a declutter delete list.

    python apply_deletions.py declutter_delete_list.json             # shows the plan, asks to confirm
    python apply_deletions.py declutter_delete_list.json --dry-run   # only show the plan
    python apply_deletions.py declutter_delete_list.json --quarantine ~/declutter_trash

Only items listed in the exported file are touched, and only if they are still
unchanged since the scan. With --quarantine files are moved to that folder
instead of being deleted, so you can empty it yourself later.
"""

import argparse
import sys

from declutter.actions import ListError, apply_deletions, load_list, plan_deletions
from declutter.report import human_size


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("delete_list", help="declutter_delete_list.json exported from the report")
    ap.add_argument("--quarantine", metavar="DIR", help="move files into DIR instead of deleting them")
    ap.add_argument("--dry-run", action="store_true", help="show what would happen, change nothing")
    ap.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    a = ap.parse_args(argv)

    try:
        plan = plan_deletions(load_list(a.delete_list, "delete"))
    except ListError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    verb = "QUARANTINE" if a.quarantine else "DELETE"
    for it, _ in plan.ok:
        print(f"{verb}  {it['path']}")
    for it, why in plan.skipped:
        print(f"SKIP  {it.get('path')}  ({why})")
    print(f"\n{len(plan.ok)} file(s) to {verb.lower()} ({human_size(plan.total_bytes)}), {len(plan.skipped)} skipped.")
    if not plan.ok or a.dry_run:
        return 0
    if not a.yes:
        word = "quarantine" if a.quarantine else "delete"
        if input(f"Type '{word}' to {word} these {len(plan.ok)} files: ").strip().lower() != word:
            print("Cancelled - nothing was deleted.")
            return 1
    done, failed = apply_deletions(plan, a.quarantine)
    print(f"{'Quarantined' if a.quarantine else 'Deleted'} {done} file(s).")
    for it, why in failed:
        print(f"  failed: {it['path']}: {why}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
