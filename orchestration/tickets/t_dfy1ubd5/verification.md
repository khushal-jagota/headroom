The package changes only the product-design Worker. Khushal corrected the installation scope through Chief during Implementation.
Chief's direct `frontend-design` install remains intact. No Anthropic managed row or repository seed is part of this package.

The native Claude and Codex paths both resolve to `/home/vps/.local/share/agent-skills/frontend-design`.
All three readable `SKILL.md` paths have SHA-256 `9c4dff4c20509bfc9a2fd30405276508474dafbd2e865731281d1dab8f2d4a15`.
These are read-only observations. This Worker did not repair or provision actual native homes.

## Skill recommendations

- Keep `frontend-design`. Its interface craft guidance fits the Worker, with house tokens and approved scope in control.
- Leave `canvas-design` out, as Chief decided. Its static art workflow and font bundle do not serve this interface work.
- Leave `theme-factory` out. Its purpose is theme selection for slides and other artifacts. Its supplied palettes and fonts duplicate or conflict with our tokens.
- Leave `web-artifacts-builder` out. It creates complex Claude.ai artifacts through React, Tailwind, and shadcn. Our existing Svelte product and HTML design workflow do not need that separate toolchain.

The last two recommendations concern usefulness, not a limitation of direct installation. No installation was attempted.

## Provisioning check

`provision_native_backend_skills` iterates the managed skill names and replaces collisions for those names.
It leaves other native entries alone. Therefore the direct `frontend-design` install survives while that name remains outside the managed set.
A future managed row with the same name would replace its native link. This package creates no such row.

The isolated proof applied the exact Worker-only manifest through the service to a scratch database.
It provisioned scratch Codex and Claude homes twice each, with user-owned `frontend-design` symlinks and stale Panels links.
Both direct links and their skill bytes stayed identical. Both homes exposed the exact revised Worker source.
The scratch managed home contained no `frontend-design`.
See `provisioning-proof.json`. No actual home was provisioned.

## Checks

- Existing `tests/unit/test_canonical_skill_sources.py`: all 19 cases passed.
- Narrow mypy for `managed_skills.py`, `skill_sources.py`, and `worker_settings/service.py`: no issues in three files.
- Ruff for those same modules: all checks passed.
- The skill validator accepted the revised product-design Worker.
- The exact insertion preserves all original live source bytes outside the addition.
- The manifest contains only the Worker row. Its target equals the repository seed.
- Independent review found no correctness or authority issues. Its request for explicit skill recommendations is addressed above.

No new automated tests were added. Existing coverage and the isolated check prove the relevant publication and preservation behavior.
The live Worker row remains unchanged and awaits Chief's owner-authorized application. The repository merge remains a Consequences action.
