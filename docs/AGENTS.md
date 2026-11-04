# docs/ conventions

Rules for agents who edit `docs/`. `docs/CLAUDE.md` points to this file, so Claude and Cursor read one source.

## What the docs are for

`docs/` is a top-level view of Headroom's systems. A reader with a question starts here. They learn what a system is for, what it is made of, what it can do, and roughly how. Then they go to the code for the details.

A page is detailed enough to sense-check the design, and no more. A reader never needs the code to understand a page. A page is not an explanation of the code. `docs/README.md` is the map of pages.

## What a page holds

Each page covers one real system. It says:

- what the system is for;
- what it is made of;
- how the parts connect to each other and to other systems;
- what it can do, and roughly how;
- the decisions that shape it;
- where the code lives.

[goals.md](goals.md) is the model. Match its depth.

## What a page leaves out

Leave out field lists, request shapes, timings, limits, exact error rules, edge cases, keyboard and pointer behavior, test details, history, step-by-step procedures, and anything the code says better.

Test every sentence: does a reader need it to understand or question the design? If not, delete it.

## When you change a system

Update its page in the same change, at the same depth.

- Trim before you add. A new feature usually changes a sentence. It rarely adds a section.
- Do not write a page for a feature. Put the feature in the page of the system it belongs to.
- Write a new page only for a new system.
- If a page grows past about 1,500 words, cut it back before you finish.
- When a system changes or goes away, correct or delete its page. A page that describes removed machinery is a bug.
- Put history in git. Put plans, evidence, and current judgment on the Ticket.
- Put procedures and runbooks in `ops/`, not here.
- Say a fact once. Link to the page that owns it.

## Craft

- Use plain language and the product's own words. Code names go at the end as pointers. They do not replace the explanation.
- Use an ASCII diagram when shape is the point. Mermaid does not display here.
- Say honestly what works today.
- End with `_Last verified: <date>._` when you checked the page against the code.

`DESIGN.md` holds the visual rules, `CONTEXT.md` the word list, and `PRINCIPLES.md` the engineering rules. Do not copy them into a page.
