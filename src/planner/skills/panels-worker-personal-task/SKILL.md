---
name: "panels-worker-personal-task"
description: "Working a user-owned personal task Ticket."
---

# Personal task Worker

This Worker represents a task the user owns. Keep the Ticket visible and useful without turning it into an automatically managed task.

## Kickoff

Let the user provide only the context they need. Do not turn kickoff into planning or infer work that the user has not stated.

## Outcome

The user defines and owns the desired outcome. Help clarify or express it when asked, but do not substitute an agent-authored plan or decide what completion means for the user.

The user can complete an unset current user-owned field with `panels ticket complete <ticket-id> <field> --value <text>` or `--body-file <path>`. This records the value and advances one Stage. It does not grant permission for the Worker to start the next Stage.

## Closeout

Reach or work this stage only after the user explicitly engages the agent. Follow the user's instructions, record what actually resulted from the outcome, and keep the closeout concise. Do not treat Ticket creation, opening, advancement, or silence as permission to start.

## General discipline

Preserve ordinary Panels proposal/acceptance and visibility semantics. A task that needs no agent help remains user-led; do not add reminders, hidden execution state, or extra lifecycle stages.
