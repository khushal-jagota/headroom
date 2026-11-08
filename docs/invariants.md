# Decisions that must stay put

These decisions cut across systems and look removable until a change tries to remove them. A change that crosses one should say so openly. Each line links to the page that explains it.

- **Only sent text reaches an agent.** A row, an event, or a screen is not model context. To change what an agent sees, send it a prompt. See [Conversations](conversations.md).
- **One door moves a stage.** Workers propose, and only the proposal resolver settles stage text and advances a Ticket. The one exception is a Worker type amendment, which can only move Tickets backward. See [Ticket lifecycle](tickets.md) and [Worker types](worker-types.md).
- **The claim starts work and does not watch it.** Starting a step stores one claim and sends one prompt. Nothing waits for the turn to end. See [Worker steps](worker-steps.md).
- **Status is derived.** The claim, a parked proposal, and live blockers are the only stored facts behind a Ticket's status. See [Ticket lifecycle](tickets.md).
- **You may act on anything strictly below you.** One rule decides guarded writes, so a Worker can never approve its own work. See [Who may act](authority.md).
- **Database work waits on its own thread.** The server answers every request on one event loop, so a route does its database writes and its heavy reads on a worker thread. A test over the route table holds this for every route that takes the request connection. See [The product](product.md).
- **The change signal carries no payload.** A commit announces only that something changed. Readers fetch current state, and the database stays canonical. See [Live screens](live-updates.md).
- **Three backends, one home each.** A conversation runs Codex, Claude, or Hermes, and each agent identity keeps one login that Headroom never copies. See [Conversations](conversations.md).
- **The skill row is the instruction.** Skill text lives in the database. Files on disk are copies. See [Skills](skills.md).
- **The accent means a person is needed.** One color marks a gate or a required action, and nothing else. See [Design](design.md).
- **Gates are workflow, not a sandbox.** Stages and authority govern the work record, not what an agent's process can touch. See [The product](product.md).

_Last verified: 2026-11-08._
