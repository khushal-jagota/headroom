---
name: "panels-worker-release-prep"
description: "Stage-by-stage guidance for preparing one release candidate and returning a ready-or-not verdict."
---

# Release Prep

A release prep worker readies one release candidate. The Chief starts the Ticket with a candidate staging commit. The worker returns a ready-or-not verdict with evidence, and a summary of what Khushal can expect at Update. The Chief opens and merges the release pull request into main, and Khushal presses Update.

### The stages

The sequence is **Brief → Prep Plan → Readiness → Consequences → Done**. Prep Plan is the Chief's gate before the expensive work.

### Standing rules

- **A release is two commits.** Live is the commit in the live app's manifest. The candidate is a full staging SHA. Every check names the full SHA that it ran on. The checkout tracks only `origin/staging`. Fetch other commits by full SHA, and fetch main with `git fetch origin refs/heads/main:refs/remotes/origin/main`.
- **Never release.** Do not open or touch a staging-to-main pull request, push to main, restart the live server, or free a port. Stop your own processes by the PID that you recorded.
- **Your changes.** Merge the iPhone review re-pin into staging yourself, by pull request when `typecheck (merged tree)` passes, also during a staging hold. Fix only the release records and the scripts in `scripts/release-prep/`.
- **A product failure is a finding.** Stop that check, send the Chief the evidence, and continue the checks that do not depend on it. The Chief routes the fix.
- **Reuse, then keep.** Use the existing gates and the scripts in `scripts/release-prep/`. Put a new or changed release script there, not only in the Ticket folder. The Ticket folder clears seven days after the Ticket ends.
- **Mac work goes to a Mac Ticket.** Workers cannot create Tickets. Send the Chief an exact brief and wait for the result.
- **The GitHub budget is shared.** Use one wait with `--interval 300`. Never rerun only the failed jobs of a qualification run. Rerun the whole run.
- **Hard pieces go to a subagent.** You run on Sonnet. Hand the rehearsal analysis, a failure diagnosis, or a tangled byte-for-byte trace to an Opus subagent with a self-contained brief. Check what comes back before you use it. Ask the Chief for a restart on Opus only if the subagent is not enough.
- **Automatic gates.** If a manual check could become an automatic gate, note the option in the evidence. Do not build it.

Follow `panels-worker` for the shared ownership, proposal, approval and communication rules. This skill defines only the release prep work.

## Brief

Stage: `needs_brief`. Owner: worker. Result: `brief`.

- **needs\_brief** — the candidate commit, the live commit, and what the release contains.
### needs\_brief — name the candidate

A good Brief is about five lines. It gives the full candidate SHA, the live SHA, and the pull requests in the range in plain words. It adds anything that the Chief already knows, for example a planned floor raise or known noise. The Chief writes most of it at creation. Fill only the gaps, and do not decide anything here.


## Prep Plan

Stage: `needs_prep_plan`. Owner: worker. Result: `prep_plan`.

- **needs\_prep\_plan** — the decisions with reasons, the checks to run, and any Mac Ticket brief.
### needs\_prep\_plan — decide what this release needs

This is the Chief's gate before the expensive work. The Chief must be able to approve it at a glance. Read the `live..candidate` diff, not only the pull request titles.

A good **Prep Plan** gives each item in one line with its reason:

- **Rehearsal on a copy of live.** Khushal's rule: run one when the release changes stored data, the schema (a new step in `server/core/database/schema_steps/`), the conversation view, startup, or the update itself (installer, snapshot, update record). Name the file or pull request that triggers it. If the rule is unclear for a change, say yes. The 25 Nov Chief disconnect escaped every review. Remember that the update runs the code of the running release, not the candidate.
- **iPhone floor.** Compare the candidate's iPhone contract digest with `server/ios_compatibility/review.json`. If the contract changed, name the routes that changed and say whether installed builds still work. Say whether the floor rises against the floor that production main records, and whether a new iPhone build is needed.
- **Other client floors.** These include the desktop bridge floor, and any floor that the diff raises.
- **Machine kinds.** List every supported home kind (Linux x86_64 and Apple Silicon today) and the artifact that each one receives.
- **The checks to run.** A short list.
- **The Mac Ticket brief,** if Mac work is needed. Give the exact staging commit, the steps, what to return, and the Mac rules: never take over Khushal's screen or ask for control of his apps, no macOS permission prompts, no searches outside the Ticket folder and TMPDIR, Simulator only through simctl and xcodebuild, and no git push or git credentials on the Mac.
- **The cost.** The expected time and disk. The last copy of live took 18 GB.


## Readiness

Stage: `needs_readiness`. Owner: worker. Result: `readiness`.

- **needs\_readiness** — the prep work done, then the ready-or-not verdict with evidence and the Update summary.
### needs\_readiness — do the prep and give the verdict

Run the checks in parallel where you can. The Mac Ticket runs while the VPS checks run.

- **Re-pin first.** The re-pin merge moves staging. Every later check names the staging head after that merge. Confirm that the merge changed only `review.json`.
- **Staging gates.** Every required run is green on that commit. The browser gates (`typescript-backend.yml`, workflow_dispatch on staging) pass. Name known noise with its run link, for example a macOS runner capacity cancel.
- **Rehearsal on a copy of live,** if approved. Copy live read-only. Remove the custody rows, the push devices with their delivery rows, and the machine credentials. Turn scheduled work off, use a private port, and run in the foreground. Measure each schema step, the time to ready health, the snapshot downtime and the time to the first Chief view. The bar: the Chief, every Manager, the largest Tickets and Review load and stream in a browser, and owner sends behave. Name expected refusals apart from real failures, for example "Waiting for VPS 1" when no machine is joined. Delete the copy. Show that live is unchanged: health, skill links, routes and units.
- **iPhone.** Run private test servers from the live and the candidate commit on fresh databases. Send both the requests of the installed builds, and name the results that differ. If a new build is required, the Mac Ticket builds it from the stated staging commit, checks it against both servers, uploads it to TestFlight, and confirms that it is valid. That build must be on TestFlight before the verdict can be "ready".
- **Byte for byte.** For every machine kind, show that the artifact that the gate qualified is the artifact that publishing ships. Follow every step after qualification, for example signing. On 26 Nov a signed Mac package differed from the qualified unsigned package.
- **Moved candidate.** The same Ticket continues on the new commit. Always rerun the iPhone digest and the rehearsal decision. Rerun other checks only if the new commits touch them, and name them. If a decision changes and needs new expensive work, ask the Chief first.

A good **Readiness** opens with the verdict: "ready" or "not ready", with each blocker, its evidence and who must act. Then comes the **Update summary**, written for Khushal so that the Chief can forward it unchanged. It says what the release contains in plain words. It says what to expect at Update: the downtime from the measured snapshot time, a one-time macOS prompt, a machine that updates late, and the iPhone build that he must have installed. A floor raise always appears there. Put the evidence in an artifact and link it.


## Consequences

Stage: `needs_consequences`. Owner: worker. Result: `consequences`.

- **needs\_consequences** — the cleanup verified, and staging still at the verified commit.
### needs\_consequences — clean up and confirm nothing moved

Merge any release script changes into staging by pull request. Then propose a short verified report:

- Nothing reached main. Say this first.
- The copy of live is gone: no folder, no listener on its port, and no process.
- Every test server and browser is stopped.
- The re-pin is on staging.
- Staging head still equals the verified commit, or the report names what moved after the verdict.


## Done

Stage: `done`. Owner: terminal. Result: `none`.

- **done** — finished.

