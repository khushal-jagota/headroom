# Conversations

A conversation is one agent, working in one folder, and the permanent notebook of what was actually delivered to it and what it did. Every Ticket Worker, the Chief, and every Manager has one. The person typing, the automatic Worker step, a manager wake, and a deploy-recovery notice all enter that same conversation. There is no second input path.

The conversation id belongs to its caller. A Ticket stores its own. Chief stores its own. A Manager stores its own. The backend process’s session id is internal. It can be rebound without anything outside the conversation system noticing, and it is never the identity a screen or a command uses.

## Why the notebook exists

Only text that was sent into the agent is context the model can use on a later turn. The notebook is the append-only record of finished facts: a delivered prompt, a completed agent message, a tool call, a permission ask and its answer, a model change, a turn ending, an addressed message to the owner, a refusal, a discard. A row is written once.

Live streaming text is shown while a turn runs and is not stored as notebook prose. Private reasoning is dropped. An open pane receives that live tail directly, so a running turn does not have to announce every token through the global change signal. Rows other screens depend on — delivered and refused prompts, asks, turn endings, model changes, messages to the owner — still announce themselves in the ordinary way.

A message to the owner is a notebook row for Khushal. It does not invoke the backend. Editing a Ticket field, parking a proposal, or updating a recap in the database does not inform the Worker. Those become agent context only when a later prompt carries them.

## One process, three backends

The backends are a closed set: `hermes`, `codex`, and `claude`. One door turns an untrusted name into a member of that set or refuses it. While work is running, the conversation has one child process. After a server restart, nothing is running until the next send. After about half an hour idle, the process stops. The next send starts it again. If the backend cannot restore the session’s memory, the send refuses out loud. Headroom does not put a fresh brain behind an old transcript.

New conversation ends the running turn, discards anything still held, and links a fresh conversation. The old notebook stays on the Ticket as history. Only the active conversation accepts sends. Held prompts live in memory. A restart loses anything not yet delivered. The notebook keeps what was delivered or explicitly discarded.

Each real agent identity has one canonical home and one credential: the user’s Hermes, Codex, and Claude homes. Headroom launches children against those homes. It does not keep a second copy of a login. A symlink to the canonical credential is still one owner. A copied or hardlinked credential file is a second owner, and that split is how a rotating token gets spent out from under the real login. The Codex runtime guard rejects matching credentials in a different home for children Headroom launches. That guard does not cover Hermes or Claude. A whole-home copy that never names the login file can still slip past the source tripwire. The limits are written on `tests/unit/test_no_codex_credential_copies.py`.

Model and reasoning changes ride the next message that is actually delivered. Browsing the picker does nothing until then. Codex and Hermes apply the change in place. Claude restarts the session and carries memory across. The backend is fixed once the conversation exists.

The model picker shows full model names. For Claude and Codex, the highest numbered
version of each variant appears first. Other enabled models sit under one
“Older models (N)” row, which opens in place. It opens automatically when the saved
model is older; the saved choice stays exactly as it was. Hermes shows one flat
list and has no reasoning step. An empty Hermes list is a valid answer.

Choosing a model with no reasoning options finishes the choice immediately.
Otherwise the picker asks for one of that model's reasoning options. There is no
custom model box or “Provider default” reasoning row. An unavailable or empty list
says so plainly, and Refresh tries the read again.

A Ticket's setup row and conversation composer share one model-list query for its
machine and workspace. Refresh updates both. A failed refresh keeps the previous
list and shows the error. Setup and launch-default screens use the same editor,
which remembers each backend's choice while the screen stays open and restores
the previous choice if a save fails.

## How a send tells the truth

Send Message is the door for a person, Chief, a Manager, or a Worker addressing someone. It resolves the recipient’s current conversation, creating the link on first use, and records who sent it.

The fate is about delivery, not about whether the model agreed. A provider that explicitly rejects a prompt produces a visible refusal with its reason. Provider-reported model names and extra provider receipt fields are not stored in the notebook. Delivery acceptance, refusal and uncertainty still are.

Model lists come from the machine that runs the Ticket, including Hermes. The Mac's
last answer stays available when it is offline; [Executor](executor.md) explains
when it is refreshed. Claude's list comes from its CLI startup handshake, with no
prompt or turn. Picking a Claude model saves the resolved, pinned ID, such as
`claude-opus-5-5`, while showing the CLI's display name. Model metadata includes its
family and numeric version, including Fable and Terra. Older models remain in the
list.

A Ticket saves its backend, exact model name and effort. Newer models do not change a saved choice. A Ticket with no saved model starts from its Worker type's defaults. New shipped defaults use `gpt-6.1-sol`, `gpt-6-astra` or `claude-opus-5-5`; existing saved Tickets keep their models.

Panels refuses disabled models when creating a Ticket, saving its configuration or dispatching a message. A disabled model blocks the next automatic Worker step before a claim is taken. It does not send a Manager wake. A dispatched refusal remains a row in the notebook with its reason. Adapters send the exact model and effort directly, and the provider can still refuse them. Disabling a model after dispatch can still allow that turn.

A held message with no explicit change uses the conversation's model and effort when it is sent. An explicit change rides that message. After delivery, Panels remembers the choice for the Ticket's next start. That save follows the prompt's commit, so a crash between them can leave the old choice saved.

| Fate | Meaning |
| --- | --- |
| started | The prompt was written to a live backend before the call returned. |
| queued | Headroom is holding it. It is not on the backend yet. |
| injected | Steer was admitted into the running turn. Admission is not obedience. |
| refused | Delivery did not happen, and the reason says why. |
| uncertain | A steer may have crossed the wire. There is no automatic retry. |
| suppressed | The prompt was dropped on purpose before the wire, usually because a notice had gone stale. |
| recorded | A message to the owner was appended. No backend was called. |

A busy agent is not a refusal. The composer’s mode decides what busy means:

- Steer, the default when a person sends, injects into the running turn when the backend can accept it.
- Queue holds the prompt until the agent is free. Worker steps, manager wakes, and similar automation use queue so they do not steer into an unrelated turn.
- Send now interrupts the running turn and runs this message ahead of held work.

Attachments, a model change, a steer the backend refused, and a Codex slash command that needs its own turn join the queue with a visible reason. When the agent frees, several queued messages may be combined into one backend prompt, with each sender named, unless a message carries a model change. That one starts its own turn. The notebook still records each sender’s content.

Empty content is rejected before a fate exists.

## Permissions, voice, video, and a deploy

Permission asks are always shown and always wait. Headroom does not auto-approve and does not remember a grant. An answer lands only on an ask that is still pending on the live turn. Claude and Codex can also ask structured questions. Hermes stays on the permission flow. Production runs use full tool access. The asks still appear, because the agent decided to ask.

Permission choices and structured question answers follow the same rules. Once an answer is on its way, a second answer cannot take its place. If the backend refuses it while the turn is still live, the request waits for another answer. A choice saved for an offline Mac stays in the notebook even if the turn ends before delivery finishes. Only a separate acknowledgment proves it reached the original callback. See [saved answers](executor.md#a-lost-connection-supplies-no-answer).

Voice input never sends audio into the conversation. The mic captures a clip, Groq transcribes it when `PLAN_GROQ_API_KEY` is set, and the text drops into the draft. That key is separate from any agent subscription.

One video, MP4, MOV, or WebM, up to 250 MiB, can be attached. The upload can finish before Send. Headroom validates it with ffprobe, holds it under a disk budget, and expires an unused upload after a day. The agent receives a link or a path. Headroom does not transcode it and does not promise the model understands it.

A deploy can kill a running session. After the new runtime is admitted, eligible conversations receive one recovery prompt through the ordinary send path. Nothing is replayed. A queued recovery does not count as resumed. Only a started turn whose prompt actually contains the notice does. If its write may have crossed the wire but the acknowledgment is lost, the notebook records one uncertain row with the recovery reference. Recovery becomes unable to resume; the reservation and child remain fenced, and Panels does not retry. See [Deployment](deployment.md).

## Three conversations, one contract

| | Ticket | Chief | Manager |
| --- | --- | --- | --- |
| Who it is | The Worker for one Ticket | The Chief of Staff | The supervisor of one Manager and its children |
| What starts it | The Ticket’s launch setup | Chief’s managed settings | The Manager’s launch defaults, plus a saved goal when one was set |
| What automation sends | The readiness opener, in queue mode | Recovery, and a recap when requested | Goal on create, wakes, recovery |
| What is unique | The worker-step claim is tied to the send fate | Always live for Review’s closing reply | Wakes are batched into this conversation |

Unread dots and working marks are per conversation. A child Ticket’s dot is not the Manager’s dot.

## What comes back out

Gated Ticket text changes only through [proposals](proposals.md). Review and notifications read attention derived from proposals, messages, asks, and failures. A recap request is a real prompt, and the recap field updates when the agent follows it. Artifacts are files. The conversation can discuss them. A file written only on disk does not by itself refresh every screen. Usage rows in the notebook feed cost lines. They are not the provider’s bill. Allowance is a separate read, described in [Usage](usage.md).

A failed turn on a Ticket, when the ceiling holder is an agent, records a wake for that holder. When the holder is Khushal, the failure reaches him through attention and notifications instead of a wake.

## Where it shows up

The conversation dock on a Ticket, Chief, and a Manager is the same pane. Focus shows the human-relevant thread: owner prompts, messages to the owner, asks, answers, failures, stops. Full shows the notebook, including tools and live agent text. The composer carries the model and effort in force, Steer, Queue, Send now, Stop, and New. `f` toggles Focus and Full when focus is not in a text control.

The human’s message is a contained pill. The agent’s message is bare prose. That split is deliberate. See [Design](design.md).

## Pointers

The contract is `src/planner/conversation/contracts.py`. The system and the three adapters live under `src/planner/conversation/`. Send Message is `src/planner/message_delivery/`. The pane is `web/src/components/conversation/`.

Shared turn tokens and Ticket locales are defined in `src/planner/execution_identity.py`.
The backend and Ticket contracts still export those same types. The conversation
package exports its in-memory stand-in directly, without a lazy import.

_Last verified: 2026-10-31._
