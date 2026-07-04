"""Pure markdown parsers for the seed/migration pipeline: tokenization,
field mapping, and per-file parsers. Stdlib + contract imports only; no I/O,
no clock. The importer owns file reads, timestamps, and SQL."""
