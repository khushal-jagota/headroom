# How the record-carries-content package was actually built

A literal account of the loop I was in, written for analysis rather than as a retelling.
The numbers are the team lead's, from my transcript: 130 minutes, 446 tool calls, 381 of
them Bash, 102 pytest runs (27 whole-suite, 75 narrow) totalling 60 minutes, against 34
total file changes. About 70 minutes was my own turn latency across sequential calls.

What follows is why.

## The steps, in the order they happened

**1. Read the brief and set up, at the same time.** I started the venv, the pinned
install and both `npm ci` runs in the background and read the kickoff while they ran. This
was the one place I got the concurrency right, and it cost nothing.

**2. Read the system, directly, about fifteen calls.** Contracts, events, storage, the
core, the HTTP layer, all three adapters, the browser's wire, transcript, outgoing and
composer, and the two components. I did not delegate this. That was right: the shape
decision was mine and I needed the detail in my own head, not a summary of it.

**3. Proposed the shape and stopped.** Correct — it was the one step the brief said was
expensive to get wrong.

**4. Built the source.** New content and file-store modules, then the contract, the core,
the in-memory twin, the three adapters, the HTTP layer, the browser. Roughly forty calls,
mostly Python scripts doing exact-string edits. Each one ended with `ruff` and `mypy`,
which cost about a second together. This part was fine.

**5. Then the test migration, and this is where the time went.** Changing `text` to
`content` moved roughly 350 call sites across 38 test files. My loop was:

> run `pytest tests/unit` → read the first failure → work out which *class* of breakage it
> was → write a regex pass to fix that whole class → run `pytest tests/unit` again → find
> the next class → repeat.

Eight or nine times around. Each lap cost 61 seconds of suite plus a turn of my own
thinking. That is the twenty-seven whole-suite runs and most of the sixty minutes.

**6. Wrote the new tests**, per area, each followed by that area's file. This is most of
the 75 narrow runs.

**7. Broke things on purpose to check the tests failed.** Four times: the claude image
block against the real CLI, the file route, prompt rendering, the compaction seam. Total
cost about a minute. The cheapest and most valuable thing I did.

**8. Fanned out for the adapters** — at minute ninety, after the standing order arrived.

## Why the whole suite twenty-seven times

Not fear of losing anything. I was using a 61-second test run as a **search tool**.

The change moved a type through 38 test files and I had no way to enumerate them. `mypy`
in this repo checks `src/` and `tests/typing/`, so a broken test call site is invisible to
every static gate — the only thing that would tell me where they were was running them. So
I ran them, and the suite answered one class of breakage at a time because that is what a
test run does: it stops at the first thing that is wrong and tells you about that.

`.venv/bin/mypy tests/` takes one second and would have listed all 350 at once. I never
ran it. That single command, used once at step 5, would have replaced roughly twenty
minutes with one lap. This is the largest single finding in this document.

## Why 75 narrow runs for 34 file changes

The trigger was always the same and it was never a decision: **I had just edited a file, so
I ran that file.** Two or three runs per file, because each edit was one class of problem
and I edited most files more than once.

That is a check attached to an edit. The edit is not the unit of work — "make the storage
layer carry content" is, and it took four edits. Three of those four runs told me nothing I
would not have learned from the fourth.

## The fan-out, and what stopped it being earlier

I fanned out at minute ninety, immediately after the standing order. Before that I read my
brief as forbidding it, and I read it wrong.

The brief said the record change **"lands alone"**. That means *as its own commit, before
things build on it* — a dependency statement. I read it as *do it by yourself*. Nothing in
it said that, and I never questioned the reading.

The concrete cost: step 5, the migration across 38 test files, was the most parallelisable
work in the whole package. Disjoint files, mechanical, no shared decisions. Three agents on
a third of the files each would have collapsed twenty minutes into seven, and I would still
have made every judgement that mattered because none of the judgements were in there.

There is a second, worse version of the same mistake. When I finally did spawn the three
adapter agents, the tool reported errors and I told the team lead the fan-out had failed.
It had not — they had done the work. I only discovered this when I opened the claude
adapter to write an arm that was already there. I had spent the intervening time preparing
to do their job.

## Where I waited on something I could have done alongside

- **Gates run one per call.** `ruff`, `mypy`, `npm run check`, `npm test`, `pytest`, the
  e2e spec — six commands, frequently six calls, each carrying its own turn. They are
  independent. One call with all six would have cost one turn instead of six. I did this
  dozens of times.
- **Discovery run serially.** In step 2 I read files one after another when the questions
  were independent.
- **The e2e spec run whole (43s) to check one test (6s)**, several times.

## Which rules I read as requiring this

Being blunt, as asked.

- **"Keep `./verify` green; never advance over failing tests."** I read "green" as a state
  to hold continuously, so I re-proved it after every edit. It says do not advance over
  failures — it does not ask for proof between edits.

- **"`./verify` is the only source of truth for completeness. Run it after changes land —
  not mid-work, not to re-confirm a result nothing has changed since."** I obeyed the
  letter — I never ran `./verify` — and broke the spirit by doing exactly that with the
  unit suite twenty-seven times, several of them re-confirming a result nothing had changed
  since. The rule names `./verify`, so I did not apply it to the thing I was actually
  overusing.

- **"Every test you add must fail if its change is reverted."** This one earned its cost
  and I would spend it again. Four revert checks, about a minute, and they caught the claude
  image path being genuinely unproven until I made the real CLI say "I don't see an image
  attached".

- **"Anything that can run in parallel should. Serial work is a cost, and only a real
  dependency justifies it."** This rule already said what I needed. I did not apply it,
  because I had read "lands alone" as overriding it. A rule cannot help when a
  misreading of a brief is allowed to outrank it.

## What I would do differently

1. After any type change, run `mypy` over `tests/` before running a single test. One
   second, all call sites at once.
2. Never run a suite to *discover* breakage. Run it to *confirm* a piece of work.
3. One check per piece of work, not per edit and not per file.
4. Batch independent commands into one call. Six gates, one call.
5. Treat a mechanical migration across many files as fan-out work by default. It is the
   most parallel thing in any package like this and it contains no judgement to keep.

## The rule this changed

A check now follows a piece of work — not a file, and not an edit.
