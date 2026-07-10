# Personal VPS sizing for Panels, Hermes, and coding agents

_Research snapshot: 2026-07-10. This is sized for one person's private machine,
not a latency-SLO production service. Prices vary with account country, VAT, IPv4,
term, and availability; confirm the checkout total._

## Bottom line

For the original 4–8-agent workload, buy an **x86-64 VPS with 8 shared vCPU,
16–24 GB RAM, and local NVMe**. **OVHcloud VPS-4** remains the simple choice at
8 vCore, 24 GB RAM, 200 GB NVMe and £20.81/month including UK VAT; unlike the
other value plans, it includes an automatic daily backup.

For **10–12 persistent CLI sessions, several worktrees/dev servers and 2–4
browser sessions**, move the target to **32 GB RAM**. The best strict-budget fit
is now **netcup VPS Lite 4 G12s: 16 shared x86 vCore, 32 GB RAM, 640 GB SSD,
€25.72/month including 19% German VAT**, on a one-month contract with IPv4 and
IPv6. It is the strict-budget capacity recommendation for the expanded workload,
but the only exact benchmark exposes weak single-thread speed; do not mistake its
16 advertised vCores for 16 fast cores.

If €30 is a soft rather than hard ceiling, the better development machine is
**netcup VPS 4000 G12: 12 shared x86 vCore, 32 GB DDR5 ECC and 1 TB NVMe**. Its
live price is now **€32.41/month including 19% German VAT on a 12-month term**
(about €32.68 at the UK 20% VAT rate), not the earlier €26.18 price. It has a
2.5 Gbit/s interface and NVMe rather than the Lite plan's 1 Gbit/s and SSD, but
the difference will mostly show up during package installs, worktree setup and
concurrent builds—not while agents wait for models.

Do **not** choose netcup's **RS 2000 G12** merely because it has eight dedicated
cores. At €21.43/month on a 12-month term its CPU is excellent value, but 16 GB
RAM is the wrong balance for 12 sessions plus four Chromiums. Shared CPU is
appropriate for this bursty personal workload; cap locally expensive jobs rather
than trying to run twelve builds at once.

## Expanded workload: 10–12 agents and server-first CLI use

`tmux` itself is negligible. Persistent shells are not the sizing problem either.
Memory is consumed by the process trees kept alive behind them: agent CLIs, MCP
servers, TypeScript/Python language servers, Vite, test runners and Chromium.
For planning purposes, expect the normal working set to land around 16–24 GB and
leave the rest of a 32 GB machine for filesystem cache and short peaks. Four
complex Chromium sessions or a leaked long-running agent can push beyond that,
so configure swap and monitoring, but do not treat swap as working memory.

The relevant choices, at today's live prices, rank as follows:

| Rank | Plan | Current consumer price | Why it ranks here |
|---|---|---:|---|
| **1** | **netcup VPS Lite 4 G12s** — 16 shared x86 vCore, 32 GB, 640 GB SSD | **€25.72 incl. 19% VAT**, one-month minimum; IPv4+IPv6 included | Best balance inside the real budget. Enough RAM for twelve persistent sessions and several browsers, plenty of worktree space, and no annual lock-in. Lite has a 1 Gbit/s interface, is throttled to 100 Mbit/s after averaging over 100 Mbit/s for 24 hours, and uses SSD rather than NVMe. ([live checkout](https://www.netcup.com/en/server/vps/vps-lite-4-g12s-iv-1m)) |
| **2** | **netcup VPS 4000 G12** — 12 shared x86 vCore, 32 GB DDR5 ECC, 1 TB NVMe | **€32.41 incl. 19% VAT**, 12-month minimum; no-term billing adds €4.88/month | Best machine close to the budget, but no longer a sub-€30 plan. The 2.5 Gbit/s interface and NVMe make it preferable for I/O-heavy installs/builds. Automatic EU inventory was unavailable when checked; named EU locations added €4.88/month. ([live checkout](https://www.netcup.com/en/server/vps/vps-4000-g12-iv-12m)) |
| **3** | **Contabo Cloud VPS 40** — 12 shared x86 vCPU, 48 GB, 250 GB NVMe | **€25 before VAT; about €30 at 20% VAT**, with possible location fees | The most RAM on paper and the best choice if four browsers are genuinely routine. It ranks below netcup because recent owner reports continue to describe much wider CPU/disk variability and slower support; buy it only if memory capacity matters more than predictable build time. Upgrades preserve the server, but downgrades require ordering and migrating to a new one. ([official price](https://contabo.com/en/pricing/), [plan changes](https://help.contabo.com/en/support/solutions/articles/103000269700-how-to-make-changes-to-your-vps-or-vds-plan)) |
| **4** | **OVHcloud VPS-4** — 8 shared x86 vCore, 24 GB, 200 GB NVMe | **£20.81 incl. UK VAT** | Easiest operational choice and the only shortlist plan with a daily automatic backup included, but 24 GB/200 GB is less future-proof for twelve sessions and many worktrees. One-click upward scaling is good; the next useful tier is outside this budget. ([official UK plan](https://www.ovhcloud.com/en-gb/vps/)) |
| **5** | **Hetzner CX53** — 16 shared x86 vCPU, 32 GB, 320 GB SSD | **€29.49 ex VAT and IPv4; roughly €35.69 incl. 19% VAT before IPv4** | Excellent hourly flexibility, but the June 2026 increase moved it well beyond budget. Hetzner explicitly positions CX for variable, low-to-medium CPU use. The smaller CX43 is affordable but only has 16 GB. ([official prices](https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/), [CX positioning](https://www.hetzner.com/cloud/cost-optimized)) |

The new Lite tier is not yet old enough to have a meaningful long-term reliability
record. Early owner discussion confirms it is a new cost-optimised range rather
than a promotional first-year price. More generally, recent netcup users describe
G12 CPU performance as good (Zen 4/5-era EPYC hosts), while support experiences
range from trouble-free to week-long replies. ([new Lite discussion](https://www.reddit.com/r/VPS/comments/1sx46bl/netcup_brand_new_vps_lite_offerings/),
[recent G12 comparison](https://www.reddit.com/r/hetzner/comments/1u8luef/hetzner_cpx32_or_netcup_vps_2000_g12/),
[negative support report](https://www.reddit.com/r/VPS/comments/1sxw64w/suggest_me_a_vps_with_certain_specs/))

The regular netcup VPS and Lite plans are explicitly shared-resource VMs; only
netcup Root Servers guarantee CPU. Snapshots are available on the netcup plans,
but that is not the same as OVH's included scheduled daily backup. Keep an off-host
backup regardless of provider.

## CPU quality: what the advertised core count does not say

For this workload, the CPU question is not “can twelve CLIs exist?” They mostly
wait. It is whether the machine remains responsive when three agents compile or
test while two-to-four Chromiums render pages. Single-thread speed controls Vite
startup/HMR, many build steps, browser main-thread work and interactive shell feel;
available multi-core throughput controls how badly those jobs interfere with one
another. `tmux`, Panels and Hermes are not material CPU consumers.

Use the following as **purchase-screening heuristics, not measured requirements**:

- minimum acceptable: roughly Geekbench 6 **1,200 single / 7,000 multi**;
- preferred for a Mac-like interactive feel: roughly **1,800 single / 9,000 multi**;
- in physical terms, about **six-to-eight healthy modern cores' worth of sustained
  compute** is enough if full builds/tests are limited to three at once. Twelve
  advertised shared vCPUs can deliver less than this, while eight guaranteed cores
  can deliver more.

Those thresholds are inferred from comparative provider results, not from a replay
of this exact workload. Geekbench is a short test and does not measure an hour of
CPU steal. A single YABS run also cannot establish provider-wide performance, so
the range and the resource contract matter more than one best score.

For scale, the current M4 Pro measured about **3,588 single / 19,954 multi** in
Geekbench 6. None of these budget VPS choices will match its interactive speed;
even a healthy RS 2000 is roughly 35–45% slower per core, while the lower-end
OVH/Contabo samples are about 70–75% slower per core. The VPS purchase buys an
always-on parallel workspace, not a faster replacement for the Mac.

| Plan | What CPU is actually promised/exposed | Current firsthand evidence | CPU judgement |
|---|---|---|---|
| **netcup VPS Lite 4 G12s** | 16 shared vCPU. netcup does not disclose or guarantee the host CPU; the only exact sample exposed a generic QEMU CPU at 1.996 GHz. | One June 2026 owner test measured Geekbench **5** 923/10,188 and sysbench 1T/16T 1,478/18,041. Its 4K Q1 disk result was 7.8k read/9.2k write IOPS. GB5 is not numerically comparable with GB6. ([exact owner test](https://ryanvan.com/t/topic/94)) | Enough aggregate parallel capacity is plausible, but the exact sample confirms weak single-thread performance. This is a **RAM/concurrency value plan**, not a known-fast development CPU. There is still only one exact benchmark and no long-duration steal data. |
| **netcup VPS 4000 G12** | 12 shared vCPU; netcup says VPS CPU performance is not guaranteed. G12 is sold on current EPYC infrastructure, but samples can expose a masked Genoa CPU rather than the exact host model. | GB6 samples span **1,323/8,000** after 48 days of uptime to **1,944/12,913**; an exact June owner test exposed EPYC Genoa at 2.246 GHz and measured GB5 1,117/9,589. ([lower YABS](https://www.vpsbenchmarks.com/yabs/netcup-12c-31gb-20260113-c36f8c), [higher GB6](https://browser.geekbench.com/v6/cpu/15288182), [June owner test](https://ryanvan.com/t/topic/95)) | A materially better interactive-CPU bet than Lite, plus NVMe, but the range proves shared-node/time variance. **Best 32 GB development machine near the budget** if €32–33 is acceptable. |
| **netcup RS 2000 G12** | Eight exclusively assigned AMD EPYC 9645 cores; netcup explicitly contrasts this guarantee with VPS CPU. ([official contract](https://www.netcup.com/en/server/root-server)) | Healthy samples measured GB6 around **1,989–2,298 single / 10,120–11,794 multi**, with roughly 92k–185k mixed 4K IOPS. One June sample fell to 1,461/7,033, and a separate owner documented a badly performing G12 node, so delivery still needs testing. ([direct comparison](https://www.reddit.com/r/hetzner/comments/1sjn46b/hetzner_cpx31_vs_netcup_vps_2000_g12_which_one/), [high YABS](https://www.vpsbenchmarks.com/yabs/netcup-8c-16gb-20250911-a1f581), [bad-node report](https://forum.netcup.de/administration-eines-server-vserver/vserver-server-kvm-server/p255890-root-server-g12-underwhelming-performance/)) | **Best CPU contract and normal measured CPU performance under €30**, but 16 GB RAM is the compromise. Prefer it only if browser concurrency is capped around two and memory measurements support that choice. Test during the 30-day satisfaction window. |
| **OVHcloud VPS-4 2027** | Eight shared vCores; host CPU is a mix, not selectable. A community-posted OVH staff answer lists Xeon E5 v4/Xeon 6242 hosts and says VPS-2–4 may also use EPYC Milan. ([launch discussion](https://www.reddit.com/r/OVHcloud/comments/1u6j4xk/vps_2027_range_is_here_meet_the_newly_released/)) | The exact 2027 VPS-4 is too new for a useful sample set. A May 2026 OVH 8-core/24 GB sample on Haswell measured GB6 **948/4,566**; a current 4-core 2027 sample measured 839/2,713. These demonstrate the low end of the hardware mix, not a guaranteed VPS-4 result. ([8-core YABS](https://www.vpsbenchmarks.com/yabs/ovhcloud-8c-23gb-20260505-d7c3f5), [2027 YABS](https://www.vpsbenchmarks.com/yabs/ovhcloud_us-4c-8gb-20260619-tg10291)) | Better operational bundle than CPU bet: daily backup, network and simple scaling are attractive, but an old-host allocation could deliver roughly half a healthy netcup G12's build throughput. |
| **Contabo VPS 40** | 12 shared x86 vCPU; no host generation or sustained CPU entitlement is promised. Contabo prohibits CPU-heavy mining on VPS because CPU is shared. ([official shared-resource note](https://help.contabo.com/es/support/solutions/articles/103000271608--puedo-configurar-la-miner%C3%ADa-en-mi-servidor-)) | No exact current VPS 40 result was found. The closest current VPS 30 (8 vCPU/24 GB) measured GB6 **937/3,925**. Another owner running three tests over a day found Contabo the most variable and reported about a 40% peak/off-peak swing; older bad-node results are much worse. ([VPS 30 YABS](https://www.reddit.com/r/VPS/comments/1s8kig5/contabo_performance_test/), [variance report](https://www.reddit.com/r/VPS/comments/1tt3b19/ran_benchmarks_on_ishosting_hetzner_and_contabo/), [bad-node YABS](https://lowendtalk.com/discussion/199583/contabo-limits-my-vps-performance)) | 48 GB looks future-proof, but the available evidence says the CPU entitlement may be the smallest of the headline resources. **Do not choose it for build/browser performance without accepting a migration gamble.** |
| **Hetzner CX53** | 16 shared vCPU on older cost-optimised Intel/AMD hardware; Hetzner publishes a baseline-plus-temporary-burst model and does not let customers choose the CPU. ([official resource model](https://docs.hetzner.com/cloud/servers/faq/)) | One EPYC Rome sample measured GB6 **1,202/8,495**. The faster CPX42's three Genoa samples clustered tightly around 1,869–1,927/8,631–8,977, but after the June 2026 increase CPX42 is €69.49 before VAT and is no longer relevant to this budget. ([CX53 YABS](https://www.vpsbenchmarks.com/yabs/hetzner-16c-31gb-20251125-8f0aa2), [CPX42 YABS](https://www.vpsbenchmarks.com/yabs/hetzner-8c-15gb-20260117-49cd60), [current prices](https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/)) | Hetzner has the clearest shared-CPU semantics and good evidence for its newer line, but the affordable CX53 is older/slower and now about €35 with VAT before IPv4. It is not the best current budget purchase. |

### Decisive CPU ranking

There is no option under €30 that simultaneously provides 32 GB RAM, guaranteed
fast CPU and a broad sample of consistent benchmarks.

1. **Best CPU:** netcup RS 2000 G12. Its eight guaranteed EPYC 9645 cores should
   feel best for builds and browsers, but 16 GB is not a future-proof memory tier.
2. **Best balanced development machine:** netcup VPS 4000 G12 at roughly €32–33
   including VAT. It combines 32 GB with much stronger known single-core performance
   than Lite, although CPU remains shared.
3. **Best strict-budget capacity:** netcup VPS Lite 4 G12s. It remains the 12-session
   recommendation if keeping every process/browser resident matters more than fast
   individual builds. The earlier recommendation was too confident about its CPU;
   the only exact test makes that uncertainty concrete.
4. **Best operational bundle:** OVH VPS-4. Choose its daily backup/global platform,
   not its CPU performance.
5. **Most misleading headline:** Contabo VPS 40. More RAM and vCPUs do not overcome
   weak and variable delivered CPU in the nearby plan evidence.

Whichever shared plan is chosen, buy monthly, run repeated tests morning/evening for
several days, and reject/migrate if GB6 single is below about 1,200, multi below
about 7,000, or `mpstat` shows sustained steal while builds run. Also time the real
repo: one clean frontend build, one full backend test run, two concurrent Playwright
jobs and a package install in separate worktrees. Those timings are more decisive
than YABS. For storage, watch 4K random latency/IOPS; headline sequential GB/s does
not predict `node_modules`, Git and SQLite responsiveness.

## What people are actually running

These are field reports, not controlled benchmarks:

| Firsthand evidence | What it tells us |
|---|---|
| A Claude Code VPS user reports **2 vCPU / 4 GB is fine for one session** when the project has no heavy build pipeline; the CLI is mostly I/O and API waiting. ([Reddit report](https://www.reddit.com/r/ClaudeCode/comments/1sv8vrn/claude_code_on_cloud_server/)) | A single cloud-model CLI is not the reason to buy a large server. |
| A Claude Code user running **3–4 concurrent sessions** on a 16 GB machine found duplicated MCP servers (14+ processes) used about **570 MB**; removing roughly 400 MB reduced memory pressure. ([Anthropic issue comment](https://github.com/anthropics/claude-code/issues/26224)) | MCP process duplication and long-lived helpers matter; 16 GB is workable but not lavish. |
| A Codex App user with **10 active worktree threads and 17 MCP servers** reports about **30 GB total memory**. ([OpenAI issue](https://github.com/openai/codex/issues/11324)) | 24–32 GB becomes useful when many sessions each start a full MCP stack. This is desktop-app evidence and therefore an upper-bound analogue, not a CLI measurement. |
| An OpenAI maintainer says Codex CLI memory is normally “pretty tame”; in a reported multi-session OOM, the heavy Python child workloads, not the harness, were the likely pressure source. ([OpenAI issue](https://github.com/openai/codex/issues/11523)) | Size for commands the agents launch—tests, builds, Python and browsers—not model inference. |
| A long-running Claude Code session with a pathological 3.8 GB transcript consumed **12.8 GB RSS** on a 30 GB Ubuntu server. ([Anthropic issue](https://github.com/anthropics/claude-code/issues/22365)) | Rare leaks or oversized session state justify swap, monitoring, and restarting stale sessions; they do not justify buying 64 GB for normal use. |

Official baselines are similarly modest: Anthropic lists **4 GB+ RAM** for Claude
Code ([system requirements](https://docs.anthropic.com/en/docs/claude-code/getting-started)),
and OpenClaw says its gateway is lightweight enough for a small VPS or Raspberry
Pi-class host and that **4 GB is plenty** ([OpenClaw FAQ](https://docs.openclaw.ai/faq)).
OpenClaw's Docker build needs at least 2 GB because `pnpm install` can OOM on a
1 GB host ([official Docker guide](https://docs.openclaw.ai/install/docker)). Those
figures cover the harness/gateway, not eight simultaneous project toolchains.

Browser work is the important multiplier. Playwright recommends only one worker in
CI for stability, warns that Chromium in Docker can run out of memory without host
IPC, and says each installed browser occupies a few hundred MB. ([CI guidance](https://playwright.dev/docs/ci),
[Docker guidance](https://playwright.dev/docs/docker), [browser storage](https://playwright.dev/docs/browsers))
That supports 24 GB as a useful comfort tier, but it does not imply one browser per
agent should run concurrently.

## Current options in budget

| Plan | Current listed resources and price | CPU / architecture | Storage and traffic | Flexibility and verdict |
|---|---|---|---|---|
| **netcup VPS Lite 4 G12s** | 16 vCore, **32 GB**, **640 GB SSD**; **€25.72/month incl. 19% German VAT** | Shared x86 vCore | Traffic included; 1 Gbit/s interface; snapshots supported | One-month term and IPv4 included. **Best 10–12-agent fit inside budget**; slower storage/interface than regular G12. ([official checkout](https://www.netcup.com/en/server/vps/vps-lite-4-g12s-iv-1m)) |
| **OVHcloud VPS-4** | 8 vCore, **24 GB**, 200 GB NVMe; **£20.81/month incl. UK VAT** | Intel x86 VPS | Unlimited traffic, advertised up to 3 Gbit/s; IPv4 and daily automated backup included | One-click upward scaling without data migration. **Best 4–8-agent / lowest-maintenance fit**. Advertised rate uses 12 months prepaid. ([official plan](https://www.ovhcloud.com/en-gb/vps/)) |
| **netcup VPS 4000 G12** | 12 vCore, **32 GB DDR5 ECC**, **1 TB NVMe**; **€32.41/month incl. 19% German VAT** | Shared x86 vCore; CPU is not guaranteed | Traffic included; 2.5 Gbit/s interface; snapshots supported | 12-month term/billing; monthly/no-term costs €4.88 more. **Best near-budget development machine**, but currently above €30 and automatic-EU stock was unavailable when checked. ([official checkout](https://www.netcup.com/en/server/vps/vps-4000-g12-iv-12m)) |
| **Hetzner CX43** | 8 vCPU, **16 GB**, 160 GB SSD; **€15.99/month ex VAT and IPv4** after 15 June 2026 | Shared x86 (Intel or AMD); older cost-optimized hardware and limited availability | 20 TB EU traffic; paid IPv4 | Hourly billing and easy rescale, but cannot cross architecture and cannot shrink an enlarged disk. Flexible trial option, but netcup gives much more disk at a similar VAT-inclusive total. ([official price adjustment](https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/), [plan positioning](https://www.hetzner.com/cloud/cost-optimized), [rescale limits](https://docs.hetzner.com/cloud/servers/faq/)) |
| **Contabo Cloud VPS 30 / 40** | 8 vCPU / 24 GB / 200 GB NVMe for **€14**; or 12 vCPU / 48 GB / 250 GB for **€25**, before tax/add-ons (about €30 at 20% VAT) | Shared x86-64 | 32 TB advertised traffic; three snapshots, not automatic backup | Exceptional paper capacity, but not my first choice for predictable build/test latency. Upgrades are supported; direct downgrade is not—buy and migrate to a new server. ([official pricing](https://contabo.com/en/pricing/), [plan-change docs](https://help.contabo.com/en/support/solutions/articles/103000269700-how-to-make-changes-to-your-vps-or-vds-plan)) |

Contabo's caution is based on anecdotes, not an official performance guarantee:
several customers report noisy-neighbour, disk, network, and support problems, while
some also report years of acceptable personal use. One comparison user claimed a
netcup root server was 5–6× faster in single-core Geekbench than their Contabo VPS.
([multi-provider discussion](https://www.reddit.com/r/VPS/comments/1pmkt6j/contabo_vs_hetzner/),
[Contabo CPU discussion](https://www.reddit.com/r/VPS/comments/1km66ww/high_cpu_usage_on_contabo_server/))
That makes Contabo a defensible cheap experiment, especially if occasional stalls do
not matter, but headline vCPU/RAM should not be treated as equivalent to the same
numbers at another provider.

## Why x86-64, 32 GB, and 640 GB is the expanded-workload balance

- **x86-64:** Codex, Claude Code, Node, Python, Docker and current Playwright all have
  Arm routes, but x86 avoids occasional binary-only dependency and container-image
  friction. Hetzner also prevents rescaling between Arm and x86 architectures.
- **32 GB RAM:** this is the useful floor for twelve persistent sessions plus 2–4
  browsers. It leaves room for Chromium, language servers, package installation,
  duplicated MCP helpers, and builds arriving together. It is not an allowance for
  twelve simultaneous browsers.
- **640 GB SSD:** gives worktrees and duplicated project dependencies room to breathe.
  Worktrees share Git objects, but checked-out files, per-worktree dependencies, build
  outputs, browser caches, Docker layers, and logs do not all share space. The regular
  VPS 4000's 1 TB NVMe is better if install/build I/O is a frequent bottleneck.
- **Shared CPU:** suitable for bursty, personal agent work. CPU contention affects
  completion time, not correctness. Playwright itself recommends controlling worker
  count rather than blindly matching detected cores.

## Operating envelope to start with

On the VPS Lite 4, run Panels/Hermes and up to **12 cloud-model sessions**, but
gate local heavy work:

- start with at most 3 concurrent Playwright/computer-use jobs;
- start with at most 3 full builds or test suites at once;
- 8 GB swap or zram as an emergency buffer, with alerts rather than normal reliance;
- shared Playwright browser cache and package/download caches where safe;
- prune completed worktrees, Docker layers, logs, and stale agent/MCP process trees;
- monitor peak RSS, swap, load, CPU steal, disk latency, free disk, and job duration.

After two weeks, change tier only if evidence says so. Upgrade RAM if swap or OOM is
the problem; reduce local concurrency or seek better CPU if RAM is free but load,
steal time, and test duration spike. For 10–12 sessions, **netcup VPS Lite 4 is the
strict-budget recommendation; VPS 4000 G12 is the better I/O-heavy choice if €32–33
and annual prepayment are acceptable; Contabo VPS 40 is the RAM-first gamble**.
