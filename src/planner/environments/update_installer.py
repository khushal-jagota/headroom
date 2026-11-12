"""Compatibility entry point for the self-contained Linux installer."""
from planner.platform_runtime.linux.update_installer import main

if __name__ == "__main__":
    raise SystemExit(main())
