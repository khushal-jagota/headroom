# Conversation history

The history picker reads prior Ticket and Manager conversations from the existing
`conversation_history_catalog`. Python's database schema already supplies its
membership, timestamp, and source-revision triggers. This area adds no schema,
projection, index, or write during a GET.

`routes.ts` serves `/api/conversation-history/:kind/:owner_id` beneath `/api`.
It validates integer queries before owner-kind selection. `read.ts` uses one
database snapshot, excludes the current association, and orders by the stored
creation time and binary conversation ID. Selected entries seek their page.
Case-folded label prefixes seek after the requested index and wrap once.
Each prefix batch contains at most 60 rows. Entries also retain the 64 KiB
Python-JSON byte budget.

The generated contract in `web/src/lib/catalogs/history.ts` remains authoritative.
Python supports Ticket and Manager owners. The earlier proposal's Chief history
line is corrected to the same unsupported-kind answer as Python.

`format.ts` uses `Intl.DateTimeFormat` with medium dates and short times.
It uses Node's bundled locale and timezone data and requires no system ICU library.
Unknown or malformed locale names fall back to en-US. Unknown and empty time zones
fall back to UTC. Custom GMT offsets that Intl refuses use a shifted UTC instant.
An explicitly empty locale retains the PyICU root pattern through Intl date parts.
The formatter interface retains a no-op `close` method.

The Manager accepts locale-data differences from Python's system ICU. Label
punctuation or spacing can therefore differ while the underlying date, time, and
conversation identity stay the same. The direct proof retains raw labels and
code points and names its narrow accepted comparison rule. It samples en-US and
de-DE over UTC and Berlin instants, plus GMT+05:30, unknown names, and empty names.
These samples do not establish parity for every locale or timezone-data release.

The direct HTTP proof reuses ordinary app adapters and one tiny synthetic database.
It covers populated pages, current exclusion, selection, prefix wrap, timestamps,
source revisions, and the byte budget. It starts no core, listener, or provider.
