"""Test infrastructure protecting launch-account skill trees.

The final manifests detect added, removed, and changed files, directories, and
symlinks under the frozen launch roots and their discovered link targets. Only
Codex's managed skills/.system subtree is omitted, for both default homes and
CODEX_HOME, including root aliases. Codex rewrites these bundled files at startup.
Panels skills and every other subtree retain the same checks. The interpreter
AuditGuard still refuses audited writes throughout the full protected roots.
"""
