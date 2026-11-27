# Diagnostics

Diagnostics shows the health of the machine that runs Headroom: CPU, memory, load, disk, network, services, and processes, with short trends. It exists so the owner can see whether the host is healthy without a separate monitoring login. It does not deploy, back up, or alert.

## What it is made of

- **A sampler.** One server process at a time owns sampling, through a file lock. Other processes serve what is already stored. Sampling runs even when Worker dispatch is off.
- **A metrics store.** A separate SQLite file beside the application database. Its writes never wake the application's change signal, so frequent samples never make open screens reload.
- **The page.** Settings → Diagnostics (`#/settings/diagnostics`; the old `#/vps` redirects there), with windows of five minutes, a day, or 30 days.

## How readings are taken

The sampler collects at a slow pace at rest. While the page is visible, it renews a short activity lease and the sampler speeds up. When the tab is hidden the lease lapses on its own. Disk capacity and service state refresh on a slower pass. Reading the page never probes the host by itself.

History is kept in tiers: raw samples briefly, then coarser buckets for longer. The page says what it still has. A missing reading is stored as missing with a reason and drawn as a gap. A stale feed keeps its last readings, dims them, and says so.

Percentages for services and processes are against the whole machine, so a row compares directly with the host chart. Nested service totals overlap and are not added together. Temperature and fans are unavailable on this guest. Command arguments and environment values are never stored.

The Mac collector uses native host and process observations through the same store and API. Process rates require the same boot UUID and exact process birth. Available memory means free plus inactive pages. Linux pressure and unproved disk, network, and process I/O measurements remain unavailable. Service grouping is unavailable on the Mac. An empty service table does not prove that launchd has no jobs.

## Tailnet path

Each sample also records how the machine reaches every tailnet device, so a dropped stream can be lined up with a path change. The Linux collector runs `tailscale status --json` and `tailscale debug metrics` as the server's own user, beside the other reads, with a one second limit. For each device the sample holds whether the path is direct or relayed, the peer endpoint, the last handshake, the device's home relay, and whether the path changed since the last sample. The machine's own home relay is recorded once. The debug counters are host-wide, so the sample holds three rates for the machine as a whole: path discovery attempts, relay packets, and home relay changes. A missing, slow or failing `tailscale` records the source as unavailable with a reason, and the sample is taken on time. The Diagnostics page does not show it yet. The Mac collector does not record this.

## Decisions that shape it

- A separate store and no change signal, so monitoring cannot slow the workspace.
- Fast only while someone is looking.
- Gaps stay gaps. Nothing fills in an old reading as current.

## Code

- `server/environments/vps_metrics/`: sampler, collectors, store, and retention. Limits are in `configuration.ts`.
- `web/src/components/vps/`: the page.

_Last verified: 2026-11-27._
