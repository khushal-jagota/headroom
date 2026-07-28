# Implementation plan review

## Findings

### P1 — supported image coverage is incomplete

The contract requires coverage of the supported media policy, and
`pendingImages.ts` accepts PNG, JPEG, GIF, and WebP. The plan names only PNG
and WebP as accepted cases. Add a table-driven admission case for all four
media types while retaining the SVG, HEIC, text, empty, and oversize rejection
cases. This is current public policy, not a production expansion.

### P1 — reconnect completion is not yet awaitable

The proposed controlled second `readEventsAfter` promise proves that reconnect
starts, but resolving it does not itself give the test a promise for the
continuation because `onTrouble` deliberately returns `void` and starts
`connect()` with `void connect()`. An immediate assertion after resolving the
read can race the microtask that opens the replacement tail.

Have the fake `openTail` resolve a second deferred when the replacement tail
opens. After resolving the reconnect read, await that deferred before asserting
the new tail position, feed, and callback count. Assert the synchronous
`close-old-tail` then `read-events` order before resolving it.

### P2 — several cited ranges have overlapping or wrong domain ownership

Correct the plan before porting so one legacy assertion has one owner:

- Feed owns liveness decisions at lines 385–399 and 415–469; transcript owns
  stopped-row/ask projection at 400–413 and 426–427.
- Transcript owns 2043–2097. Remove its claimed “legacy-text portion” of
  2011–2039; those wire normalization and file-address assertions belong wholly
  to `conversation-wire-values`.
- Outgoing's pure post-read transition is 993–1006, not 993–1022. Storage owns
  the boundary/storage work beginning at 1008 and should not repeat the pure
  post-read assertions.
- Thread items owns 1464–1520 and 1627–1673. Feed owns the frame-liveness cases
  at 1523–1549; the current `1464–1540` range overlaps them.
- Wire values owns only the direct capability assertions at 663–664 from the
  composer section. Composer delivery owns 649–662.
- Composer asks' final placeholder range runs through 1986, not 1977.

### P2 — the typed shared fixture omits a three-suite event

`permission_asked` is needed by feed liveness, transcript ask projection, and
thread folding. Add a typed `permissionAskedEvent` builder; otherwise the plan
duplicates the same discriminated event across three suites despite its stated
sharing threshold. Keep permission answers and other less-shared variants
typed inline.

### P2 — two TypeScript test seams need explicit instructions

- The unknown-live-frame characterization cannot be passed to
  `feedWithLiveFrame` as an ordinary literal because `ConversationLiveFrame` is
  a closed union. Permit one narrow cast at that untrusted-input seam, rather
  than weakening the shared builders or production type.
- In the storage reload case, reassign the module variable to the fresh dynamic
  import after `vi.resetModules()` and use only that new module's functions
  thereafter. Retaining old imported functions would leave two live reservation
  ledgers and invalidate the reload proof.

## Checks that passed

- Vitest 4.1.10 supports ordinary TypeScript imports, `vi.resetModules`,
  `vi.stubGlobal`, spies, and fake dates under the current Node environment.
- Node 22.22.3 supplies `File`, `btoa`, and object-URL functions. `window` and
  `EventSource` are absent, but neither is needed at module import time.
- A retained `MemoryStorage` plus a fresh dynamic import is a valid reload seam.
  Make its five-million-character quota or the equivalent stored-length
  assertion explicit when proving the conservative storage envelope.
- The proposed package script removes exactly one of the current ten legacy
  commands, leaves the other nine in order, and runs Vitest once.
- The twelve-way split is feasible below 600 lines. The largest proposed suite
  owns roughly 320 legacy source lines before Vitest structure and shared
  builders; no thirteenth suite is presently justified.

## Result

FAIL — amend the plan for the P1 coverage/sequencing issues and the bounded P2
ownership and fixture corrections before implementation.

## Re-review

- **Resolved — supported image coverage.** The pending-image suite now names
  table-driven admission for PNG, JPEG, GIF, and WebP, plus the existing empty,
  unsupported, and bounded rejection cases.
- **Resolved — reconnect completion.** The feed plan now uses a second deferred
  resolved by the replacement `openTail`, awaits it after resolving the read,
  and checks synchronous close-before-read ordering.
- **Unresolved — line/domain ownership.** The feed/transcript, transcript/wire,
  thread/feed, wire/composer, and composer-placeholder ranges are corrected.
  The outgoing split is not: `conversation-outgoing` now correctly owns
  993–1006, but `conversation-outgoing-storage` still says to migrate the
  inclusive range 928–1093. Change that storage range to **928–992 and
  1008–1093**, or explicitly state that 993–1006 is excluded.
- **Resolved — shared typed fixture.** The plan now adds
  `permissionAskedEvent` for its three consumers.
- **Resolved — TypeScript and reload seams.** The plan permits one narrow cast
  for the unknown external live frame and requires a mutable module variable
  to be rebound after `vi.resetModules()`, with only the fresh module used
  thereafter.

**Final result: FAIL.** One bounded P2 ownership ambiguity remains; amend the
storage range before implementation.

**Final bounded re-review: PASS — the outgoing-storage range now excludes 993–1006 and assigns only 928–992 and 1008–1093.**
