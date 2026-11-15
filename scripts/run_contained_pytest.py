#!/usr/bin/env python3
"""Run repository pytest only after an operating-system boundary exists."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from test_containment.runner import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
