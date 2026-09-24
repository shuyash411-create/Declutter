"""declutter entry point (this is the file PyInstaller packages).

Double-clicking the built app opens the GUI. `declutter --scan DIR...` runs a
headless scan instead (see declutter/cli.py).
"""

import multiprocessing
import sys


def main():
    multiprocessing.freeze_support()
    if "--scan" in sys.argv[1:]:
        from declutter.cli import main as cli_main
        return cli_main(sys.argv[1:])
    from declutter.gui import run
    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
