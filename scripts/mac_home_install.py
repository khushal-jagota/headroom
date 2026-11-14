"""Compatibility entry point for the sealed Mac home installer."""

from planner.installation.mac_home import install_home as install_home
from planner.installation.mac_home import main

if __name__ == "__main__":
    main()
