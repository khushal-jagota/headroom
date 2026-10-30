# VPS measurements

Panels keeps machine, service, and process measurements in a separate SQLite store.
The store sits beside the application database, with `.vps-metrics.sqlite3` after its name.
These writes do not publish application change signals.

One sampler owns a file lock for each store. Other server processes serve stored evidence
and retry ownership every 15 seconds. Collection runs even when Worker dispatch is off.
Tests do not start a host sampler. The server stops its own sampler during normal shutdown.

The sampler collects a baseline immediately, then every 15 seconds. A visible client sends
`POST /api/vps-metrics/activity` to renew a fixed 30-second lease. During that lease, the
sampler collects every two seconds. Filesystem capacity and service state refresh every
60 seconds. Activity cannot increase that cadence. GET requests never probe the host.

`GET /api/vps-metrics/latest` returns the last machine and service evidence, its source
timestamps, retention coverage, and collection status. Its sample omits process rows.
`GET /api/vps-metrics/processes` returns the complete inventory through pages of at most
250 rows. Send the returned sequence with the next offset to keep one immutable snapshot.
An expired sequence returns HTTP 410.

`GET /api/vps-metrics/history` accepts `signal`, optional `entity`, Unix-second `start` and
`end`, and `resolution` (`raw`, `minute`, or `coarse`). Each response contains at most
2,000 points. Invalid ranges and configured limit violations return HTTP 422. A storage
failure returns HTTP 503. Stored timestamps remain visible after a collection failure.
The 500 ms read deadline also covers history decoding. If a large range exceeds it,
split the range into smaller requests. Retention coverage describes the stored tier;
individual points carry signal-specific valid duration.

Host CPU uses total machine capacity as 100%. Process and service CPU use one core as
100%, so those measurements can exceed 100%. The sample includes the core count.
Process RSS differs from service cgroup memory. Process I/O differs from host device I/O.
Nested cgroup totals overlap, so do not add parent and child service totals together.
User service health covers the current operating user's manager. Temperature and fans
are unavailable on this KVM guest.
Missing measurements contain a null value and a reason. Cached slow fields carry their
own source timestamp. Arguments and environment values do not enter the inventory.

Raw machine and service evidence lasts one hour. Minute aggregates last 48 hours.
Fifteen-minute aggregates last 31 days. Complete process snapshots last 15 minutes.
Machine and service payloads share 192 MiB. Process payloads use at most 64 MiB.
Budget eviction can shorten these periods. Coverage reports the actual retained period.
A complete process snapshot over four MiB becomes explicitly unavailable instead of a
partial inventory. SQLite pages use at most 512 MiB. A 64 MiB WAL limit pauses sample
writes if a checkpoint cannot reclaim space. The last committed sample remains available.

Operators can override any field in `environments/vps_metrics/config.py` with an environment
variable named `PLAN_VPS_METRICS_<UPPERCASE_FIELD>`. For example,
`PLAN_VPS_METRICS_RAW_SECONDS=7200` retains two raw hours, and
`PLAN_VPS_METRICS_METRICS_PAYLOAD_BYTES=268435456` sets a 256 MiB payload budget.
Cadence fields include `IDLE_SECONDS`, `ACTIVE_SECONDS`, `ACTIVITY_SECONDS`, `SLOW_SECONDS`,
and `OWNERSHIP_RETRY_SECONDS`. Retention fields include `RAW_SECONDS`, `MINUTE_SECONDS`,
`COARSE_SECONDS`, and `PROCESS_SECONDS`. Other byte and query limits use the exact field
names in that module. Numeric limits must be positive. Retention tiers must increase.
Restart the operator-owned service to apply changed environment values.

The existing VPS status endpoints keep their original contracts and collection paths.
The VPS destination opens the machine-health page inside the shared pane. It shows
CPU, memory, load, disk I/O, network, and root disk capacity, followed by services and
grouped processes. Service and process CPU percentages use the whole machine as 100%.
Process groups keep their positions after their first CPU sort. Partial totals retain
an explicit unavailable part rather than treating missing members as zero.

The visible page renews activity and reads current samples every two seconds. A hidden
browser page or a different pane stops reads and lease renewal. The existing lease
expires on its own. The page resumes with a fresh read when it becomes visible.

Five-minute trends use raw samples. The 24-hour window uses minute buckets. The 30-day
window uses fifteen-minute buckets through adjacent bounded requests. A shorter retained
record occupies only its actual part of the selected window. Missing values and gaps
remain visible as gaps. The longer windows refresh at their bucket boundaries.

A stale or failed feed retains the last readings, dims them, and shows their age with
“no feed.” A missing primary measurement shows its reason and no trend. First-read
placeholders follow reduced-motion preferences. Temperature, fans, and unsupported
machine identity fields do not appear. The page does not include deployment or backup
status and does not call the existing VPS status endpoints.
