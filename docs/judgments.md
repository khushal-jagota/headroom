# Ticket judgments

A Ticket judgment holds the user's assessment of finished work. It stays separate from
the Ticket's workflow fields because it describes the result, not a step that moves the
work forward.

The first judgment signal is an optional verdict. A verdict contains a rating, text, or
both. The rating has five stable values:

1. **Awful**
2. **Poor**
3. **Good**
4. **Great**
5. **Extraordinary**

The text records what the user liked or disliked.

Only a direct user can change a verdict, and only while the Ticket is done. Clearing a
verdict keeps the Ticket's judgment record available for future signal types. A Ticket
can finish without a judgment.

The Ticket screen shows the add and edit controls on a done Ticket. If a Ticket returns
to an earlier Stage, its saved verdict remains visible without edit controls. Panels has
no judgment list, filter, or analysis surface.

A Ticket worker can record a short trouble note during its active claimed worker step.
Use `panels worker trouble --body-file PATH`. The command accepts one nonempty line of
at most 500 characters. It records trouble that the worker encountered. It does not
rate the worker or record what went well.

Trouble notes are append-only and keep their creation order. The Ticket screen shows
the notes and their recorded times near the verdict. Panels does not provide edit,
delete, browse, filter, or analysis controls for trouble notes.

_Code paths:_ `src/planner/judgments/`, `web/src/components/TicketVerdict.svelte`,
`web/src/components/TicketTroubleNotes.svelte`.

_Last verified: 2026-08-10._
