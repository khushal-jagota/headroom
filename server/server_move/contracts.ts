/**
 * One move of the server to another machine. The old server leads the run. The machine code
 * on the target machine copies the state, installs the new server around it, checks it, and
 * switches. Nothing here belongs to one operating system.
 */
import type { ChecksumManifest } from '../environments/server_state/contracts.ts';

export type ServerMovePlatform = 'darwin' | 'linux';

/**
 * preparing: the old server gets the server package for the target's platform.
 * copying: the target copies the state files while the old server runs.
 * paused: writes are held. The target copies the database copy and the changed files,
 *   installs the new server, and checks it.
 * committing: the point of no return. The old server never lifts the pause again.
 * moved: the new server took the next move number. The old server answers with its address.
 * failed: the run ended before the point of no return. The old server continues.
 */
export type ServerMovePhase =
  'preparing' | 'copying' | 'paused' | 'committing' | 'moved' | 'failed';
export const SERVER_MOVE_ACTIVE_PHASES: ReadonlySet<ServerMovePhase> = new Set([
  'preparing',
  'copying',
  'paused',
  'committing',
]);

/** The owner's request: `POST /api/machines/:machine_id/move`. */
export interface ServerMoveRequest {
  /** The HTTPS origin of the new server. The owner supplies its route and certificate. */
  readonly server_url: string;
  /** The loopback port of the new server on the target machine. */
  readonly port: number;
}

/** What the owner sees: `GET /api/server-move` returns `{ move: ServerMoveView | null }`. */
export interface ServerMoveView {
  readonly move_id: string;
  readonly target_machine_id: string;
  readonly server_url: string;
  readonly port: number;
  readonly phase: ServerMovePhase;
  /** A plain sentence for the owner when phase is failed, otherwise null. */
  readonly failure: string | null;
  readonly started_at_ms: number;
  readonly updated_at_ms: number;
}

/** The sealed server package for the target's platform, at the running revision. */
export interface ServerMovePackage {
  readonly platform: ServerMovePlatform;
  readonly revision: string;
  readonly archive_sha256: string;
  readonly archive_size: number;
}

/** Trusted ingress settings that the new server's settings repeat. */
export interface ServerMoveIngress {
  readonly provider: string;
  readonly allowed_login: string;
}

/**
 * The run record on the old server: `SERVER_MOVE_ATTEMPT_FILE` in the server folder. It is
 * outside the database and outside the state inventory, so a restart of the old server reads it.
 */
export interface ServerMoveAttempt extends ServerMoveView {
  readonly platform: ServerMovePlatform;
  readonly revision: string;
  readonly server_id: string;
  /** The move number before the switch. The new server takes this number plus one. */
  readonly move_number: number;
  readonly package: ServerMovePackage | null;
  readonly trusted_ingress: ServerMoveIngress | null;
  readonly paused_at_ms: number | null;
}

/** `GET /api/executor/server-move` for the target machine of the active run, otherwise 404. */
export interface ServerMoveInstruction {
  readonly move_id: string;
  readonly phase: ServerMovePhase;
  readonly server_url: string;
  readonly port: number;
  readonly server_id: string;
  readonly move_number: number;
  /** Null while the old server prepares the package. */
  readonly package: ServerMovePackage | null;
  readonly trusted_ingress: ServerMoveIngress | null;
}

/**
 * `GET /api/executor/server-move/:move_id/files`. Before the pause, the state files of the
 * running server without the database. From the pause on, the complete checksum list: the
 * database copy under the database's relative name, and every state file.
 */
export interface ServerMoveFiles {
  readonly complete: boolean;
  readonly manifest: ChecksumManifest;
}

/** `POST /api/executor/server-move/:move_id/report` from the target machine. */
export type ServerMoveReport =
  { readonly state: 'checked' } | { readonly state: 'failed'; readonly reason: string };
/** The old server's answer. `commit: true` only after it wrote the committing phase. */
export interface ServerMoveReportReply {
  readonly commit: boolean;
}

/**
 * The marker that makes a new server start held. The installer's move mode writes it in the
 * new server folder. A held server answers its health check, the server link and the local
 * executor link, and does no other work until the commit.
 */
export interface ServerMoveHeldMarker {
  readonly move_id: string;
  readonly target_machine_id: string;
  readonly server_url: string;
  readonly server_id: string;
  /** The move number of the copied record. The commit sets this number plus one. */
  readonly move_number: number;
}

/** `POST /api/executor/server-move/held/probe` on the new server: a test job with no agent. */
export interface ServerMoveProbeJob {
  readonly job_id: string;
  readonly nonce: string;
}
/** `POST /api/executor/server-move/held/probe/:job_id` with `{ nonce }` answers the job. */
export interface ServerMoveProbeAnswer {
  readonly answered: true;
}
/** `POST /api/executor/server-move/held/commit` with `{ move_id }`. */
export interface ServerMoveCommitReply {
  readonly server_id: string;
  readonly move_number: number;
}

/**
 * The installer's move mode: `node <payload>/server/installation/move_main.ts` with one JSON
 * object on stdin. `install` prints `ServerMoveInstallResult` as JSON on stdout.
 */
export interface ServerMoveInstallRequest {
  readonly command: 'install';
  /** The new installation root. It must not exist. */
  readonly root: string;
  readonly service: string;
  readonly port: number;
  readonly archive: string;
  readonly archive_sha256: string;
  readonly revision: string;
  /** The copied and checked state folder. The installer moves it to `<root>/data`. */
  readonly data_directory: string;
  readonly trusted_ingress: ServerMoveIngress | null;
  readonly held: ServerMoveHeldMarker;
}
export interface ServerMoveInstallResult {
  readonly root: string;
  readonly data_directory: string;
  /** The new server's local executor link: `<root>/data/executor-link/server.sock`. */
  readonly executor_link: string;
}
/** Remove a new server that did not take over: its services and its root. */
export interface ServerMoveRemoveRequest {
  readonly command: 'remove';
  readonly root: string;
  readonly service: string;
}

export const SERVER_MOVE_ATTEMPT_FILE = 'move-attempt.json';
export const SERVER_MOVE_HELD_FILE = 'move-held.json';
/** Old-server work folder for the package cache and the database copy. Not state. */
export const SERVER_MOVE_DIRECTORY = 'server-move';
/**
 * Machine code holds agent commands and output through a server pause of up to five minutes
 * (`CLI_RESULT_TIMEOUT_SECONDS`). The pause of a move ends within that time.
 */
export const SERVER_MOVE_PAUSE_LIMIT_SECONDS = 300;
export const SERVER_MOVE_DEFAULT_SERVICE = 'com.panels.home';
