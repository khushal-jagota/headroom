# Implementation plan — hermes-relay-s1-foundation

Binding source: `orchestration/tickets/hermes-relay-s1-foundation/contract.md`.
Grounding: `orchestration/hermes-relay-redesign/plan.md`, `s0-spike.md`.
Every item below traces to a contract requirement; anything the contract did not ask for is
either absent or listed under "Decisions the contract left open" with a one-line justification.

This plan is RED-first and TDD-ordered: the test files are written and seen failing before the
module they drive is written. Section 9 is the exact build order the implementer follows.

---

## 0. Concurrency model (the one design fact everything else follows from)

Two distinct concurrency domains, matching the two distinct transports:

- **Backend supervisor — thread-based**, a direct mirror of `planner.minds.gateway.GatewayChild`.
  It owns a `hermes serve` subprocess whose stdout must be read line-by-line for the readiness
  line `HERMES_BACKEND_READY port=<n>`. That is exactly the `ChildProcess` / `SpawnFn` /
  reader-thread shape already proven in `gateway.py`, so the supervisor reuses that shape (its own
  copies of the tiny `ChildProcess` protocol surface it needs — `read_stdout`, `wait`, `kill`,
  `close_stdin` — plus a `spawn_hermes_serve_popen` real spawner). It does NOT import from
  `minds/` (contract forbids touching `minds/`, and importing its private `_stdout_loop` shape
  would couple us to gateway internals); it re-expresses the same pattern locally.

- **Relay + upstream WS client — async**, on the FastAPI event loop, mirroring
  `planner.core.ws.tail_events` (async `@app.websocket` route, `websockets.asyncio.client` for the
  single upstream connection). One event loop; the "lock" discipline below is `asyncio.Lock`,
  the async analogue of the gateway's `threading.Lock` guarding `_pending`.

Rationale: the supervisor's job is process lifecycle over stdout (threads, like the gateway); the
relay's job is WS fan-out on the server's event loop (async, like `tail_events`). Forcing either
into the other's model is the wrong structure.

---

## 1. Module layout — `src/planner/hermes_backend/`

New package. Files, and for each: the classes/functions with descriptive names, one-line purpose,
and key signatures. `__init__.py` re-exports the public surface (mirrors `minds/__init__.py`).

### 1.1 `src/planner/hermes_backend/__init__.py`
- Package docstring + re-exports of the public names used by `core/server.py`:
  `HermesBackendSupervisor`, `spawn_hermes_serve_popen`, `HermesBackendUnavailable`,
  `HermesRelay`, `relay_downstream_ws`, `compose_hermes_backend`. Nothing else.

### 1.2 `src/planner/hermes_backend/supervisor.py`
Owns exactly one `hermes serve` child. Thread-based, mirrors `GatewayChild`.

- `HermesBackendUnavailable(Exception)` — the backend did not become ready (spawn failure, exit
  before ready, or readiness timeout). Reported, never raised out of Panels startup (server
  integration catches it; see §7). One-line purpose: transport-level unavailability signal.

- `class HermesServeProcess(Protocol)` — the injection seam surface the supervisor needs from a
  spawned child. Exactly the subset used:
  ```
  def read_stdout(self) -> str | None: ...
  def close_stdin(self) -> None: ...
  def kill(self) -> None: ...
  def wait(self, timeout: float | None = None) -> int | None: ...
  ```
  (No `send`/`read_stderr`: the supervisor never writes to the child and only needs stdout for the
  readiness line. Everything-earns-its-existence: no `send` seam because nothing sends.)

- `SpawnHermesServeFn = Callable[[list[str], dict[str, str]], HermesServeProcess]` — the injectable
  spawn seam, same shape as `minds.gateway.SpawnFn`. Tests pass a fake; production passes
  `spawn_hermes_serve_popen`.

- `class PopenHermesServeProcess` — real `HermesServeProcess` over `subprocess.Popen[str]`
  (text, line-buffered, `stdin=PIPE, stdout=PIPE, stderr=PIPE`). Direct analogue of
  `minds.gateway.PopenChild`, trimmed to the four methods above (stderr is drained by a daemon
  thread to `deque(maxlen=...)` for a diagnostic tail, exactly as gateway does).

- `def spawn_hermes_serve_popen(argv: list[str], env: dict[str, str]) -> HermesServeProcess` —
  the real spawner; wraps `subprocess.Popen`. Analogue of `spawn_popen`.

- `def build_hermes_serve_argv(hermes_bin: str, port: int) -> list[str]` — returns
  `[hermes_bin, "serve", "--port", str(port), "--skip-build"]`. Isolated so the exact args are one
  testable place (contract: `--port <configured> --skip-build`).

- `def build_hermes_serve_env(base_env: Mapping[str, str], hermes_home: str, session_token: str)
   -> dict[str, str]` — returns `dict(base_env)` with `HERMES_HOME=<hermes_home>` and
  `HERMES_DASHBOARD_SESSION_TOKEN=<session_token>` set. Isolated for the same reason.

- `def generate_dashboard_session_token() -> str` — per-boot token, `secrets.token_urlsafe(32)`.
  One line; isolated so the supervisor takes it as an argument and tests inject a fixed token to
  assert env exactness.

- `READINESS_LINE_PREFIX: Final = "HERMES_BACKEND_READY port="` — the exact stdout marker.
- `READINESS_TIMEOUT_DEFAULT: Final = 30.0` — see §2 config; the constant is the fallback default.
- `CHILD_CLEANUP_BUDGET_SECONDS: Final = 2.0` — the small bounded budget for terminating a child
  during a readiness-failure cleanup (see `wait_ready` below). Fixed policy, not config: it is an
  internal safety bound on an error path, not an owner knob.

- `class HermesBackendSupervisor` — the supervisor. Signature and behavior:
  ```
  def __init__(
      self,
      *,
      hermes_bin: str,
      hermes_home: str,
      port: int,
      session_token: str,
      readiness_timeout_seconds: float,
      base_env: Mapping[str, str],
      spawn: SpawnHermesServeFn = spawn_hermes_serve_popen,
  ) -> None: ...
  ```
  - `__init__` builds argv+env (via the helpers), calls `spawn(argv, env)` inside a `try/except
    OSError` that re-raises as `HermesBackendUnavailable`, stores the child, and starts a single
    daemon reader thread `hermes-serve-stdout` that scans stdout lines for `READINESS_LINE_PREFIX`.
    On match it parses the trailing int as `self._ready_port`, sets a `threading.Event`
    `_ready_gate`, and keeps draining stdout (so the pipe never blocks the child). On stdout EOF it
    sets a `_dead` Event and sets `_ready_gate` (death wakes waiters, gateway pattern lines
    289-298). It does NOT retain argv/env on any public attribute — the injected fake spawn is the
    single place that captures argv+env for the exactness test (finding #11: the generated
    dashboard token and full process env are never parked on a public object).
  - `def wait_ready(self, timeout: float | None = None) -> int` — waits on `_ready_gate` up to
    `timeout or self._readiness_timeout_seconds`; returns the parsed ready port on success.
    Mirrors `GatewayChild.wait_ready` (gateway lines 316-324), but returns the port because the
    contract's readiness fact is `port=<n>`. **On any post-spawn readiness failure — the gate was
    set by death (child exited before ready) OR the gate never fired within the timeout — the child
    may still be alive; before raising, `wait_ready` terminates its own child within a bounded
    cleanup so no live child is ever left behind when the caller drops the supervisor reference
    (finding #2, contract line 42 "terminate only its own child").** Exact ordering:
    1. Detect failure (timeout elapsed, or gate set with no `_ready_port` parsed → death).
    2. Call the internal `self._terminate_child(deadline=monotonic() + CHILD_CLEANUP_BUDGET_SECONDS)`:
       `close_stdin()` → `wait(remaining())`; if still alive `kill()` → `wait(remaining())`; then
       bounded `join` of the stdout thread. Same close→wait→kill→wait shape as `shutdown` (below),
       reused. This is bounded by `CHILD_CLEANUP_BUDGET_SECONDS`.
    3. Then `raise HermesBackendUnavailable(...)`. The caller still reports unavailable (§7), but
       the child is already gone — the dropped reference orphans nothing.
    `shutdown` and this cleanup share one private `_terminate_child(deadline=...)` helper.
  - `def shutdown(self, *, deadline: float | None = None) -> None` — mirrors
    `GatewayChild.shutdown(*, deadline=)` exactly (lines 392-408) via `_terminate_child(deadline)`:
    `close_stdin()`, then `wait(remaining())`; if still alive, `kill()` + `wait(remaining())`; then
    bounded `join` of the stdout thread. `remaining()` = `max(0.0, deadline - monotonic())` when
    `deadline` is given (falls back to `CHILD_CLEANUP_BUDGET_SECONDS` when `deadline is None`).
    Terminates only its own child. Idempotent — safe if the child was already cleaned up by a
    readiness-failure path. No second deadline model (contract "shutdown model").
  - `@property alive -> bool` — `not self._dead.is_set()`.
  - `@property ready_port -> int | None` — the parsed readiness port, or None if not yet ready.

- `def compose_hermes_backend(*, config: Config, base_env: Mapping[str, str],
   spawn: SpawnHermesServeFn = spawn_hermes_serve_popen,
   session_token: str | None = None) -> HermesBackendComposition | None` — **the composition seam
  (finding #9).** Minimal helper that `core/server.py`'s lifespan calls (see §7). It is the single
  place the enable-flag gate lives:
  - If `not config.hermes_backend_enabled`: return `None` and **never touch `spawn`** — this is the
    operative gate the config-off test drives with `test_mode=False`.
  - Else: generate (or take injected) `session_token`, construct a `HermesBackendSupervisor(...,
    spawn=spawn)` (so exactly one `spawn` call happens), `wait_ready()`, and on success build the
    `HermesRelay` + its `run()` task wiring inputs, returning a small
    `HermesBackendComposition` record (`supervisor`, `relay`, `relay_run_coro_factory` or the
    already-built relay + upstream url). On `HermesBackendUnavailable` it re-raises for the lifespan
    to catch-and-log (the supervisor has already terminated its child per `wait_ready` above).
  Kept minimal: it exists only to make the enable-flag gate injectable and testable at
  `test_mode=False` with a fake `spawn`, which is otherwise impossible without standing up the whole
  app. It lives in `hermes_backend/` (in the allowlist), not a new top-level module and not in
  `minds/chat/runtime`.

- `@dataclass class HermesBackendComposition` — the record `compose_hermes_backend` returns: the
  live `supervisor`, the constructed `relay`, and the `upstream_ws_url` (the lifespan creates the
  `run()` task and stores `app.state.hermes_relay`). Small; exists so the seam has a typed return.

### 1.3 `src/planner/hermes_backend/upstream_client.py`
The single owner of the one upstream WebSocket connection. Async.

- `UPSTREAM_WS_PATH: Final = "/api/ws"` — the endpoint path (query `?token=` appended).
- Reconnect/backoff constants (see §6): `RECONNECT_INITIAL_DELAY_SECONDS`,
  `RECONNECT_BACKOFF_MULTIPLIER`, `RECONNECT_MAX_DELAY_SECONDS`. (No max-attempts constant —
  reconnect is bounded by backoff, not by attempt count; finding #3.)

- `def build_upstream_ws_url(port: int, session_token: str) -> str` — returns
  `f"ws://127.0.0.1:{port}/api/ws?token={session_token}"`. `127.0.0.1` is the fixed loopback bind
  (matches `config.HOST`); isolated so the exact URL is one testable place. The token is URL-safe
  (`token_urlsafe`) so no quoting is needed; the builder does not re-encode.

- `class UpstreamConnection(Protocol)` — the minimal async surface the relay needs from a live
  upstream socket, so tests inject a fake without a real server for the pure-relay unit tests, and
  the reconnect tests use a real in-process `websockets` server:
  ```
  async def send(self, frame_line: str) -> None: ...
  async def recv(self) -> str: ...            # one newline-delimited JSON frame line
  async def close(self) -> None: ...
  ```
  A thin `WebsocketsUpstreamConnection` adapter wraps a `websockets.asyncio.client` connection to
  this protocol (its `recv()` returns one text frame; the upstream sends newline-delimited JSON but
  each WS text message is already one frame per the S0 wire, so `recv` yields one JSON string).

- `ConnectUpstreamFn = Callable[[str], Awaitable[UpstreamConnection]]` — the async connect seam
  (arg = the ws url). Production = `connect_websockets_upstream`, a function that calls
  `websockets.asyncio.client.connect(url)` and wraps it; tests pass a fake connect that returns a
  scripted `UpstreamConnection` (or raises, to exercise the connect-FAILURE backoff path).

The reconnect loop itself lives in the relay (§1.4) because reconnect must synthesize the
upstream-reset downstream frame, which is relay routing logic — the client module only owns
url-building, the protocol, the constants, and the real `websockets` adapter.

### 1.4 `src/planner/hermes_backend/relay.py`
The relay core: id-namespacing + session fan-out + tee + reconnect. Async, single upstream owner.

Value/record types (small, frozen dataclasses):

- `@dataclass(frozen=True) class DownstreamRequestKey` — identifies one in-flight request's origin:
  ```
  downstream_id: int          # per-connection monotonic id assigned by the relay
  downstream_request_id: object  # the id the downstream put on its JSON-RPC request (int|str)
  ```
  Purpose: the key stored under an allocated upstream id so a response is restored to its origin.

- `@dataclass class DownstreamRegistration` — one connected downstream's routing state:
  ```
  downstream_id: int
  send: Callable[[str], Awaitable[None]]   # writes one frame line to this downstream ws
  subscribed_session_ids: set[str]         # sessions this downstream declared interest in
  ```
  Purpose: the relay's per-downstream record; the fan-out and id tables key off `downstream_id`.

- `@dataclass(frozen=True) class RelaySubscribeMessage` — the parsed relay-control subscribe (see
  §3):
  ```
  session_id: str
  ```
  Purpose: the typed result of recognizing a subscribe control frame.

- `class RelayFrameObserver(Protocol)` — the tee-observer protocol (named exactly this):
  ```
  def observe_upstream_to_downstream(self, frame: JsonDict) -> None: ...
  def observe_downstream_to_upstream(self, downstream_id: int, frame: JsonDict) -> None: ...
  ```
  Purpose: registered observers see every frame in both directions. **S1 ships this seam with TEST
  observers only — there is no product consumer.** (Contract: tee seam, test observers only.)

- `class HermesRelay` — the relay. Owns: the single upstream connection lifecycle, the explicit
  upstream-availability state, the id tables, the downstream registry, the observer list. Key
  members and methods:
  ```
  # upstream-availability state (finding #4)
  self._upstream: UpstreamConnection | None = None   # the live socket, or None while absent/closed
  self._upstream_send_lock: asyncio.Lock             # guards every upstream write (finding #6)
  self._id_lock: asyncio.Lock                        # guards id maps + allocator + registry snapshot

  def __init__(
      self,
      *,
      connect_upstream: ConnectUpstreamFn,
      upstream_ws_url: str,
      observers: Sequence[RelayFrameObserver] = (),
  ) -> None: ...

  async def run(self) -> None:
      # The upstream owner loop (§6): connect → set _upstream → read frames → route to downstreams;
      # on connect-FAILURE (connect_upstream raises) OR established-socket-drop (recv raises
      # ConnectionClosed): clear _upstream, backoff, and retry — forever, until shutdown cancels
      # this task. On each successful (re)connect after the first: reset id tables + synthesize the
      # upstream-reset frame to every downstream (§5). Runs as one asyncio.Task started by the
      # lifespan (§7).

  async def shutdown(self, *, deadline: float) -> None:
      # deadline-aware, bounded by the shared deadline (finding #1). Order:
      #   1. close the upstream socket (self._upstream.close()) under _upstream_send_lock,
      #      suppressing errors — this unblocks a parked recv();
      #   2. cancel the run task;
      #   3. await it under asyncio.wait_for(..., timeout=max(0.0, deadline - monotonic())),
      #      suppressing TimeoutError and CancelledError.
      # Never blocks past the shared deadline. The lifespan calls this, then the supervisor
      # shutdown, with the SAME single deadline (§7).

  async def register_downstream(self, send: Callable[[str], Awaitable[None]]) -> DownstreamRegistration:
      # allocate downstream_id, insert into registry, return the registration.

  async def unregister_downstream(self, registration: DownstreamRegistration) -> None:
      # remove from registry AND cancel only this downstream's pending id mappings, O(k) in that
      # downstream's own pending set (§4, finding #7).

  async def handle_downstream_frame(self, registration: DownstreamRegistration, raw_line: str) -> None:
      # parse; if it is a RelaySubscribe control frame (§3) → record session interest, no forward.
      # else it is native JSON-RPC to forward upstream (finding #5):
      #   - if upstream is absent/closed → reply to THIS downstream with a relay-generated error
      #     frame carrying the original id (§4); no mapping allocated.
      #   - if it carries a correlatable id → namespace + map + rewrite + send upstream (§4),
      #     rolling back the mapping if the send raises.
      #   - if it has NO id (a notification) → forward VERBATIM upstream, no id rewrite, no mapping.

  def add_observer(self, observer: RelayFrameObserver) -> None:  # test seam only
  ```

### 1.5 `src/planner/hermes_backend/downstream_route.py`
The downstream FastAPI WS route body. Async, mirrors `core/ws.py::tail_events` shape.

- `async def relay_downstream_ws(websocket: WebSocket, relay: HermesRelay) -> None` —
  `await websocket.accept()`; register with the relay (its `send` closure calls
  `websocket.send_text`); loop `raw = await websocket.receive_text()` → `relay.handle_downstream_frame(...)`;
  on `WebSocketDisconnect` (or receive failure) → `finally: await relay.unregister_downstream(...)`.
  Auth posture matches `/api/events`: **no per-route token check** — the app-level
  `TrustedIngressMiddleware` is the only gate, exactly as `/api/events` relies on it (verified:
  `core/ws.py::tail_events` has no auth body; `server.py` mounts it plain). The relay's upstream
  token is separate and internal (loopback to the child).

---

## 2. Config keys

The backend is OFF BY DEFAULT in production config. Add exactly these `Config` fields (after
`gateway_adapter`, before the trusted-ingress block, keeping the frozen dataclass grouped), the
matching `_*_value(...)` lines in `load_config`, and the `config.yaml` lines.

`Config` dataclass additions:
```
    # hermes serve relay backend (S1, off by default)
    hermes_backend_enabled: bool
    hermes_backend_home: str
    hermes_backend_port: int
    hermes_backend_readiness_timeout_seconds: int
```

`load_config(...)` additions (exact lines, same helper pattern as neighbors):
```
        hermes_backend_enabled=_bool_value(
            cfg, env, "hermes_backend_enabled", "PLAN_HERMES_BACKEND_ENABLED", False
        ),
        hermes_backend_home=_str_value(
            cfg, env, "hermes_backend_home", "PLAN_HERMES_BACKEND_HOME", "data/hermes-home"
        ),
        hermes_backend_port=_int_value(
            cfg, env, "hermes_backend_port", "PLAN_HERMES_BACKEND_PORT", 9911
        ),
        hermes_backend_readiness_timeout_seconds=_int_value(
            cfg, env, "hermes_backend_readiness_timeout_seconds",
            "PLAN_HERMES_BACKEND_READINESS_TIMEOUT_SECONDS", 30
        ),
```

`config.yaml` additions (checked-in defaults; enable flag present-and-false so it stays OFF and the
checked-in file documents the switch, matching how `dispatch_enabled` is present):
```
hermes_backend_enabled: false      # S1 relay backend; off by default (contract: off in prod)
hermes_backend_home: data/hermes-home   # HERMES_HOME for the spawned `hermes serve`
hermes_backend_port: 9911          # loopback port for the spawned `hermes serve` (S0 used 9911)
hermes_backend_readiness_timeout_seconds: 30   # bound on parsing HERMES_BACKEND_READY
```

Per-key justification (each is load-bearing; nothing else added):
- `hermes_backend_enabled` — the config gate. Contract: "Config-gated and off by default." Required.
- `hermes_backend_home` — the `HERMES_HOME` the child is spawned with. Contract names it explicitly
  ("`HERMES_HOME` set to the configured planner Hermes home"). It is NOT derivable from `db_path`
  the way `core/server.py` derives the stdio-gateway home, because this backend is a distinct,
  separately-configured surface (S0 used a scratch home); a config key makes it explicit and
  overridable. Default matches the existing `data/hermes-home`.
- `hermes_backend_port` — `--port <configured>`. Contract names it. Required.
- `hermes_backend_readiness_timeout_seconds` — the "bounded timeout" for the readiness line the
  contract names. Required as a tunable per PRINCIPLES (timings live in config, never inline).

**No shutdown/grace key** — shutdown uses the single existing `shutdown_grace_seconds` deadline
(contract shutdown model). **No reconnect/backoff config keys** — the reconnect bounds are fixed
policy constants in `upstream_client.py` (§6); they are not owner-tunable knobs the contract asks
to expose, and adding config keys nobody flips would violate everything-earns-its-existence.
**No child-cleanup-budget key** — `CHILD_CLEANUP_BUDGET_SECONDS` is an internal error-path safety
bound, not an owner knob. **No session-token config** — the token is per-boot generated
(`generate_dashboard_session_token`), never persisted or configured. The config surface is exactly
the four keys above; nothing else is added.

---

## 3. Downstream subscribe message shape

A downstream declares session interest by sending, over the relay WS, exactly:
```json
{"relay":"subscribe","session_id":"<stored-or-live-session-id>"}
```

Discriminator: the top-level key **`"relay"`**. `handle_downstream_frame` first checks
`isinstance(frame, dict) and frame.get("relay") == "subscribe"`. If so, it is a relay-control
message: the relay reads `session_id` (must be a non-empty str; otherwise ignored as malformed) and
adds it to `registration.subscribed_session_ids`. It is NOT forwarded upstream.

Why `"relay"` is the correct discriminator (not, e.g., a reserved method name):
- Upstream JSON-RPC requests are `{"jsonrpc":"2.0","id":...,"method":...}`. A relay-control frame
  carries a `"relay"` marker key that the Hermes wire never uses. So the two are unambiguously
  distinguishable by structure, and the relay never has to reserve or shadow a real Hermes method
  name (which would couple us to Hermes's method set — a coupling the contract's "never interprets
  payloads" spirit forbids).
- It is minimal: one marker key + one field. No unsubscribe, no batch, no ack — the contract asks
  only that "a downstream subscribes to session ids"; unsubscribe is not required (a downstream
  drops interest by disconnecting, which clears its registration). Everything-earns-its-existence:
  no unsubscribe verb until a consumer needs it (S2+).

Every downstream frame that is NOT a `relay`-control frame is treated as **native JSON-RPC to
forward upstream, verbatim** — the relay is a transparent native relay (contract lines 5, 10, 66)
(finding #5). Concretely, in `handle_downstream_frame`, after ruling out the control frame:
- If the frame carries a correlatable `id` (a request), the relay namespaces that id and forwards
  (§4).
- If the frame has NO `id` (a JSON-RPC notification — valid, id-less traffic that the actual
  upstream dispatches; `id` is optional at `tui_gateway/server.py:1228`), the relay forwards it
  **verbatim upstream** — no id rewrite, no mapping. The relay never drops native frames.
The relay inspects only `relay`, `id`, and routing metadata — nothing else — so "never interprets
payloads beyond ids and routing metadata" stays true.

---

## 4. Id-namespacing data structures + concurrency

The single upstream connection has one integer id space (upstream ids are per-connection integers,
S0 fact). The relay allocates upstream ids onto that space and restores responses to origin.

Upstream-availability state (finding #4). The relay carries an explicit
`self._upstream: UpstreamConnection | None`. It is `None` before the first connect, and set to
`None` again on every drop/close before backoff. A downstream request that arrives while
`self._upstream is None` (initial connect, backoff, or a just-closed socket) is NOT silently
dropped and leaves NO mapping behind: the relay replies to that downstream with a relay-generated
JSON-RPC error frame carrying the downstream's ORIGINAL id:
```json
{"jsonrpc":"2.0","id":<original>,"error":{"code":-32001,"message":"upstream unavailable"}}
```
Chosen over a silent drop because it is the calm state and keeps the downstream's id space clean —
the downstream sees an explicit, correlated failure for exactly the request it sent and can retry,
rather than waiting forever on a response that will never come. No mapping is allocated for a
rejected request, so nothing can leak.

Structures on `HermesRelay` (guarded by `self._id_lock: asyncio.Lock`):
- `self._next_upstream_request_id: int` — monotonic allocator, starts at 1 (mirrors
  `GatewayChild._next_id`). Reset to 1 on each fresh upstream connection (each connection is a new
  id space; S0 fact "per-connection integers").
- `self._upstream_id_to_origin: dict[int, DownstreamRequestKey]` — **the authoritative forward
  map**: allocated upstream id → which downstream + which original downstream request id. Direct
  analogue of `GatewayChild._pending` keyed by allocated id.
- `self._downstream_id_to_upstream_ids: dict[int, set[int]]` — **per-downstream index** (finding
  #7): downstream id → the set of upstream ids currently allocated for that downstream's in-flight
  requests. This gives true O(k) disconnect cleanup (k = that downstream's own pending count) and
  is correct when one downstream reuses an original id for two in-flight requests (the set holds
  two distinct upstream ids; nothing overwrites). Both structures are mutated together under the
  one lock, so they never disagree.

Allocation (in `handle_downstream_frame`, request branch — only when `self._upstream is not None`):
1. Parse the downstream request; take its `downstream_request_id = frame["id"]`.
2. Under `self._id_lock` (this critical section contains **no `await`**): capture
   `upstream = self._upstream` (the current socket); `upstream_id = self._next_upstream_request_id;
   self._next_upstream_request_id += 1`; build
   `key = DownstreamRequestKey(registration.downstream_id, downstream_request_id)`; set
   `self._upstream_id_to_origin[upstream_id] = key` and
   `self._downstream_id_to_upstream_ids.setdefault(registration.downstream_id, set()).add(upstream_id)`.
3. Rewrite a COPY of the frame's `"id"` to `upstream_id` (leave `jsonrpc`, `method`, `params`
   untouched — ids only). Notify observers `observe_downstream_to_upstream(downstream_id, original_frame)`.
4. Send under the send lock, NOT under `_id_lock`:
   ```
   try:
       async with self._upstream_send_lock:
           await upstream.send(json.dumps(rewritten) + "\n")
   except (ConnectionClosed, OSError):
       # roll back EXACTLY the newly-allocated mapping so no orphan remains (finding #4):
       async with self._id_lock:
           self._upstream_id_to_origin.pop(upstream_id, None)
           ids = self._downstream_id_to_upstream_ids.get(registration.downstream_id)
           if ids is not None:
               ids.discard(upstream_id)
               if not ids:
                   self._downstream_id_to_upstream_ids.pop(registration.downstream_id, None)
       # then surface the same relay-generated "upstream unavailable" error frame to this
       # downstream (the drop is being handled by run()'s reconnect path).
   ```

Restoration (in the upstream read loop, response branch): a frame with `id` present and
(`result` in frame or `error` in frame):
1. Under `self._id_lock`: `key = self._upstream_id_to_origin.pop(upstream_id, None)`; if found also
   discard `upstream_id` from `self._downstream_id_to_upstream_ids[key.downstream_id]` (popping the
   set entry when it empties).
2. If `key is None`: uncorrelatable (e.g. upstream parse-error frames carry `id: null`, S0 fact; or
   a response to a since-disconnected downstream) — drop it (mirrors gateway lines 279-286
   "ignore"). Do not fan out a response to a guessed downstream.
3. Else: look up `DownstreamRegistration` by `key.downstream_id` in the registry. If still present,
   rewrite the frame's `"id"` back to `key.downstream_request_id`, notify observers
   `observe_upstream_to_downstream(frame)`, and `await registration.send(json.dumps(frame) + "\n")`.
   If the downstream already disconnected, drop.

Send-lock discipline — **one normative rule** (finding #6):
- Every upstream write goes through `self._upstream_send_lock: asyncio.Lock`. This is separate from
  `self._id_lock`, which guards the id maps + allocator (and the fan-out registry snapshot, §8).
- `_id_lock` critical sections contain no `await`. The upstream `send` is performed under
  `_upstream_send_lock`, never under `_id_lock`. This is the async analogue of the gateway's
  separate `_send_lock` vs `_lock` split (gateway lines 343-357): table mutation under one lock,
  transport write under the other, never nested table-lock-around-a-send.

Correctness under concurrent interleaved requests from multiple downstreams:
- Two downstreams issuing requests concurrently are two coroutines on one loop. The id allocation
  critical section (step 2, under `_id_lock`, no `await`) is atomic, so each gets a distinct
  `upstream_id` and a distinct forward-map entry — no interleaving corrupts the counter or the
  maps, even when the two sends interleave under `_upstream_send_lock`.
- Restoration keys strictly by `upstream_id`, so a response can only ever map back to the exact
  origin `(downstream_id, downstream_request_id)` that produced it. Two downstreams that happen to
  use the same `downstream_request_id` (e.g. both send id `1`) stay distinct because the key
  includes `downstream_id`. This is the acceptance-test-2 guarantee, exercised under real
  concurrency (§8.2).

Downstream disconnect cancels ONLY its own pending mappings (`unregister_downstream`), O(k):
- Under `self._id_lock`: `pending = self._downstream_id_to_upstream_ids.pop(registration.downstream_id,
  set())`; for each `upstream_id` in `pending`, `self._upstream_id_to_origin.pop(upstream_id, None)`.
  A late upstream response for one of those ids then finds `key is None` and is dropped (restoration
  step 2). Other downstreams' mappings are untouched. Then remove the downstream from the registry.

Event fan-out ordering:
- The upstream read loop is a single coroutine reading frames one at a time in order and dispatching
  each (response-restore or event-fanout) before reading the next. Because there is one reader and
  each snapshot's sends complete before the next frame is processed, per-downstream frame order is
  exactly upstream order. (The gateway preserves order the same way via a single reader thread;
  here a single reader coroutine.) The relay adds no buffering/coalescing — it is a transparent
  relay. Fan-out itself is snapshot-and-isolate; see §5/§8.

---

## 5. Event routing

Upstream frames with `method == "event"` (no `id`) route by `params`:
- **Session-scoped** — `params.get("session_id")` is a non-empty str: fan out to exactly the
  downstreams whose `registration.subscribed_session_ids` contains that session id. A downstream
  that never subscribed to that session receives nothing.
- **Process-level** — `params.get("session_id")` is absent/None (e.g. `gateway.ready`, S0 fact —
  the first frame after connect carries no `session_id`, no `id`): fan out to EVERY registered
  downstream.

This mirrors `GatewayChild._route_event` (gateway lines 307-313: `session_id is None` → process
feed; else session feed). The relay reads only `method`, `params.session_id`, `id` — it does not
interpret or store any other payload field.

Fan-out is **snapshot-and-isolate** (finding #8):
1. Under `self._id_lock` (no `await`), SNAPSHOT the list of recipient downstreams and their `send`
   callables — the exact set that matches this event (session subscribers, or all for process-level)
   — into a local list, IN registry order.
2. Release the lock, then `await` each snapshot entry's `send` in order. A concurrent
   `unregister_downstream` during an `await` cannot corrupt iteration, because we iterate the local
   snapshot, not the live registry.
3. Wrap each individual `send` in its own `try/except`: if one downstream's `send` raises, catch it,
   isolate that downstream (schedule/await its `unregister_downstream`), and CONTINUE delivering to
   the rest. One bad downstream MUST NOT abort the single upstream reader coroutine (which would
   break every other client). The same snapshot-and-isolate discipline applies to the response
   restoration send and to the synthetic reset broadcast below.

Upstream-reset event frame (relay-synthesized, NOT an upstream frame). On each successful reconnect
(§6), before resuming normal traffic, the relay sends to every registered downstream (via the same
snapshot-and-isolate fan-out) exactly:
```json
{"jsonrpc":"2.0","method":"event","params":{"type":"relay.upstream_reset"}}
```
- Shape rationale: it is a well-formed JSON-RPC event with a `relay.`-namespaced `type` so
  downstreams can recognize it as relay-generated and distinct from any Hermes `gateway.*`/`*.delta`
  event. It has NO `session_id` (it is process-level — it invalidates every session's live stream,
  so it must reach all downstreams, exactly like `gateway.ready`). It carries no payload beyond the
  type: S1 downstreams only need the signal to re-resume; nothing to interpret.
- The relay generates this frame; it is never received from upstream. On generation it is passed
  through the observer tee as an upstream-to-downstream frame (observers see every frame in both
  directions, including this synthetic one — matches "observers see every frame").
- On reset, the id tables are also reset: the old upstream connection's ids are dead. Under
  `_id_lock`, clear `_upstream_id_to_origin` and `_downstream_id_to_upstream_ids` and reset
  `_next_upstream_request_id = 1`. In-flight requests whose responses will never come are dropped
  (their downstreams get the reset and re-issue). Subscriptions (`subscribed_session_ids`) are NOT
  cleared — they belong to the downstream, not the upstream connection; the reset tells the
  downstream to re-resume, and its still-declared interest routes the resumed session's events.

---

## 6. Reconnect / backoff bounds

Named constants in `upstream_client.py` (imported by the relay). Exact values:
```
RECONNECT_INITIAL_DELAY_SECONDS: Final = 0.5
RECONNECT_BACKOFF_MULTIPLIER: Final = 2.0
RECONNECT_MAX_DELAY_SECONDS: Final = 10.0
```
Reconnect is **bounded by backoff, not by attempt count** (finding #3, the faithful reading of the
contract's "bounded reconnect-and-backoff"): the relay retries FOREVER until shutdown cancels the
`run()` task, with the delay CAPPED at `RECONNECT_MAX_DELAY_SECONDS`. The run loop only ends on
relay shutdown (task cancellation) — it never permanently strands connected downstreams. Delay
sequence (capped, forever until shutdown): 0.5, 1.0, 2.0, 4.0, 8.0, 10.0, 10.0, 10.0, … A
*successful* connect resets the delay to `RECONNECT_INITIAL_DELAY_SECONDS`.

`HermesRelay.run` structure (both failure paths go through the SAME backoff-and-retry):
```
first_connect = True
current_delay = RECONNECT_INITIAL_DELAY_SECONDS
while True:                                   # only exits on task cancellation (shutdown)
    try:
        upstream = await connect_upstream(self._upstream_ws_url)   # url carries ?token=
    except (ConnectionError, OSError, WebSocketException):          # CONNECT-FAILURE path
        await self._sleep(min(current_delay, RECONNECT_MAX_DELAY_SECONDS))
        current_delay *= RECONNECT_BACKOFF_MULTIPLIER
        continue
    # connected:
    current_delay = RECONNECT_INITIAL_DELAY_SECONDS               # reset delay on success
    async with self._id_lock:
        self._upstream = upstream
    if not first_connect:
        await self._reset_id_tables_and_broadcast_reset()          # §5
    first_connect = False
    try:
        async for frame_line in self._read_frames(upstream):       # normal traffic
            await self._route_upstream_frame(frame_line)
    except (ConnectionClosed, OSError):                            # ESTABLISHED-SOCKET-DROP path
        pass
    finally:
        async with self._id_lock:
            self._upstream = None                                  # mark absent BEFORE backoff
        with contextlib.suppress(Exception):
            await upstream.close()
    await self._sleep(min(current_delay, RECONNECT_MAX_DELAY_SECONDS))
    current_delay *= RECONNECT_BACKOFF_MULTIPLIER
```
- `self._sleep` is `asyncio.sleep` in production; tests inject a fake sleep that RECORDS the delay
  sequence without waiting (§8.4), so the capped sequence is asserted deterministically.
- The connect-FAILURE path (an exception raised by `connect_upstream`) and the
  established-socket-drop path (`recv` raising `ConnectionClosed` mid-read) are handled identically:
  clear `_upstream` (drop path only — connect-failure never set it), backoff-capped-sleep, multiply
  delay, loop. `_upstream` is always `None` while disconnected, so a concurrent downstream request
  gets the calm relay error frame (§4), never a corrupt mapping.
- On the very first successful connect there is no prior stream to reset, so the reset broadcast is
  skipped (`first_connect` guard) — preserving the "first-connect no reset" behavior.

These are fixed policy, not config (§2): the contract asks for bounded reconnect, not owner-tunable
reconnect knobs.

---

## 7. Server integration — exact minimal edits

Only `core/server.py`, `core/config.py`, and `config.yaml` change (config in §2). Nothing else in
these files changes; the stdio-gateway composition is untouched.

`core/server.py` — mirror the gateway gating exactly (built only when `not config.test_mode`, so
test mode never spawns; and only when the enable flag is on, via `compose_hermes_backend`).

Inside `_lifespan`, alongside the existing `loops`/`shared_gateway` locals, add locals
`hermes_backend_supervisor: Any = None`, `hermes_relay: Any = None`, and
`hermes_relay_task: Any = None`. In the `elif not config.test_mode:` branch, AFTER the existing
gateway composition (independent of it), add:
```
            from planner.hermes_backend import (
                HermesBackendUnavailable,
                compose_hermes_backend,
            )
            try:
                composition = compose_hermes_backend(
                    config=config,
                    base_env=os.environ,
                )
            except HermesBackendUnavailable:
                _log.exception("hermes serve backend unavailable; relay surface disabled")
            else:
                if composition is not None:   # None => hermes_backend_enabled is False
                    hermes_backend_supervisor = composition.supervisor
                    hermes_relay = composition.relay
                    app_.state.hermes_relay = hermes_relay
                    hermes_relay_task = asyncio.create_task(hermes_relay.run())
```
Notes: `compose_hermes_backend` (§1.2) is the single place the enable-flag gate lives; it returns
`None` when disabled (no spawn), and constructs+`wait_ready()`s the supervisor when enabled, raising
`HermesBackendUnavailable` on readiness failure (the supervisor has already terminated its child,
finding #2). Add `import asyncio` to the module's imports (not currently imported — verified).
Unavailability is caught and logged; Panels startup never crashes (contract: "never a crash of
Panels startup").

In the `finally:` of `_lifespan`, after the existing gateway shutdown, add — using the SAME single
`deadline` already computed at the top of the `finally` (line 239) — the relay first (async,
bounded), then the supervisor (finding #1):
```
            if hermes_relay is not None:
                await hermes_relay.shutdown(deadline=deadline)
            if hermes_backend_supervisor is not None:
                hermes_backend_supervisor.shutdown(deadline=deadline)
```
`hermes_relay.shutdown(deadline=)` (§1.4) closes the upstream socket, cancels `run()`, and bounds
the await by `max(0.0, deadline - monotonic())` (suppressing `TimeoutError`/`CancelledError`), so it
can never exceed the shared budget. `hermes_backend_supervisor.shutdown(deadline=)` uses the same
`deadline = _monotonic() + float(config.shutdown_grace_seconds)`. No second deadline. (`contextlib`
is used inside `relay.run`/`shutdown`, not in `server.py`; `server.py` no longer needs a
`contextlib` import since the bounded await lives inside `HermesRelay.shutdown`.)

App state + route (in `create_app`, near the other `app.state.*` assignments and the `/api/events`
route):
```
    app.state.hermes_relay = None
```
and the route body (mirrors `/api/events` — plain, no per-route auth, relies on middleware):
```
    @app.websocket("/api/relay")
    async def relay_ws(websocket: WebSocket) -> None:
        relay = app.state.hermes_relay
        if relay is None:
            await websocket.close(code=1013)   # try again later: backend disabled/unavailable
            return
        await relay_downstream_ws(websocket, relay)
```
`from planner.hermes_backend import relay_downstream_ws` at the top of `server.py`. Route path
`/api/relay` (sibling of `/api/events`; descriptive). When the backend is off/unavailable
(`hermes_relay is None`, the default and the whole test-mode case), the route closes with 1013
"service unavailable — try again later," satisfying "the relay surface reports unavailable."

That is the complete list of `server.py` edits: two top-level imports (`asyncio`,
`relay_downstream_ws`) plus the branch-local imports (`HermesBackendUnavailable`,
`compose_hermes_backend`), three lifespan locals, one gated compose block, one gated shutdown block,
one `app.state.hermes_relay = None`, one `@app.websocket("/api/relay")` route. Nothing else.

---

## 8. Test files (under `tests/unit/`) — 1:1 with the four acceptance areas

All against fakes. No test spawns a real Hermes. Four new files, one per acceptance area, plus the
route smoke folded in.

### 8.1 `tests/unit/test_hermes_backend_supervisor.py` (acceptance area 1)
Fake: `FakeHermesServeProcess` implementing `HermesServeProcess` — a scriptable stdout queue
(seed it with the readiness line or with garbage-then-ready or with EOF-before-ready), plus a
`hung` flag so `wait(timeout)` returns None until killed (the hung-child shutdown case). Records
whether `kill()`/`close_stdin()` were called. A `fake_spawn(argv, env)` closure captures argv+env
and returns the fake (mirrors `FakeGateway.spawn`) — argv/env are asserted through the FAKE, not
through any public supervisor attribute (finding #11).
Named tests:
- `test_supervisor_spawns_with_exact_env_and_args` — asserts the FAKE-captured argv ==
  `[hermes_bin, "serve", "--port", "9911", "--skip-build"]` and the FAKE-captured env has
  `HERMES_HOME` = configured home and `HERMES_DASHBOARD_SESSION_TOKEN` = the injected token, nothing
  else mutated.
- `test_supervisor_parses_readiness_line_and_returns_port` — stdout yields
  `HERMES_BACKEND_READY port=9911`; `wait_ready()` returns 9911.
- `test_supervisor_readiness_timeout_terminates_child_then_reports_unavailable` — stdout never
  yields the line and the fake child stays alive; `wait_ready(timeout=short)` calls the fake's
  `close_stdin`/`kill` (child terminated within the bounded cleanup) and THEN raises
  `HermesBackendUnavailable`. Asserts the child was terminated before the raise (finding #2 — no
  orphan).
- `test_supervisor_child_exit_before_ready_reports_unavailable` — stdout EOFs first;
  `wait_ready()` raises `HermesBackendUnavailable` (not a hang, not a crash); cleanup is a no-op on
  an already-dead child (idempotent).
- `test_supervisor_config_off_means_no_spawn` — **injectable composition test at `test_mode=False`
  (finding #9):** call `compose_hermes_backend(config=<hermes_backend_enabled=False,
  test_mode=False>, base_env={}, spawn=fake_spawn)`; assert it returns `None` and `fake_spawn` was
  NEVER called. Companion `test_supervisor_config_on_spawns_exactly_once` — same call with
  `hermes_backend_enabled=True` and a fake `spawn` returning a ready fake child; assert `fake_spawn`
  was called EXACTLY once. This varies ONLY `hermes_backend_enabled` and proves the flag is the
  operative gate (test-mode suppression is not what's under test here).
- `test_supervisor_shutdown_kills_hung_child_within_deadline` — a hung fake child;
  `shutdown(deadline=monotonic()+small)` calls `kill()` and returns within the deadline.

### 8.2 `tests/unit/test_hermes_relay_ids.py` (acceptance area 2)
Fakes: `FakeUpstreamConnection` (async `send` records sent frames, `recv` yields scripted response
frames from a queue, `close`); `FakeDownstream` (an async `send` collector + an assigned
downstream_id); a `connect_upstream` fake returning the fake upstream. Drive the relay directly (no
FastAPI).
Named tests:
- `test_two_downstreams_interleaved_requests_get_only_their_own_responses` — **real concurrency
  (finding #6):** downstream A and B each send request id `1` and `2` via two handlers run under
  `asyncio.gather`, with a BARRIER/blocking fake upstream `send` that forces the two sends to
  interleave under `_upstream_send_lock`; the relay allocates distinct upstream ids; responses fed
  back carry each downstream's original id and reach only that downstream.
- `test_upstream_sees_one_coherent_monotonic_id_space` — assert the ids the relay put on the
  upstream `send` are distinct, monotonic from 1, one per forwarded request.
- `test_same_downstream_request_id_from_two_downstreams_stays_distinct` — both send id `1`; keyed by
  `downstream_id` they never collide; both restore to the correct origin.
- `test_downstream_disconnect_cancels_only_its_pending_mappings` — A and B have pending requests;
  disconnect A; A's mappings gone (O(k) via `_downstream_id_to_upstream_ids`), B's intact; a late
  upstream response for A's old id is dropped; B's response still routes.
- `test_same_downstream_reuses_original_id_maps_both` — one downstream sends TWO in-flight requests
  with the SAME original id; both get distinct upstream ids and both are tracked in that
  downstream's set (proves the per-downstream index doesn't overwrite; finding #7).
- `test_request_while_upstream_absent_gets_relay_error_frame` — with `_upstream is None` (relay
  constructed, `run()` not yet connected / in backoff), a downstream request gets the relay-generated
  `{"jsonrpc":"2.0","id":<original>,"error":{"code":-32001,"message":"upstream unavailable"}}` and
  NO mapping is allocated (finding #4).
- `test_upstream_send_failure_rolls_back_mapping` — upstream `send` raises; the newly-allocated
  mapping is popped from BOTH structures (no leak) and the downstream gets the relay error frame
  (finding #4).
- `test_idless_downstream_frame_is_forwarded_verbatim_upstream` — a native id-less JSON-RPC
  notification (no `id`, no `relay` marker) is forwarded to the upstream `send` UNCHANGED (byte/JSON
  identical), not dropped, with no mapping allocated (finding #5).
- Route smoke (folded here): FastAPI `TestClient.websocket_connect("/api/relay")` against an app
  whose `app.state.hermes_relay is None` closes 1013; against a stubbed relay, the route
  registers/unregisters. Proves route wiring without a real backend.

### 8.3 `tests/unit/test_hermes_relay_routing.py` (acceptance area 3)
Fakes: as above plus a `RecordingRelayFrameObserver` implementing `RelayFrameObserver` collecting
both directions.
Named tests:
- `test_session_scoped_event_reaches_only_subscribed_downstreams` — A subscribes to `sess-1`, B to
  `sess-2`; an upstream event with `session_id == "sess-1"` reaches only A.
- `test_process_level_event_reaches_all_downstreams` — an upstream `gateway.ready` (no `session_id`)
  reaches both A and B.
- `test_subscribe_control_frame_is_not_forwarded_upstream` — a `{"relay":"subscribe",...}` frame
  from a downstream records interest and is never sent upstream (assert upstream `send` untouched).
- `test_tee_observer_sees_every_frame_in_both_directions` — the observer records the forwarded
  request (downstream→upstream), the routed response and events (upstream→downstream), including the
  synthetic reset.
- `test_one_downstream_send_failure_does_not_stop_others_or_kill_reader` — three subscribed
  downstreams, the middle one's `send` raises; the other two still receive the event, the failing
  one is unregistered, and the upstream reader keeps running for the next frame (finding #8).
- `test_opaque_payload_preserved_in_all_three_shapes` — **payload opacity (finding #10):** push a
  downstream REQUEST, an upstream RESPONSE, and an EVENT, each with nested unknown payload fields;
  assert the forwarded/routed payload is semantically identical except for the top-level request
  `id` the relay rewrites. For frames needing no id rewrite (id-less notification, event), assert
  the raw frame is forwarded UNCHANGED.

### 8.4 `tests/unit/test_hermes_relay_reconnect.py` (acceptance area 4)
Fake: a scripted `connect_upstream` that can (a) RAISE on an attempt (connect-failure path) and
(b) return a `FakeUpstreamConnection` whose `recv` raises a `ConnectionClosed`-like mid-traffic
(established-drop path), then return a second working connection. An injected fake `self._sleep`
records the delay sequence without waiting. (This file additionally uses a real in-process
`websockets.asyncio.server` for ONE end-to-end reconnect proving the `WebsocketsUpstreamConnection`
adapter against the real lib; the rest use fakes for determinism.)
Named tests:
- `test_established_drop_triggers_reconnect` — first connection drops mid-read; relay reconnects and
  the second connection is used; downstreams are never stranded.
- `test_connect_failure_backs_off_and_eventually_connects` — `connect_upstream` RAISES on the first
  N attempts then succeeds; the relay keeps retrying (bounded by backoff, not attempts) and connects
  (finding #3 — connect-failure, not just socket-drop, goes through backoff-and-retry).
- `test_downstreams_receive_upstream_reset_on_reconnect` — after reconnect, every registered
  downstream received exactly `{"jsonrpc":"2.0","method":"event","params":{"type":"relay.upstream_reset"}}`;
  the first connect did NOT emit a reset.
- `test_requests_flow_after_reconnect` — a request issued after reconnect is namespaced on the fresh
  id space (id starts at 1 again) and its response routes back (contract acceptance line 81).
- `test_reconnect_backoff_delay_sequence_is_capped` — via the injected fake sleep, assert the
  recorded delays are the capped sequence 0.5, 1.0, 2.0, 4.0, 8.0, 10.0, 10.0, … and that a
  successful connect resets the delay to 0.5. (No "gives up after N" test — that behavior is
  removed; the loop only ends on shutdown.)
- `test_shutdown_bounds_run_task_by_deadline` — with a run task parked in a slow `recv`,
  `await relay.shutdown(deadline=monotonic()+small)` closes the upstream, cancels the task, and
  returns within the deadline (finding #1).

Note: the former "gives up after N attempts" test is DROPPED (the behavior no longer exists); the
"requests flow after reconnect" test is the contract's subsequent-requests acceptance.

---

## 9. RED-first build order (the exact sequence the implementer follows)

Each step: write the test file/cases, run them, SEE THEM FAIL (import/attribute errors, then
assertion failures), then write the minimum module code to turn them green. Never write module code
ahead of a failing test.

1. **Config first (thinnest RED).** Add the four config assertions inside
   `test_hermes_backend_supervisor.py` (the config-off/config-on composition tests read
   `hermes_backend_enabled` and the four keys via a `Config`; `test_config.py` is out of allowlist).
   RED: `Config` has no `hermes_backend_*`. GREEN: add the four fields + `_*_value` lines to
   `core/config.py` and the `config.yaml` lines (§2).
2. **Supervisor + composition seam.** Write `test_hermes_backend_supervisor.py` (8.1) with the
   `FakeHermesServeProcess` fake, including the readiness-cleanup test, both config-gate composition
   tests, and the hung-child shutdown test. RED. Then write `supervisor.py` (§1.2) — supervisor,
   `_terminate_child`, `wait_ready` cleanup, and `compose_hermes_backend` — until all green.
3. **Relay ids.** Write `test_hermes_relay_ids.py` (8.2) with the fake upstream/downstream, the
   real-concurrency interleave test, the per-downstream-index tests, the upstream-absent error-frame
   test, the send-failure rollback test, the id-less verbatim-forward test, and the route smoke. RED.
   Then write the id-namespacing core of `relay.py` (§1.4, §4) + value types + upstream-availability
   state + `_upstream_send_lock` until green.
4. **Relay routing + tee.** Write `test_hermes_relay_routing.py` (8.3), including the fan-out
   isolation test and the opaque-payload test. RED. Extend `relay.py` with event routing (§5), the
   subscribe control handling (§3), snapshot-and-isolate fan-out (§5/§8), and the observer tee until
   green.
5. **Reconnect + reset + bounded shutdown.** Write `test_hermes_relay_reconnect.py` (8.4),
   including the connect-failure backoff test, the capped-delay-sequence test, and the
   bounded-shutdown test. RED. Write `upstream_client.py` (§1.3, §6) constants + adapter +
   `connect_websockets_upstream`, and the `HermesRelay.run` reconnect loop, synthetic reset (§5,
   §6), and `HermesRelay.shutdown` (§1.4) until green.
6. **Server integration.** Apply the `server.py` edits (§7) — `compose_hermes_backend` wiring,
   `/api/relay` route, bounded relay+supervisor shutdown. The route smoke (in 8.2) drives the RED;
   write `downstream_route.py` (§1.5) and the `server.py` edits until green.
7. **Full `./verify`.** Confirm every existing test still passes unchanged and the four new files
   pass. Do not re-run to re-quote.

---

## 10. Bounded file allowlist

The implementer may create or edit ONLY these files:

Create (new):
- `src/planner/hermes_backend/__init__.py`
- `src/planner/hermes_backend/supervisor.py`
- `src/planner/hermes_backend/upstream_client.py`
- `src/planner/hermes_backend/relay.py`
- `src/planner/hermes_backend/downstream_route.py`
- `tests/unit/test_hermes_backend_supervisor.py`
- `tests/unit/test_hermes_relay_ids.py`
- `tests/unit/test_hermes_relay_routing.py`
- `tests/unit/test_hermes_relay_reconnect.py`

Edit (minimal, as specified in §2 and §7):
- `src/planner/core/config.py` — four `Config` fields + four `_*_value(...)` lines. Nothing else.
- `src/planner/core/server.py` — two top-level imports + branch-local imports, three lifespan
  locals, one gated compose block, one gated shutdown block, one `app.state.hermes_relay = None`,
  one `@app.websocket("/api/relay")` route. Nothing else.
- `config.yaml` — four keys (§2).

PROHIBITED (restate): no edits under `src/planner/minds/`, `src/planner/chat/`, or
`src/planner/runtime/`. The composition helper `compose_hermes_backend` lives in
`src/planner/hermes_backend/` (in the allowlist), NOT a new top-level module and NOT in
minds/chat/runtime. No edits to `tests/unit/conftest.py` (add fixtures locally in each new test
file). No new config keys beyond the four. No production enablement (`hermes_backend_enabled`
ships `false`). No chat/vocabulary/worker/CLI/Hermes-side changes. The tee seam ships with TEST
observers only — no product consumer.

---

## Decisions the contract left open (named + one-line justification)

1. **Two concurrency domains (supervisor = threads, relay = async).** The supervisor reads a
   subprocess stdout for readiness (thread/`SpawnFn`, like the gateway); the relay fans out over the
   FastAPI event loop (async, like `tail_events`). Forcing one model on both is the wrong structure.
2. **Reconnect is bounded by backoff (capped delay), not by attempt count.** The relay retries
   forever until shutdown with the delay capped at `RECONNECT_MAX_DELAY_SECONDS`; the run loop only
   ends on task cancellation. This is the faithful reading of "bounded reconnect-and-backoff" and
   never permanently strands connected downstreams (finding #3). Fixed policy constants, not config.
3. **`hermes_backend_home` is a config key, not derived from `db_path`.** This backend is a distinct,
   separately-configured surface (S0 used a scratch home); an explicit overridable key beats a
   silent derivation. Default matches the existing `data/hermes-home`.
4. **Per-downstream id index `_downstream_id_to_upstream_ids: dict[int, set[int]]`.** Gives true
   O(k) disconnect cleanup in that downstream's own pending set and stays correct when a downstream
   reuses one original id for two in-flight requests (the set holds both upstream ids; nothing
   overwrites). `_upstream_id_to_origin` remains the authoritative forward map (finding #7).
5. **Disconnected-send behavior: relay-generated error frame.** When a downstream request arrives
   while upstream is absent/closed, the relay replies to that downstream with
   `{"jsonrpc":"2.0","id":<original>,"error":{"code":-32001,"message":"upstream unavailable"}}` and
   allocates no mapping — the calm state that keeps the downstream's id space clean and leaves no
   orphan (finding #4). A failed upstream `send` rolls back exactly the newly-allocated mapping and
   surfaces the same frame.
6. **Readiness-failure cleanup: the supervisor terminates its own child before reporting
   unavailable.** On any post-spawn readiness failure, `wait_ready` runs a bounded
   `_terminate_child` (close_stdin → wait → kill → wait, `CHILD_CLEANUP_BUDGET_SECONDS`) BEFORE
   raising `HermesBackendUnavailable`, so dropping the supervisor reference never orphans a live
   child (finding #2).
7. **Composition seam `compose_hermes_backend` in `hermes_backend/`.** A minimal helper the lifespan
   calls; it is the single place the enable-flag gate lives, so the config-off/config-on tests drive
   it at `test_mode=False` with an injected fake spawn — proving the flag, not test-mode, is the
   operative gate (finding #9).
8. **Subscribe discriminator is a top-level `"relay":"subscribe"` marker.** Structurally disjoint
   from JSON-RPC (a marker key the Hermes wire never uses) and does not shadow any Hermes method
   name — keeps the relay from coupling to Hermes's method set. Every non-control frame is forwarded
   verbatim (finding #5): id-bearing requests are namespaced; id-less notifications are forwarded
   unchanged; nothing native is dropped.
9. **`relay.upstream_reset` shape** — a process-level JSON-RPC event with a `relay.`-namespaced type
   and no payload: minimal signal to re-resume, recognizably relay-generated, reaches all
   downstreams like `gateway.ready`.
10. **Route path `/api/relay`, closes 1013 when unavailable.** Descriptive sibling of `/api/events`;
    1013 ("try again later") is the calm-unavailable signal the contract asks the surface to report.
11. **No unsubscribe verb.** The contract asks only that a downstream subscribes; a downstream drops
    interest by disconnecting (registration cleared). No speculative verb until a consumer needs it.
