# Sprint Tracking

Date range: 2026-07-01 to 2026-07-12

## Todo

- Extract more information from the real-life pipeline.
  - Priority: P1
  - Urgency:
  - Mode: Supervised
  - Project: Vylo
  - Use notes and metadata from meeting tools such as Granola as part of the real-life pipeline: participant emails, meeting notes, and other supplied context that can support persona assignment and richer analysis.
  - Related to durable personas, but track it as its own pipeline/input item rather than burying it inside persona modelling.

- Hook extra meeting recorders into the plugin system.
  - Priority: P2
  - Urgency:
  - Mode: Supervised
  - Project: Vylo
  - Add support for additional meeting recorders through the plugin system so new recorder sources can feed Vylo without one-off integrations.

- Complete onboarding in two stages: simple first, magical second.
  - Priority: P1
  - Urgency:
  - Mode: Campaign
  - Project: Vylo
  - Build the simple version first so Vylo captures the right information; then shape the fuller version that feels more magical once the dependent product pieces are clearer.
  - Keep the dependency boundary explicit: do not let the magical version block the useful simple version.

- Shape Google Calendar integration as a major campaign.
  - Priority: P2
  - Urgency:
  - Mode: Campaign
  - Project: Vylo
  - Work out how calendar context helps Vylo understand what is coming up in someone’s life and how it should connect to notifications, durable personas, preparation, and timing.
  - Shape first; build only if it earns space after the personas/readiness work is clear.

- Re-evaluate stale-token sign-out recovery.
  - Priority: P2
  - Urgency:
  - Mode: Supervised
  - Project: Vylo
  - Preserved technical reliability item: check local history/docs, Convex/Clerk auth behaviour, and onboarding redirect blast radius before changing forced sign-out recovery.

- Get the welcome message working properly.
  - Priority: P2
  - Urgency:
  - Mode: Supervised
  - Project: Vylo
  - Figure out where the welcome message should come from, what should generate/control it, and make the opening experience feel intentional rather than random app-greeting confetti.

## In Progress

- Build durable personas into a compounding people / CRM-like layer.
  - Priority: P0
  - Urgency:
  - Mode: Campaign
  - Project: Vylo
  - Make people durable enough that Vylo understands who someone is over time: entity assignment, profile compounding, relationship context, and better use of information across real-life analysis and future product surfaces.
  - Current state: first exploration started on 2026-07-02 in `.claude/plans/durable-personas-exploration.md`; repo grounding and value-layer framing are now captured.
  - Settled so far: durability is about how the layers work, not whether to do them; psychology is computed on write; durable persona creation should be automatic.
  - Still open: future sources, persona-based testing, update behaviour, history/on-write surface, rules placement, commitments, and standing/their-model-of-you.

- Properly review and finish the PWA.
  - Priority: P1
  - Urgency:
  - Mode: Supervised
  - Project: Vylo
  - Check and review installability, app behaviour, device paths, and the preserved PWA/push validation work before treating it as done.
  - Current state: PWA push work was recovered, brought current, and locally merged into `main` on 2026-07-02; it is not pushed yet.
  - Included follow-ons: `Talk to Vylo` long-press installed-app shortcut and onboarding call wake-lock parity with other call surfaces.
  - Still open before shipping: regenerate app icons after the icon-canon decision, set up prod push env, and run the real-device pass / deploy cutover checklist.

- Build notifications as its own system.
  - Priority: P1
  - Urgency:
  - Mode: Supervised
  - Project: Vylo
  - Notifications are linked to PWA but should be treated as their own product/system question: what deserves a notification, how delivery works, how permissions behave, and how this connects to personas/calendar context.
  - Current state: notifications now have a two-layer system: any product event can push to a user with its own copy and deep link, using Apple’s declarative wire format so iOS renders notifications at OS level instead of trusting service-worker rendering.

- Ship a small product-accurate landing page.
  - Priority: P1
  - Urgency:
  - Mode: Supervised
  - Project: Vylo
  - Two gates:
    - Gate 1: good enough to ship and push `main` without embarrassing the product story; in progress as of 2026-07-02 with hero/nav changes and a clarity-block plan, but still not complete.
    - Gate 2: genuinely good landing page.
  - Gate 1 remaining work: shape the clarity block, decide whether extra sections earn a place, settle the “strangers” wording, and align stale page metadata.

- Add microphone support for Vylo.
  - Priority: P3
  - Urgency:
  - Mode: Supervised
  - Project: Vylo
  - Low-priority sprint unlock: let users speak into Vylo where voice input would reduce friction or make the product feel more natural.
  - Current state: implementation progressed substantially on 2026-07-02; local commits wire transcription/voice input into the composer, and the running notes report real-phone validation done.
  - Still open for sprint truth: user closeout/ship state confirmation before marking this sprint item done.

- Finish design leftovers.
  - Priority: P2
  - Urgency:
  - Mode: Supervised
  - Project: Vylo
  - Named remaining slices: app typography pass and finishing `docs/design.md`.
  - `docs/design.md` was completed/updated in a 2026-07-02 repo commit.
  - App typography pass remains open; other design leftovers can be picked up opportunistically rather than tracked as separate sprint tasks.

## Done

- Ship waitlist mechanics.
  - Priority: P1
  - Urgency:
  - Mode: Supervised
  - Project: Vylo
  - Done after the 2026-07-02 5am cutoff: native waitlist mechanics merged to `main` with unauthenticated email signup, UTM attribution, dedupe by email, and no Clerk signup/account requirement.

## Blocked

## Deferred
