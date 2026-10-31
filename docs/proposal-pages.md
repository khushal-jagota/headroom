# Proposal pages

A Worker can file the current stage’s blank as a page instead of a block of prose. The page is one document the holder can scan: a title, a short summary, and the blocks that make the case. Headroom stores that document on the proposal. Approving it stores the same document as the field value, so the Ticket keeps showing the page.

The page uses the same kit a goal page uses. Goals are dashboards, with measures, completion, and files of their own. A proposal page is only the blank for this stage. See [Goals](goals.md) for what a goal does with the kit, and [Proposals and gates](proposals.md) for who may file and when the filing parks.

## How a page is filed

Only that Ticket’s Worker may file, through the same propose command as prose. The body is either ordinary text or one JSON document:

```
panels worker propose <ticket-id> <<'EOF'
{
  "kind": "headroom.page",
  "version": 1,
  "title": "Ship the kit",
  "summary": "The holder can read the plan on the approval card.",
  "blocks": [
    { "type": "text", "text": "Prose proposals stay prose." }
  ]
}
EOF
```

`kind` is `headroom.page` and `version` is `1`. `title`, `summary`, and `blocks` are required. A block may omit its id. Headroom keeps an id once the page is stored, so a later edit does not reshuffle the page. The worked example is `src/planner/page_kit/examples/proposal-page.json`.

Prose stays prose, including a JSON note that is not this kind. A body that claims to be a page and is not valid JSON, or that leaves the kit, is not stored. The Worker sees where it left the kit. The proposal that was already parked stays where it was.

The pieces a page can hold are text, a callout, figures, a fact strip, a table, a status, a checklist, a list of where something shows, a grid, a section, and a set of views. A section holds either more blocks or views, not both. A chart can sit in the document too; a proposal usually does not need one. Tones are `neutral`, `good`, `bad`, and `attention`. There is no colour field.

## What stays on the screen

Sorting a table, selecting rows, opening a checklist step, and switching a view are choices the reader makes while looking. They are not part of the stored document. Reload the Ticket and the stored page is unchanged: the first view is showing again, the table is in its filed order, and nothing is selected.

The playground table offers selection so that craft can be seen. A filed table does not. The count under a table reads from the first column until rows are selected, and then it reads how many are selected, with Clear.

The pieces can be opened on their own at `/_app/page-kit.html`. That page is the worked example, plus the header, figures, table, checklist, surface list, and view switch by themselves. It uses the same host tokens as Ticket and Review.

## Where it shows up

On the Ticket, and on Review, the approval card shows the page under the gate heading. Approve accepts the document. Edit on the page replaces the document as part of approving, the same way a prose edit does. Send it back when the author should change it. After approval, the settled field shows the page.

A proposal that is prose still shows as prose.

## Pointers

The shared check lives in `src/planner/goals/logic/documents.py`. The proposal door that chooses prose or a page is `src/planner/page_kit/logic/validate.py`, called from `src/planner/tickets/logic/resolution.py`. The renderer is `web/src/components/pageKit/`. The card is `web/src/components/WorkerProposalCard.svelte`.

_Last verified: 2026-10-31._
