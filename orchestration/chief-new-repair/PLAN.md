# Chief `/new` repair

The live failure has two independent causes which meet at fresh-session startup. They are split so
each implementation has one contract and one focused regression.

## t_new01 — Native fresh-session command

Make `/new` create and bind a new Hermes session directly. It must not enter the noninteractive
slash worker, resume the old session first, or wait for Hermes CLI confirmation. The returned durable
key must reach Panels before the command completes so the next Chief message uses the new live handle.

## t_new02 — Stable Hermes home

When `PLAN_HERMES_HOME` is not explicit, derive the dedicated Hermes home from the configured planning
database directory. A process serving an absolute canonical database from another git worktree must
therefore keep using the canonical database's Hermes home instead of a new worktree-local profile.

## Integration order

The tickets may be implemented in parallel: t_new01 owns shared-gateway command transport and t_new02
owns startup home resolution. Integrate t_new02, then t_new01, run one independent full-diff review,
one canonical `./verify`, and restart the live listener from the redesigned frontend worktree.
