# Browser evidence

The settled built frontend was served from the isolated ticket worktree with a worktree-local
database, logs, dispatcher lock, control socket, and Hermes home on free port 8137. A real
Chromium page used a 390×844 viewport.

From Workspace, the browser selected the screenshot Ticket and reached
`#/ticket/t_xkgb9qty`, then selected Chief of Staff and reached `#/chief`. The local service
was stopped after capture.

- [Mobile standalone Ticket page](/files/tickets/t_w5gmk3cq/artifacts/mobile-ticket-page.png)
  — SHA-256 `c50752757588b7cb475bf51afb1bf8da425f4937dcf4fb64f6c16617bc890733`
- [Mobile standalone Chief of Staff page](/files/tickets/t_w5gmk3cq/artifacts/mobile-chief-of-staff-page.png)
  — SHA-256 `5164bc1c22e0ea6b08b4f9a93b7ca0f0942c40b3efde8777802986fda37e6be2`

The first fixture setup mistakenly used the installed live-server CLI rather than the
isolated HTTP API, creating temporary Ticket `t_54kav7m8`. It was identified by exact id
and title and immediately hard-deleted. No pre-existing Ticket or file was removed. The
successful evidence run used only the worktree-local server and state.

