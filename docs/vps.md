# VPS

The VPS page is a glance at the machine that runs Headroom: CPU, memory, load, disk, network, services, and processes, with short trends. It exists so Khushal can see whether the host is healthy without a separate monitoring login. It does not deploy, back up, or alert. Deployment outcome and backup age live on the smaller status summary, not on these graphs.

## How readings are taken

Measurements live in a separate SQLite file beside the application database, named with a `.vps-metrics.sqlite3` suffix. Those writes do not wake the application change signal. One sampler owns the file. Other server processes serve what is stored and retry ownership every 15 seconds. Collection runs even when Worker dispatch is off. A normal shutdown stops the sampler. Tests do not start a host sampler.

At rest the sampler collects every 15 seconds, after an immediate baseline. While the VPS page is visible it renews a 30-second activity lease, and during that lease the sampler collects every 2 seconds. Filesystem capacity and service state refresh about once a minute. Activity does not speed that slower pass. A GET never probes the host by itself.

Host CPU treats the whole machine as 100%. A single process can be shown against one core and can exceed 100% in the stored sample. The page’s service and process percentages use the whole machine as 100% so a row is comparable to the host chart. Process memory is not cgroup memory. Process I/O is not host device I/O. Nested service totals overlap, so parent and child are not added together. Temperature and fans are unavailable on this guest. A missing measurement is a null plus a reason. Arguments and environment values are not stored.

Raw samples last about an hour, minute aggregates about 48 hours, fifteen-minute aggregates about 31 days, and full process snapshots about 15 minutes. Budgets can evict sooner. The page says what it actually still has. A stale feed keeps the last readings, dims them, and says there is no feed.

The visible page reads every two seconds and renews the lease. A hidden tab or a different pane stops both. The lease expires on its own. Five-minute trends use raw samples, the day uses minute buckets, and 30 days uses fifteen-minute buckets. Gaps stay gaps.

## The other status read

`GET /api/vps-status-summary` is the small header: deployed SHA, deployment outcome, CPU, RAM, disk, and the age of the latest verified backup. `panels environment status` is the operator’s full local inventory and does not go through this page. See [Environments](environments.md).

## Where it shows up

`#/vps` in the shell, under System. It is not embedded in the Workspace utility pane. Window toggles choose 5 minutes, 24 hours, or 30 days. Services and processes list below the instruments, with an explicit unavailable reason when sampling failed.

Operators can override a numeric limit with `PLAN_VPS_METRICS_<FIELD>`, using the field names in `src/planner/environments/vps_metrics/config.py`. Retention tiers have to stay in increasing order. Restart the service to apply them.

## Pointers

`src/planner/environments/vps_metrics/` and `web/src/components/vps/`.

_Last verified: 2026-10-30._
