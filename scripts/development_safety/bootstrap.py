"""Reach only this checkout's standard-library-only guard under isolated Python."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from development_safety.launcher import main

raise SystemExit(main())
