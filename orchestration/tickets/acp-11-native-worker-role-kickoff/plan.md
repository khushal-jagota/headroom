# ACP-11 implementation plan

## Scope

Implement the owner-approved native skill exposure and one-shot ACP role kickoff without changing
the ACP protocol, browser state, backend adapters, or Hermes checkout.

## Public-seam slices

1. Add a repository-level test that treats `.agents/skills` and `.claude/skills` as the native
   project interfaces. Create each as one relative symlink to the canonical `skills/` directory and
   prove that every canonical child containing `SKILL.md` is exposed through both links.
2. Add a shared `AcpEmployeeChildFactory` decorator. A successful `session/new` arms its returned
   session id; the first matching `session/prompt` atomically consumes the arm and prepends the exact
   Ticket or Chief directive as a separate text content block. A failed replacement `session/new`
   leaves the prior arm intact. A text-only slash-command prompt passes through without consuming the arm.
   Session load, capture-load, fork, and later ordinary prompts do not arm or decorate anything. A
   new child that only loads an existing session therefore cannot repeat the kickoff.
3. Compose that decorator around every materialized backend factory before the registry receives the
   factories. This keeps browser echoes and typed replay unchanged because the hub and broker retain
   the caller's original `PromptRequest`; only the child delivery copy changes. During the decorated
   prompt and each session replay only, a session-scoped one-shot normalizer drops a separate synthetic
   role chunk or removes the role prefix from Hermes' flattened echo. It disarms in `finally`, and a
   genuine message equal to the directive remains visible.
4. Remove Claude's Panels-specific `systemPrompt` request decoration while retaining its ingress
   normalization wrapper. Update Claude unit coverage to prove caller metadata is passed through for
   new/load/capture-load and compaction controls are still normalized.
5. Extend official-SDK e2e coverage across both prompt origins: a Ticket whose first prompt is
   Automatic Employee work and the Chief whose first prompt is typed in the browser. Prove the ACP
   audit contains the exact one-shot directive while live and replayed human echoes contain only the
   caller's text; prove later prompts and a loaded/replacement child are not decorated again.
6. Document native role skills and the one-shot delivery boundary in `docs/chat.md`, then run only the
   contract's focused Ruff and test gates. The orchestrator owns the final repository-wide `./verify`.

## Focused gates

- Ruff on the changed Python source and tests.
- `tests/unit/test_project_backend_skill_links.py`
- `tests/unit/test_role_skill_kickoff.py`
- `tests/unit/test_claude_acp_backend.py`
- The role-kickoff selections in `tests/e2e/test_acp_conversation.py`
