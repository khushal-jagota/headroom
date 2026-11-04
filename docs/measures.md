# Measures

A measure is a dated number that belongs to a Project: monthly revenue, events per day, hours logged. Measures exist so that goal charts and completion rules read one shared, retained set of numbers instead of each asking a provider or an agent. [Goals](goals.md) owns what a goal does with them: charts, finish lines, and manual controls on a page.

## What it is made of

- **Measures.** A name, unit, Project, and timezone. Kind (reading, running total, period count) and period are descriptive. They do not restrict arithmetic.
- **Sources.** A saved Python script that collects values for a Project, run on a schedule or on request. A source has a responsible Chief, Manager, or goal. That owner, and Workers it authorizes, edit and run it. A goal agent can manage a source for its own goal and no other.
- **Datasets.** A retained table owned by a source: rows by date, with category dimensions and numeric fields. A measure is one number per date. A dataset is a breakdown, such as events by team. Goal charts read both.
- **Credentials.** Named API keys that scripts look up by name. The names are descriptive and reusable, not owned by a Project. Values are stored encrypted in the database, with the key in a separate file that is backed up alongside it. A missing key fails loudly and never resets records. An agent asks for a key through a card in its conversation, and the owner saves it there. A secret never travels as a chat message.

## Three kinds of measure

```
 source script ───────────────► taken ─────┐
 formula over other measures ─► assembled ─┼─► retained dated values ─► goal charts and rules
 owner or agent entry ────────► manual ────┘
```

- **Taken.** Collected by a source script. Each value points to the run that wrote it.
- **Assembled.** Calculated from other measures: constants, references with day offsets, arithmetic, minimums and maximums, elapsed days, date-based phases, and daily sums over a day, week, or month. It reads retained values only, never a provider, and cycles are refused. Goal expectation lines use the same evaluator.
- **Manual.** Typed in by the owner or Chief. A goal agent can write a manual measure bound in its own goal.

## How collection works

A source runs its script as an ordinary child process in the application's Python environment. The owner chose trusted scripts and simple secrets over a sandbox. The script has installed packages, files, and network access. Naming credentials by key avoids embedding secrets in code, and is not a security boundary.

The script emits dated values through runtime helpers. Printed output creates nothing. Headroom checks only that numbers are finite, dates valid, and outputs owned by the source. Meaning belongs to the script: signs, units, and whether a period is finished.

A source runs daily at a server-local time, or when someone requests it. A test run returns candidates without changing anything. A collection run overwrites each date it supplies, including corrections to past dates, and keeps all other dates.

A failed collection keeps the prior values and notifies the responsible agent. The agent can fix the script and rerun, or ask the owner for information or a new key. Only a successful collection clears it.

## Missing and partial

A missing value stays missing. A script can say a provider has no history, and Headroom never turns that into zero. A period count also records whether the queried interval covers the whole day, week, or month: complete, partial, or unknown. This is a fact about the data. A partial value still compares against targets. Formulas never turn partial or unknown inputs into complete ones.

## Where it shows up

There is no measures screen. Values appear on goal pages, and manual controls sit on goal charts. Agents use `panels measure`, `panels dataset`, `panels measure-source`, and `panels credential`. Each `schema` subcommand prints accepted request shapes, so agents read it before a write. [Attention](attention.md) retains its daily figures through this runtime. See [CLI](cli.md).

## Decisions that shape it

- Another provider needs a script and configuration, not a deployment.
- Trusted scripts, not a sandbox.
- Retained values are the one thing charts and rules read. Reads never reach providers.
- Missing is never zero.
- A source's authority follows its responsible Chief, Manager, or goal.

## Code

- `src/planner/measures/`: definitions, formulas, coverage, manual writes.
- `src/planner/measure_sources/`: sources, the script runtime, scheduling, failure notices.
- `src/planner/datasets/` and `src/planner/credentials/`.

_Last verified: 2026-11-04._
