# Mac home crash proof

This probe tests the first Mac home change before the rest of the first slice proceeds.
It does not install a production home or prove the full first slice.

Use a separate Apple Silicon Mac checkout at the candidate commit in the Ticket handoff.
Do not use the live Mac executor checkout, environment, or service.
The Mac must have an active graphical login session.
Do not test sleep, logout, or reboot with this probe.

Create the checkout's independent dependencies with `scripts/dev setup`.
The setup command needs Python 3.12 or newer, Node.js 22 or newer, and npm.
For PyICU, install Homebrew `icu4c` and `pkgconf` first, then export these build paths:

```sh
export PKG_CONFIG_PATH="$(brew --prefix icu4c)/lib/pkgconfig"
export PATH="$(brew --prefix icu4c)/bin:$PATH"
scripts/dev setup
```

Check the source revision and clean status, then run the probe:

```sh
git rev-parse HEAD
git status --short
.venv/bin/python scripts/mac_home_crash_probe.py
```

The script refuses a dirty checkout or a foreign Python environment.
It creates a private `~/.ph-<random>` folder and a random Aqua LaunchAgent.
All configuration, database, logs, locks, sockets, and provider-home paths point into that folder.
Dispatch stays disabled and test mode stays enabled.
The script does not change Tailscale or provider authentication.
It selects a free loopback port and preserves the report and logs.

The probe checks these boundaries:

1. The real supervisor starts the real application and health answers on the selected port.
2. The listener is the supervisor's child and shares its process group.
3. A hard kill targets only the random launchd job after an identity check.
4. The old application's exact microsecond birth disappears. Reparenting does not count as disappearance.
5. A different supervisor starts a replacement child and health answers again.
6. The job unloads, its recorded processes disappear, and the port becomes available.

A pass requires `result: passed`, `job_removed: true`, `cleanup_verified: true`, and no cleanup intervention or error.
Return the exact revision, command output, `report.json`, `stdout.log`, and `stderr.log` to the Manager.
The successful restart proves recovery of the application's port and database ownership lock.
This probe does not prove Worker cleanup, backups, ordinary stop, install usability, or the later slices.

If crash recovery fails, retain the report and stop further build work.
The script unloads only its random job and waits for natural process teardown.
If a recorded test process survives, cleanup uses its exact birth identity and records that intervention.
An intervention forces failure and never counts as launchd crash proof.
If cleanup remains unproved, report the remaining identities before any manual action.
