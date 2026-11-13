The supervisor owns one replaceable application child and retains its lifecycle lease through actual child, control-server and capability cleanup. Application restart uses the existing control socket. A failed or terminal stop never launches a replacement child. The APP completion callback, sealed process owner and required shutdown deadlines remain application contracts.

Managed Linux stop uses the [accepted intent protocol](../DEVIATIONS.md#managed-linux-supervisor-stop). A private capability authorizes a short-lived intent for the current incarnation. The stop client pins that exact supervisor with a Linux pidfd, checks the authorization reply, sends TERM through the retained handle and waits for exit. A bare INT/TERM without current intent refuses. Foreground launches accept ordinary signals. Linux-specific functions load only on the managed path.

The capability file is `0600` under its owned `0700` directory. Canonical placement excludes application, database parent, managed file roots and snapshot destination, including symlink-resolved roots. Runtime-directory input and capability bytes never enter the APP environment or argv. Cleanup checks the published inode and incarnation before removal. This protects accidental stop boundaries, not hostile same-user activity or SIGKILL. A broad kill can still terminate APP.

Control messages use one bounded JSON line, at most 4096 bytes. Restart acknowledgement completes before the server waits for client EOF and starts replacement. Shutdown cancels held control clients. Socket successor safety depends on retention of the same lifecycle lease, not protection against arbitrary same-user path replacement.

The configured control socket is `0600` inside its owned real `0700` parent. Restart remains a same-user, unauthenticated operation.

| Boundary | Seconds | Status |
| --- | ---: | --- |
| APP grace | 30 | Existing APP policy |
| APP hard phase | 45 | Existing shared process force |
| Exact Worker-unit force ceiling | 55 | Existing 45 plus 10-second control attempt |
| Supervisor APP fallback | 60 | First-stop anchored timer |
| Managed stop client | 80 | Derived 60 plus two 10-second exchanges |
| Planned outer unit phase | 90 | Derived 60 plus 30 margin, per systemd phase |

Repeated stop signals and application restart reuse the same first APP stop flight. At 60 seconds the supervisor logs, attempts SIGKILL only on its still-unreaped APP group and exits 1. It never signals a reaped or replacement PID. APP nonzero completion remains a failure after a requested stop. JavaScript timers cannot preempt synchronous work. Independent Worker services are outside the Panels cgroup, so external fallback cannot prove them gone if APP never delivered its own force request.

These modules supply operations only. Fixed runtime entries, actual new unit rendering, installer stop, packaging and production environment composition remain separate work. No unit is installed or proved here. The real 60-second fallback, new-unit ExecStop/restart, failed control endpoint, pre-activation failure, capability exclusion and installer cutover require the hosted acceptance sequence. Mocked-clock or isolated control proofs do not close those service gates.
