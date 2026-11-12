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

`format.ts` calls the system ICU C date formatter through the existing `koffi`
dependency. It uses medium dates and short times, like PyICU. ICU supplies locale
fallback, custom GMT offsets, unknown-zone behavior, and all label code points.
Each read closes its native formatter, including failed reads. The server's Debian
runtime must install `libicu-dev` and provide its unversioned `libicui18n.so`
link in the multiarch library directory. This is the same development library
that its PyICU build requires. Node's bundled ICU is not used. The native binding
targets this Linux runtime. No other platform is claimed by this proof.
The direct proof compares exact labels and code points in en-US and de-DE, over
UTC and Berlin instants. Focused requests also exercise GMT+05:30, an unknown
locale and time zone, and empty locale and time zone values. This proof samples
these boundaries. It does not claim that two locale samples prove all locale data.

The direct HTTP proof reuses ordinary app adapters and one tiny synthetic database.
It covers populated pages, current exclusion, selection, prefix wrap, timestamps,
source revisions, and the byte budget. It starts no core, listener, or provider.
