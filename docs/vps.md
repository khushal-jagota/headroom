# VPS

The VPS page is a glance at the machine that runs Headroom: CPU, memory, load, disk, network, services, and processes, with short trends. It exists so Khushal can see whether the host is healthy without a separate monitoring login. It does not deploy, back up, or alert. The installed commit, deployment outcome, and backup age are on the smaller status summary described in [Environments](environments.md).

## What it is made of

- **A sampler.** One server process at a time owns sampling, through a file lock. Other processes serve what is already stored. Sampling runs even when Worker dispatch is off.
- **A metrics store.** A separate SQLite file beside the application database. Its writes never wake the application's change signal, so frequent samples never make open screens reload.
- **The page.** `#/vps` in the shell, under System, with windows of five minutes, a day, or 30 days.

## How readings are taken

The sampler collects at a slow pace at rest. While the page is visible, it renews a short activity lease and the sampler speeds up. When the tab is hidden the lease lapses on its own. Disk capacity and service state refresh on a slower pass. Reading the page never probes the host by itself.

History is kept in tiers: raw samples briefly, then coarser buckets for longer. The page says what it still has. A missing reading is stored as missing with a reason and drawn as a gap. A stale feed keeps its last readings, dims them, and says so.

Percentages for services and processes are against the whole machine, so a row compares directly with the host chart. Nested service totals overlap and are not added together. Temperature and fans are unavailable on this guest. Command arguments and environment values are never stored.

## Decisions that shape it

- A separate store and no change signal, so monitoring cannot slow the workspace.
- Fast only while someone is looking.
- Gaps stay gaps. Nothing fills in an old reading as current.

## Code

- `src/planner/environments/vps_metrics/`: sampler, collectors, store, and retention. Limits are in `config.py`.
- `web/src/components/vps/`: the page.

_Last verified: 2026-11-04._
