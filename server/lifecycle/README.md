The supervisor owns one replaceable application child and retains its lifecycle lease through actual child, control-server and capability cleanup. Application restart uses the existing control socket. A failed or terminal stop never launches a replacement child. The APP completion callback, sealed process owner and required shutdown deadlines remain application contracts.

Managed Linux stop uses the [accepted intent protocol](../DEVIATIONS.md#managed-linux-supervisor-stop). A private capability authorizes a short-lived intent for the current incarnation. The stop client pins that exact supervisor with a Linux pidfd, checks the authorization reply, sends TERM through the retained handle and waits for exit. A bare INT/TERM without current intent refuses. Foreground launches accept ordinary signals. Linux-specific functions load only on the managed path.

The capability file is `0600` under its owned `0700` directory. Canonical placement excludes application, database parent, managed file roots and snapshot destination, including symlink-resolved roots. Runtime-directory input and capability bytes never enter the APP environment or argv. Cleanup checks the published inode and incarnation before removal. This protects accidental stop boundaries, not hostile same-user activity or SIGKILL. A broad kill can still terminate APP.

Control messages use one bounded JSON line, at most4096bytes. Restart acknowledgement completes before the server waits for client EOF and starts replacement. Shutdown cancels held control clients. Socket successor safety depends on retention of the same lifecycle lease, not protection against arbitrary same-user path replacement.

| Boundary | Seconds | Status |
| --- | ---: | --- |
| APP grace | 30 | Existing APP policy |
| APP hard phase | 45 | Existing shared process force |
| Exact Worker-unit force ceiling | 55 | Existing45plus10-second control attempt |
| Supervisor APP fallback | 60 | First-stop anchored timer |
| Managed stop client | 80 | Derived60plus two10-second exchanges |
| Rendered outer unit phase | 90 | Derived60plus30margin, per systemd phase |

Repeated stop signals and application restart reuse the same first APP stop flight. At60seconds the supervisor logs, attempts SIGKILL only on its still-unreaped APP group and exits1. It never signals a reaped or replacement PID. APP nonzero completion remains a failure after a requested stop. JavaScript timers cannot preempt synchronous work. Independent Worker services are outside the Panels cgroup, so external fallback cannot prove them gone if APP never delivered its own force request.

The fixed foreground/managed/stop entries share one config/control/lease resolver. It loads a copy of the environment, so APP retains the consumed refusal switch. The managed runtime directory stays private. The stop entry anchors its original80-second deadline before asynchronous setup. Existing APP completion, owner seal, and shutdown deadlines remain unchanged.

The new unit renderer consumes only the `panels-typescript` templates. One builtin-only classifier source serves both actions, before any candidate TS/native import. V2 selects the real Node launcher. V1 and one explicitly admitted null-contract SHA select the actual private Python launcher chain. Classification is structural routing, not replacement runtime admission. Both actions unset the four accepted loader variables, then force the same config path and refusal1. Generic environment directives cannot restore the removed values afterward.

No unit is installed or proved here. Systemd's90-second value applies separately to each stop command and the later service-stop phase. The real60-second fallback, new-unit ExecStop/restart, failed control endpoint, pre-activation failure and installer cutover require hosted acceptance. A genuine user manager is required. Mocked-clock or isolated control proofs do not close those service gates.

Default production environment operations, actual installer/capture, and full public CLI binding remain open. An outside-payload scripted APP bootstrap must disclose its exact ExecStart override and hashes. It proves only that composition. Production cutover must settle the installed name and align Python caller admission if rollback retains that name. Authorized hosted rehearsals use no agents/providers or Python patch and disclose the Python caller-refusal gap under the new name. They prove installer/health/stop behavior, not caller-refusal parity. No Python caller guard or existing unit changes here.
