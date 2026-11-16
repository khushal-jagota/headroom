"""Hand the fixture's process, environment and listener to the TS test host."""

import os


def main() -> None:
    entry = os.environ.get("PANELS_COMPARE_TS_ENTRY", "server/test_host.ts")
    os.execvpe("node", ["node", "--experimental-strip-types", entry], os.environ.copy())


if __name__ == "__main__":
    main()
