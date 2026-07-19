# Workspace three-level grouping

## Intent

Make the existing Workspace ticket tree immediately scannable by project, worker type, and current
stage. This is a visual and client-side derivation change. The board and worker-type manifest API
contracts do not change.

## Allowed files

- `web/src/routes/BoardRoute.svelte`
- `assets/app.css`
- `tests/e2e/test_board_stage_indicators.py`
- `tests/e2e/test_stage_ownership_frontend.py` (only the Workspace row assertion superseded by this hierarchy)
- `tests/e2e/test_flows_b.py` (only the E31 Workspace reload snapshot superseded by this hierarchy)

## Required behavior

- Render populated groups only, in the hierarchy project → worker type → current stage → ticket.
- Preserve project alphabetical ordering, manifest worker-type ordering, manifest stage ordering, and
  current within-stage activity ordering.
- Make project, worker-type, and stage groups independently collapsible and open by default.
- Put each worker type in a transparent, thin, low-contrast bordered container.
- Use large project titles, uppercase worker-type labels, and normal-case stage headings clearly larger
  than ticket text.
- Hide right-aligned disclosure chevrons until their own header is hovered or keyboard-focused.
- Separate stages with thin rules. Do not add a rule directly under a project title.
- A ticket row contains its title and the existing condition mark only. Preserve the mark's waiting,
  running, approval-needed, paired, error, and completed states. Do not repeat worker type or stage on a
  ticket row and do not add counts or other metadata.
- Preserve ticket selection, Hide done, Chief of Staff, routing, data attributes, and reduced-motion
  behavior.

## Acceptance

- Focused e2e coverage proves hierarchy, ordering, populated-only groups, independent disclosure, and
  running-marker preservation.
- `./verify` passes once the implementation is integrated.
