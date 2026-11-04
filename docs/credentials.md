# Credentials

A credential is a named API key that Headroom keeps for scripts that call outside services, such as a measure source that reads production usage from PostHog. Credentials exist so an agent can use a key by name without the secret ever passing through a prompt, a chat message, or a file the agent writes.

## What it is made of

- **The store.** Encrypted values in the database, sealed with a separate local key file. A key can be a bearer token, a named header, or basic authentication.
- **The name.** Each credential has a descriptive, reusable name such as `Panels_posthog_production`. The name says what the key is for. It is not owned by a Project, and a matching name can serve any source.
- **The resolver.** The host-only lookup that turns a name into a secret at the moment a script asks for it. There is no environment-variable fallback.
- **Requests.** An agent's ask, inside its own conversation, for a key it needs. The ask appears as an inline card in that conversation.
- **The command.** `panels credential` lists names and metadata, files a request, and lets the owner rename a key. It never carries a secret.

```
 agent ── panels credential request ──► card in its own conversation
                                              │
                         owner enters the key, or rejects
                                              │
                    store ◄── save ───────────┤
                      │                       ▼
                      │          result prompt, ordinary send path ──► agent
                      ▼
 measure source script asks for the name ──► resolver ──► secret, on the host only
```

## How a request works

An agent requests a key by name, provider, and purpose, with optional help for finding it. The server binds the request to the agent's exact current conversation, taken from its identity, so a request cannot be aimed anywhere else and a retired conversation cannot redirect one into its replacement. If a saved key already has that name and kind, the request is satisfied at once and nothing is asked.

Otherwise the owner answers on the card. Saving a new key, replacing an existing one, and rejecting are owner-only. The secret goes through a private owner path, never through a question answer or the conversation. The save, the request's outcome, and the intent to tell the agent commit together.

The agent then learns the outcome through an ordinary prompt that names the request and the credential's name. The card shows that delivery's fate separately from the save. A delivery that may have reached the agent is not resent automatically. The owner can retry only a delivery that provably did not happen, and a retry never repeats the save.

## Decisions that shape it

- A secret never appears in a prompt, a chat row, browser storage, or any public read. Agents see names and metadata only.
- A request lives in the requester's own conversation, because that is the only place an agent can be told the result. See [Conversations](conversations.md).
- Replacement and rename name the revision they started from, so a stale edit fails instead of overwriting newer work.
- The database and its key file belong together. A missing or wrong key is a real failure. Headroom does not reset encrypted records to recover.
- Names hide the secret from scripts' source, not from the running script. Resolution is key indirection, not a sandbox. See [Project measures](measures.md).

## Code

- `src/planner/credentials/`: the store, cipher, resolver, public metadata routes, and the CLI.
- `src/planner/credentials/requests/`: requests, owner answers, and result delivery.
- `web/src/components/conversation/ConversationCredentialRequestCard.svelte`: the card.

_Last verified: 2026-11-04._
