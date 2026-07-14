# Independent plan review

## First review — findings

1. `connection-status-plan.html:157-175` hid worker presence on mobile, conflicting with the accepted requirement that worker presence and connection health remain separate signals across desktop and mobile.
2. The heartbeat message contract and exact state transitions were not pinned.
3. Recovery ownership was underspecified; `ws.ts` must not reach into generic cache internals or debug stats.
4. Browser proof did not distinctly require both cursor replay/keyed invalidation and one-time recovery reconciliation.

## Disposition

1. **Addressed.** The tracked and managed planning artifacts keep worker presence visible on mobile. The contract now requires both signals and mobile proof.
2. **Addressed.** The contract now fixes heartbeat shape as `{events: [], cursor}` without cursor advancement, flush, mapping, or invalidation; it defines startup, valid-frame, close, missed-heartbeat, Offline, indefinite retry, and recovery transitions.
3. **Addressed.** Recovery is explicitly a `resourceCatalogue.ts` API over a narrow subscribed-key query from the generic cache. `ws.ts` cannot inspect cache internals or debug stats.
4. **Addressed.** Browser coverage must separately prove retained-cursor missed-event mapping and no-event subscribed-resource reconciliation without reload or remount.

A corrected read-only review followed before implementation.

## Corrected review

`NO VIOLATIONS`
