# Projects

A Project is a named catalog entry: a stable id, a display name, an optional folder on disk, and a summary. The catalog is data. Adding a Project does not require a code change, and the set is not a fixed enum. The summary is the context humans and agents should remember: what the project is, what matters, and which paths are worth knowing. Headroom does not manage the folder’s contents and does not create the folder. A path that starts with `~` is stored absolute. The folder may be missing. Clearing the path stores null.

A repository can live inside that folder. The folder is not implied by the Project’s name.

## How classification works

Managers, Ideas, and standalone Tickets store a project id. A Ticket that sits under a Manager takes its effective Project from that Manager. Moving the Manager moves the children’s effective Project, and it updates schedule templates assigned to that Manager so later occurrences follow. Writes accept a project id or the legacy display name. Sending both, pointed at different rows, is rejected.

There is no delete and no archive. The catalog grows by create and changes by editing the name, summary, or folder.

## What it feeds

Project choice feeds Ticket and Idea capture, Manager headers, and the locale default: a Project marked as requiring macOS selects the Mac locale for future Ticket creation when the caller did not pass a locale. It does not move Tickets that already exist. The summary feeds anyone who reads the Project, including an agent that loads it into a prompt on purpose. Headroom does not push the summary into a conversation by itself.

The removed global Workers page used to filter by Project. Workspace now groups by Chief and Manager. Project remains visible text on the Ticket and the Manager.

## Where it shows up

Selectors on Idea capture, Ticket and Manager editing, and schedule templates. `panels project` and `/api/projects` are the same catalog. A list read must say whether it wants a summary or the full record.

## Pointers

`src/planner/projects/`. Selectors in the Ticket, Ideas, and Manager screens read that resource.

_Last verified: 2026-10-30._
