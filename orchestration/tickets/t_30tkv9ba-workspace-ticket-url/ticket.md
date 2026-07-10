# Ticket t_30tkv9ba — Reflect open Workspace ticket in the URL

## Accepted success

Workspace ticket selection is URL-addressable: opening or switching tickets updates the browser URL; visiting, refreshing, bookmarking, or sharing that URL reopens the selected ticket in Workspace; browser back/forward traverses prior selections; and plain Workspace keeps its existing default view.

## Accepted boundaries

- Keep Workspace as one screen with its existing ticket rail and shared embedded `TicketRoute`.
- Use the existing hash router rather than parallel navigation state.
- Preserve `#/workspace`, the legacy `#/board` alias, and the persisted Hide done choice.
- Missing or unknown ticket IDs fall back safely to the Chief of Staff view.
- Browser behavior is proved through Playwright and the final `./verify`.

## Relevant existing code

- `web/src/App.svelte` owns hash parsing and screen routing.
- `web/src/routes/BoardRoute.svelte` currently owns Workspace ticket selection as component-local state.
- `tests/e2e/test_chief_of_staff.py` covers the Workspace default, ticket inspector, Chief of Staff return, and legacy board alias.
