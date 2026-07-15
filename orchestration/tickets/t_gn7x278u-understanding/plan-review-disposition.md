# Plan review disposition — t_gn7x278u

Codex reported two High findings. Both are accepted and resolved before implementation.

## 1. Deterministic proof of a bounded interview

The shipped specialist remains the only owner of question-selection judgment. No interview state machine, question counter, or parallel chat path will be added merely to make model judgment executable.

Deterministic coverage is split honestly:

- repository tests assert the specialist carries the concrete core questions, material-gap-only follow-up rule, explicit sufficiency criteria, and concise Understanding proposal shape;
- runtime/API/browser tests separately prove ordinary Ticket Chat reaches the durable Employee session, paired ownership prevents automatic dispatch, and a real proposal parks for approval;
- no test or implementation claim says deterministic code can prove that a nondeterministic model always exercises judgment correctly.

This preserves the approved registry-first approach and PRINCIPLES.md’s requirement for concrete assertions.

## 2. Meaning of `first_worker_stage`

`needs_understanding` becomes `new_worker`’s actual first working Stage. That is the ticket’s explicit purpose: insert a new first working Stage immediately after Kickoff.

Therefore:

- `WorkerTypeDefinition.first_worker_stage()` continues to mean the second lifecycle Stage and returns `needs_understanding` for `new_worker`;
- external-work creation/reconciliation includes Understanding in the prefix required before later `new_worker` stages;
- sprint-stage thresholds and their exact tests/docs are updated deliberately;
- tickets already at `needs_stages` or later are not rewound, while tickets still at Kickoff enter Understanding when Kickoff is approved.

The later `Stages → Thinking → Drafting → Closeout → Done` sequence and its gate behavior remain unchanged.
