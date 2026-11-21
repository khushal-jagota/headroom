# Projects

A Project is a named catalog entry that classifies work and points at a folder. Projects exist so Tickets, Managers, Ideas, goals, and measures can say what they belong to, and so agents start work in the right place. The catalog is data: adding a Project needs no code change.

## What it is made of

A stable id, a display name, a summary, an optional folder on disk, and an optional Git repository URL and default branch. The summary is context for people and agents: what the Project is and which paths matter. Headroom stores the folder but does not create it or manage its contents. The catalog has no delete and no archive. It grows by create and changes by edit.

## How work is classified

Managers, standalone Tickets, and Ideas each store a Project. A Ticket under a Manager takes its effective Project from that Manager. Moving a Manager moves its children. Goals and measure sources are scoped to a Project too. A source's datasets must match its Project.

## What the folder and repository do

When a Project has a Git repository URL, every machine prepares new Ticket checkouts by cloning that URL (on the Project's default branch if set, or the remote's HEAD branch otherwise). A Project without a repository URL gets a plain Ticket workspace folder. Neither the home nor remote machines inspect local folders to discover a repository.

The optional folder on disk is where a Ticket's application preview is found on the home. The summary does not enter a prompt by itself. An agent that wants it reads the Project.

## Where it shows up

Project selectors on Idea capture and Ticket and Manager editing. `panels project` and `/api/projects` read and write the same catalog.

## Code

`server/projects/`. Workspace preparation is in `server/ticket_workspaces/filesystem/preparation.ts`, conversation start is in `server/runtime/conversation_start.ts`, and previews read the folder in `server/previews/`.

_Last verified: 2026-11-18._
