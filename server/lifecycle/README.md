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
| Rendered outer unit phase | 90 | Derived 60 plus 30 margin, per systemd phase |

Repeated stop signals and application restart reuse the same first APP stop flight. At 60 seconds the supervisor logs, attempts SIGKILL only on its still-unreaped APP group and exits 1. It never signals a reaped or replacement PID. APP nonzero completion remains a failure after a requested stop. JavaScript timers cannot preempt synchronous work. Independent Worker services are outside the Panels cgroup, so external fallback cannot prove them gone if APP never delivered its own force request.

The fixed foreground/managed/stop entries share one config/control/lease resolver. It loads a copy of the environment, so APP retains the consumed refusal switch. The managed runtime directory stays private. The stop entry anchors its original 80-second deadline before asynchronous setup. Existing APP completion, owner seal, and shutdown deadlines remain unchanged.

The unit renderers return the new `panels-typescript` templates and environment-file contents through async bundles. Callers must write those files. Managed units require explicit socket and lease paths outside `/tmp` and `/var/tmp`, including resolved aliases. Environment files force the config, refusal and address inputs through the shared encoder. Both actions remove Node loader variables and every exported `LD_*` input before Node starts.

Start rejects a symlinked application root, holds its physical working directory and classifies that checked tree before execution. V2 uses the Node launcher. V1 and the explicitly admitted null-contract SHA use the private Python launcher. Stop uses the running supervisor's capability record and `/proc/$MAINPID/cwd`, so a rollback cannot redirect it to replacement application files. The existing authenticated pidfd client retains signal authority. Classification remains structural routing, not runtime admission.

No unit is installed or proved here. Systemd's 90-second value applies separately to each stop command and the later service-stop phase. The real 60-second fallback, new-unit ExecStop/restart, failed control endpoint, pre-activation failure, capability exclusion and installer cutover require hosted acceptance. A genuine user manager is required. Mocked-clock or isolated control proofs do not close those service gates.

Default production environment operations, actual installer/capture, and full public CLI binding remain open. An outside-payload scripted APP bootstrap must disclose its exact ExecStart override and hashes. It proves only that composition. Production cutover must settle the installed name and align Python caller admission if rollback retains that name. Authorized hosted rehearsals use no agents/providers or Python patch and disclose the Python caller-refusal gap under the new name. They prove installer/health/stop behavior, not caller-refusal parity. No Python caller guard or existing unit changes here.

## Isolated trial settings

`PLAN_WORKER_UNIT_PREFIX=panels-trial-` gives Worker services a separate namespace
and uses `panels-trial-workers.slice`. Install that slice with CPUWeight at most 10.
The process owner rejects cleanup handles from another namespace. The default stays
`panels-worker-` and `panels-workers.slice`.

`PLAN_WORKER_READ_ONLY_PATHS` is a colon-separated list of absolute paths mounted
read-only in each Worker unit. The trial protects `/home/vps/Deployments/Panels`.
The server's own unit needs the same protection for Manager and Chief children.

`PLAN_NOTIFICATIONS_ENABLED=true` keeps notification delivery active when
`PLAN_DISPATCH_ENABLED=false` disables automatic work. Notification delivery still
requires production mode and the dispatcher lock. Without an explicit setting,
notifications follow dispatch as before. These settings do not quiesce copied
recovery records. Clear those in the copied database before the first trial start.
