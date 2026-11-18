# Hermes ACP backend

`create_hermes_backend_child_factory(launch)` supplies the Hermes backend child through the fixed conversation contract. It launches the retained Python extension, `src/planner/conversation/backends/hermes_acp_extension.py`, over the shared ACP transport in `server/drivers/acp/` (its README covers the connection). The catalog owner supplies the inventory command source.

`hermes_acp_child_launch` names that extension path. `child_environment` adds the full-access switch to the shared ACP environment: its six inherited variables, explicit launch overrides, and the final conversation identity. Hermes does not use the tool-shell helper and does not add `BASH_ENV`.

`HermesModelCatalogOwner.source` supplies the unchanged catalog interface. `hermes_model_catalog_launch` runs the retained Python inventory script, `src/planner/conversation/backends/hermes_model_catalog.py`. The owner copies the complete parent environment, applies its overrides, and returns raw command outcomes. The lifecycle owner must retain the object and call `shutdown()`, because the source interface stores only its callable. Cancellation stops current probes and permits a later read. Shutdown seals new reads before cleanup.

Focused catalog tests cover cancellation, shutdown during spawn, and retry after failed cleanup. The replay suite runs the real Hermes adapter against recorded ACP traffic. No real Hermes code runs in these tests.
