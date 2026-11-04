# Projects

A Project is a named catalog entry that classifies work and points at a folder. Projects exist so Tickets, Managers, Ideas, goals, and measures can say what they belong to, and so agents start work in the right place. The catalog is data: adding a Project needs no code change.

## What it is made of

A stable id, a display name, a summary, and an optional folder on disk. The summary is context for people and agents: what the Project is and which paths matter. Headroom stores the folder but does not create it or manage its contents. A repository can live inside it, and the folder is not implied by the name. The catalog has no delete and no archive. It grows by create and changes by edit.

## How work is classified

Managers, standalone Tickets, and Ideas each store a Project. A Ticket under a Manager takes its effective Project from that Manager. Moving a Manager moves its children. Goals and measure sources are scoped to a Project too. A source's datasets must match its Project.

## What the folder does

The folder is where a Worker's conversation starts. On the VPS, an existing folder becomes the conversation's working directory, and a missing one falls back to the default workspace. On the Mac the folder path is used as given. The same folder is where a Ticket's application preview is found. The summary does not enter a prompt by itself. An agent that wants it reads the Project.

## Where it shows up

Project selectors appear in Idea capture and the Ticket and Manager editors. `panels project` and `/api/projects` read and write the same catalog.

## Code

`src/planner/projects/`. The folder rule is in `src/planner/runtime/conversation_start.py`, and previews read it in `src/planner/previews/`.

_Last verified: 2026-11-04._
