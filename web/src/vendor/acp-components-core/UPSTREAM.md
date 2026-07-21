# Upstream provenance

- Repository: https://github.com/zvzuola/acp-components
- Package: `packages/core`
- Commit: `525a9d83c5ace577ac0417bf82bf983da4042663`
- Copy date: `2026-07-19`

## Copied paths

| Upstream path | SHA-256 |
| --- | --- |
| `packages/core/src/types/index.ts` | `7e85ca21deb274ed4d28311e8249a34632f2a86d0cedace28cee379d27b8cfd4` |
| `packages/core/src/store/sessionStore.ts` | `6d89afc95fbc2758d00293feb6da2c234781403e49abb3dc1d54a87aa34d902f` |
| `packages/core/src/utils/id.ts` | `74deabced24a6e5342c2aa43c0df6b515d45a0496d144ce792aa74051315a939` |
| `packages/core/src/transport/types.ts` | `c81be948d5dc9c458ad6fe06ae262154ee242e33f58cd6848e5bab79b9d22343` |

## License evidence

The pinned `packages/core/package.json` declares `"license": "MIT"`. The upstream repository did
not contain a standalone license file at this commit, so this vendored slice includes the standard
MIT license text in `LICENSE` and attributes it to the upstream contributors.

## Intentional local corrections

ACP-03 modifies only `src/store/sessionStore.ts` after the ACP-00 byte-for-byte copy. Its current
SHA-256 is `bc6f745bdab898243aa5566e3333a968ba1c52b95156ff4b57a0a6e912203aaa`.

The local corrections are:

- exported pure helpers with injected message identity and timestamps, while the Zustand API remains
  a compatibility delegate;
- thought parts use their own helper and default closed;
- adjacent unannotated text coalesces only within a content/thought run, so typed parts flush it;
- one stable plan part and owner message are replaced by later full snapshots;
- tool updates reconcile by tool-call ID, preserve omitted content and locations, normalize explicit
  null collections to empty collections, and preserve UI-only expansion;
- new tools default closed and thought/tool expansion is updated immutably.
