# Conversation history

The history picker reads prior Ticket and Manager conversations from the existing
`conversation_history_catalog`. The baseline database schema already supplies its
membership, timestamp, and source-revision triggers. This area adds no schema,
projection, index, or write during a GET.

`routes.ts` serves `/api/conversation-history/:kind/:owner_id` beneath `/api`.
It validates integer queries before owner-kind selection. `read.ts` uses one
database snapshot, excludes the current association, and orders by the stored
creation time and binary conversation ID. Selected entries seek their page.
Case-folded label prefixes seek after the requested index and wrap once.
Each prefix batch contains at most 60 rows. Entries also keep a 64 KiB
JSON byte budget.

The contract in `web/src/lib/catalogs/history.ts` is authoritative.
Only Ticket and Manager owners have history. A Chief request gets the unsupported-kind answer.

`format.ts` uses `Intl.DateTimeFormat` with medium dates and short times.
It uses Node's bundled locale and timezone data and requires no system ICU library.
Unknown or malformed locale names fall back to en-US. Unknown and empty time zones
fall back to UTC. Custom GMT offsets that Intl refuses use a shifted UTC instant.
An explicitly empty locale retains the PyICU root pattern through Intl date parts.
The formatter interface retains a no-op `close` method.

Node's locale data can differ from a system ICU library. Label punctuation or
spacing can therefore differ while the underlying date, time, and conversation
identity stay the same.
