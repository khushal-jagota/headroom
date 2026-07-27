# Nothing puts Panels' role skills in the agent home

## Why this ticket exists

A skill is how an agent learns what it is, and an agent can only read one from its own
home. Nothing in the tree puts them there any more.

Both sides of the staging merge provisioned them, each from a place the other side had
deleted: upstream while composing the Hermes backend, in `conversation/backend_catalog.py`;
this branch while importing live state, in `environments/materialize.py`. The merge kept
both deletions, so the call site went with them. `provision_planner_home_skills` still
exists in `environments/hermes_home.py` and is still covered by its own tests — it simply
has no production caller.

## What this costs, and when

Not immediately. The links in a live agent home were written by the deploy that is running
now and keep working while that release is on disk. They break at the next deploy that
prunes the release they point into, and then every agent on that machine loses its role
skills at once.

So this is due before the release currently deployed to the VPS is pruned.

## Why it was not simply restored

It was restored, on the server's startup path, and reverted in the same session. Putting it
there makes `/api/skills` fast on the first request after a start, because the copy it
would otherwise do has already happened — and that reliably triggers
[the Agents screen's stale render](../agents-screen-stale-render/contract.md), which turns
a rare failure into one that happens on most cold starts.

That bug is worth fixing on its own terms. But provisioning must not be what depends on it.

## The shape to aim for

Provisioning an agent's home is machine provisioning, not something a web server should do
as a side effect of booting. The deploy already installs the app and owns the release
layout; it is the natural owner of the skills in the home beside it, and doing it there
also means it happens once per deploy rather than once per restart.

Whatever is chosen has to be proved against the deployed VPS, not only locally: the
failure this prevents is one that only shows up on a machine that redeploys.

## Done when

A deploy leaves every name in `PLANNER_SKILL_NAMES` resolving inside the agent home, the
old release can be pruned without breaking them, and the Agents screen is unaffected.
