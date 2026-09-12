# Headroom

**An agent harness designed to reduce mental load.**

Run agents on your computer or server. Give them work, review what they produce, and talk to them from your phone.

Headroom keeps the priorities, plans, conversations, and approvals together. You choose what matters and where you want to be involved.

## Use the agents you already use

Codex, Claude Code, and Hermes are supported today. Choose the agent, model, and reasoning level for each task. You can have different tasks running with different agents. The Backends screen shows the remaining allowance and reset times reported by Codex and Claude.

Use your existing Codex or Claude login, including subscription access where your plan supports it. Headroom runs the agents themselves and uses their existing authentication. Your provider's usage limits and billing still apply. See [Codex authentication](https://developers.openai.com/codex/auth) and [Claude authentication](https://code.claude.com/docs/en/authentication).

## Work from your phone

The agents run on the host machine. You use the browser on your phone or computer to give instructions, answer questions, read results, and approve work.

Add it to your home screen. Dictate a message or a change to a plan. Get a notification when something needs approval or an agent needs your help. Choose which notifications you want.

Your host needs to stay running and be reachable from your phone. Phone push notifications need HTTPS; on iPhone, they use the home-screen app.

## Decide what deserves your attention

You can ask an agent to work out a plan and stop before implementation. Read it, change it, or send it back. When you're happy, let the agent continue further.

Set this boundary for each task. Straightforward work can move ahead; work with important decisions can wait for you. The Review screen brings the waiting proposals and questions together.

## See what the agent made

Open a plan, document, image, or HTML prototype alongside the conversation. Read it and tell the agent what to change without losing the task you're discussing.

For coding tasks, links to supported local development servers can open through Headroom. You can look at the running work from the same interface you use to direct it.

## Get help choosing the work

Keep ideas in the backlog, choose priorities across projects, and decide what belongs in the current sprint and today.

Planning workers help review the previous day, agree today's direction, and check how it's going later. You can discuss the work with a Chief of Staff agent, too.

The point is to spend less attention remembering what needs doing, and more on deciding what's worth doing.

## Give different work a different process

Coding, research, debugging, product design, and exploring an unclear idea need different kinds of attention. Headroom has workflows for each, with their own stages and instructions.

A research task can work through a defined question. An exploration task can pause to think with you. A design task can bring you in for the wireframe and design. You can also define new kinds of worker.

Each task keeps its context, decisions, results, and conversation. Come back later and see where it got to.

## Run it yourself

Headroom runs on one host with a SQLite database. The work record stays on that host; agents connect to their configured model providers.

Formerly **Panels**. The app and command-line tool still use the `panels` name.

## Run locally

Requires **Python 3.12+**, **Node.js 22+**, and npm. Run these commands from a source checkout:

```sh
git clone https://github.com/khushal-jagota/headroom.git
cd headroom

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install --editable .

npm ci --prefix web
npm ci --prefix agent_backends
npm run build --prefix web

panels serve
```

Open [localhost:8767](http://127.0.0.1:8767). The server binds to loopback. Configuration lives in [`config.yaml`](config.yaml), with `PLAN_*` environment overrides; for example, `PLAN_PORT=8768 panels serve` selects another port. SQLite data and runtime files live under the gitignored `data/` directory by default.

Before starting agent work, configure and authenticate the backend you intend to use:

- **Codex:** install the `codex` CLI and make it available on `PATH`.
- **Claude Code:** the backend dependencies install the tested CLI; `PLAN_CLAUDE_EXECUTABLE` can select another executable.
- **Hermes:** configure an installed Hermes environment with `PLAN_HERMES_PYTHON` pointing to its Python interpreter.

Voice input uses Groq transcription; set `PLAN_GROQ_API_KEY` to enable it. This is separate from your agent subscription.

Choose the appropriate Worker, Model, and Reasoning settings before approving a new Ticket's kickoff. Provider authentication and access are separate from this repository's installation. See the [conversation system](docs/conversation-system.md) and [Worker settings](docs/worker-types.md) for details.

Headroom runs on one host and stores its work record there. Agents still use their configured model providers. Ticket gates govern workflow progression; they are not an operating-system sandbox.

## Documentation

- [System map](docs/README.md) — the main documentation entry point.
- [Tickets and gates](docs/tickets-and-gates.md) — stages, ownership, scope, and approval.
- [Worker orchestration](docs/worker-orchestration.md) — how ready work starts.
- [Days](docs/days.md) and [Sprints](docs/sprints.md) — planning and commitments.
- [CLI](docs/cli.md) — direct actions and agent commands.
- [Deployment](docs/deployment.md) and [backups](docs/backups.md) — operating an installation.

## Development

The backend is Python/FastAPI with SQLite; the browser app is Svelte/Vite. Start with [`AGENTS.md`](AGENTS.md) for code ownership and the working rules. Run `./verify` for the repository's full integration check.
