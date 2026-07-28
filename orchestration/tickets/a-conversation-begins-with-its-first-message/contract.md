# A conversation begins with its first message

## Why this ticket exists

A new claude conversation cannot send its first message. It refuses with "the backend's
session would not load", and pressing New and trying again does exactly the same thing.
Two conversations on the VPS are stuck like this right now.

The cause is that a conversation is made before there is anything to say. Typing into an
empty panel and pressing Enter is two calls today:

1. `POST /api/tickets/{id}/conversation` creates the conversation from the Ticket's stored
   launch defaults — not from what the composer is showing. That is why both stuck rows
   have no model recorded at all.
2. `POST /api/conversations/{id}/send` then sends the message, and because the composer's
   pick differs from what step 1 just created, the message carries a **model change**.

Claude takes its model and reasoning effort when the child process starts, so a change
cannot be made to a child that is up. The adapter asks for a rebind: the core stops the
child and starts another. That second child now finds a session cursor — the first child
minted one and it was written down the moment it came up — and asks claude to resume it.
Claude has written nothing under that id, refuses to start at all, and the message never
lands.

So the conversation is born before the message, on values nobody chose, and the first
thing the message has to do is correct it.

## The rule

**Nothing exists until a message is sent.** New leaves a composer and its settings and no
conversation anywhere. Sending is what brings a conversation into being.

- A message sent with no conversation id creates a conversation, on exactly the values the
  composer is showing, and is delivered into it.
- A message sent with a conversation id is delivered into that conversation.
- If the message does not land, the conversation it would have created goes with it.
  Nothing half-made survives.

The id travels with the message rather than being looked up from the Ticket. The readiness
loop can start a conversation for a Ticket while somebody is reading the panel, and a
message must land in the conversation the person is looking at rather than whichever one
the Ticket points at by the time the send arrives.

## What this fixes, and why it is not a patch over the old failure

A conversation created by its first message is created on the values that message carries,
so that message has nothing to change. No change means no rebind, no second child, and
nothing asking to resume a session that was never written. The failure is absent rather
than handled.

## Done when

A new claude conversation delivers its first message with a model and a reasoning effort
chosen in the composer; the two conversations stuck on the VPS send again; pressing New
leaves nothing behind; and a message that is refused leaves no conversation behind either.
