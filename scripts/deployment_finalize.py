"""Run a canonical finalizer from checkout source with host Python and stdlib only.

The workflow invokes this file with -I -S -B. Its execution files are outside every
cleanup target; cleanup validates that separation before it removes anything.
"""
from __future__ import annotations

import argparse
import runpy
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("finalizer", choices=("lifecycle", "adoption"))
    arguments, remaining = parser.parse_known_args()
    source = Path(__file__).resolve().parents[1] / "src"
    # -I excludes ambient PYTHONPATH and cwd, -S excludes installed packages.
    # This is the only application import location for either finalizer.
    sys.path.insert(0, str(source))
    module = {
        "lifecycle": "planner.environments.deployment_lifecycle",
        "adoption": "planner.environments.update_reporting",
    }[arguments.finalizer]
    sys.argv = [module, *remaining]
    runpy.run_module(module, run_name="__main__")


if __name__ == "__main__":
    main()
