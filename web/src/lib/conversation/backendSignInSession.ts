/** The Backends page's side of a sign-in: entering, watching, cancelling, leaving.
 *
 * This is deliberately not in the component. All of it is ordering, and ordering in markup
 * is ordering nothing can test: a page that forgets to resume a pending sign-in, or leaves
 * a timer running after the screen is gone, or lets a slow read overwrite a fresh result,
 * looks exactly like a page that works.
 *
 * Three rules it keeps.
 *
 * **Entering a page finds what is already there.** Nothing about a sign-in is persisted,
 * so a reconnecting page cannot reconstruct one — it has to ask. If a sign-in is pending
 * the page shows the same URL and code and resumes watching it. If the server has none,
 * the page shows nothing, because nothing is what there is.
 *
 * **Leaving stops everything the page is doing, and nothing the server is doing.** Closing
 * the screen must never cancel a sign-in: the whole point of a device code is finishing it
 * somewhere else. So `leave` clears timers and touches no server state — but clearing the
 * one armed timer is not enough on its own, because a read already in flight will come
 * back and arm another. `leave` marks the page inactive, and every asynchronous
 * continuation checks that before it announces anything or schedules anything. Entering
 * again makes it active again and reconnects normally.
 *
 * **A stale continuation can never overwrite a fresh result.** Reads are in flight while a
 * person presses things. Every one remembers which generation of this page's state it
 * began under, and one whose generation has moved on is dropped. Without that, a slow poll
 * answering after a cancel puts the cancelled sign-in back on screen.
 */

import { signInIsPending } from "./backendSignIn";
import type { BackendSignIn, ConversationBackendKey } from "./wire";

/** How long between asks while a sign-in is pending. */
export const SIGN_IN_POLL_MS = 2000;

/** Everything this reaches outside itself, so a test can hand over all of it. */
export type SignInPorts = {
  start(backendKey: ConversationBackendKey): Promise<{ sign_in: BackendSignIn }>;
  read(backendKey: ConversationBackendKey): Promise<{ sign_in: BackendSignIn | null }>;
  cancel(
    backendKey: ConversationBackendKey,
    sessionId: string
  ): Promise<{ sign_in: BackendSignIn }>;
  /** Re-read the backend cards, because a settled sign-in changes what identity says. */
  reloadCards(): Promise<void>;
  setTimer(run: () => void, ms: number): unknown;
  clearTimer(handle: unknown): void;
};

/** What the page draws from. */
export type SignInPageState = {
  signIn: BackendSignIn | null;
  busy: boolean;
  error: string | null;
};

const AT_REST: SignInPageState = { signIn: null, busy: false, error: null };

function sentenceFor(problem: unknown): string {
  if (problem instanceof Error && problem.message) return problem.message;
  return "The request did not succeed.";
}

/**
 * The state a pending sign-in becomes when the server no longer has it.
 *
 * This is the restart case. The page was watching something pending, the server came back
 * without it, and the honest thing to say is that it ended — not to keep showing a code
 * nobody will honour, and not to claim it failed.
 */
function endedAfterItVanished(pending: BackendSignIn): BackendSignIn {
  return {
    ...pending,
    state: "ended",
    verification_url: null,
    user_code: null,
    detail: "The pending sign-in ended. Start another one if you still need it."
  };
}

export class BackendSignInSession {
  private readonly ports: SignInPorts;
  private readonly announce: (state: SignInPageState) => void;
  private state: SignInPageState = AT_REST;
  private timer: unknown = null;
  /** Bumped by anything that establishes a newer truth than a continuation in flight. */
  private generation = 0;
  /** False until the page enters, and again the moment it leaves. */
  private active = false;

  constructor(ports: SignInPorts, announce: (state: SignInPageState) => void) {
    this.ports = ports;
    this.announce = announce;
  }

  current(): SignInPageState {
    return this.state;
  }

  /** Page entry and reconnect: find the sign-in already in force, and watch it. */
  async enter(backendKey: ConversationBackendKey): Promise<void> {
    this.active = true;
    this.generation += 1;
    const began = this.generation;
    let answer: { sign_in: BackendSignIn | null };
    try {
      answer = await this.ports.read(backendKey);
    } catch {
      // A reading that did not arrive is not news. The page shows what it had.
      return;
    }
    if (!this.isCurrent(began)) return;
    this.put({ signIn: answer.sign_in });
    if (signInIsPending(answer.sign_in)) this.watch(backendKey);
  }

  async start(backendKey: ConversationBackendKey): Promise<void> {
    if (this.state.busy) return;
    // A press is proof the page is live, so an action makes it active the way entering
    // does. Depending on `enter` having run first would make these silently do nothing.
    this.active = true;
    this.generation += 1;
    this.stopWatching();
    this.put({ busy: true, error: null });
    const began = this.generation;
    try {
      const answer = await this.ports.start(backendKey);
      if (!this.isCurrent(began)) return;
      this.generation += 1;
      this.put({ signIn: answer.sign_in, busy: false });
      if (signInIsPending(answer.sign_in)) this.watch(backendKey);
    } catch (problem) {
      if (!this.isCurrent(began)) return;
      this.generation += 1;
      this.put({ busy: false, error: sentenceFor(problem) });
    }
  }

  async cancel(backendKey: ConversationBackendKey): Promise<void> {
    const pending = this.state.signIn;
    if (pending === null || this.state.busy) return;
    this.active = true;
    this.generation += 1;
    this.stopWatching();
    this.put({ busy: true, error: null });
    const began = this.generation;
    try {
      const answer = await this.ports.cancel(backendKey, pending.session_id);
      if (!this.isCurrent(began)) return;
      this.generation += 1;
      this.put({ signIn: answer.sign_in, busy: false });
    } catch (problem) {
      if (!this.isCurrent(began)) return;
      // A refused cancel means this session is not the one in force, so what the server
      // has is the answer rather than what this page was holding.
      this.generation += 1;
      this.put({ busy: false, error: sentenceFor(problem) });
      await this.enter(backendKey);
    }
    if (!this.active) return;
    await this.ports.reloadCards();
  }

  /** The screen is gone. Stop everything this page is doing, and only this page's.
   *
   * Marking the page inactive is the part that matters. Clearing the armed timer alone
   * would leave a read already in flight free to come back and arm another, so a departed
   * page would keep polling for as long as the process lived.
   */
  leave(): void {
    this.active = false;
    this.generation += 1;
    this.stopWatching();
  }

  /** Whether a continuation that began at this generation may still act. */
  private isCurrent(began: number): boolean {
    return this.active && began === this.generation;
  }

  private watch(backendKey: ConversationBackendKey): void {
    this.stopWatching();
    if (!this.active) return;
    if (!signInIsPending(this.state.signIn)) return;
    const began = this.generation;
    this.timer = this.ports.setTimer(() => {
      void this.tick(backendKey, began);
    }, SIGN_IN_POLL_MS);
  }

  private async tick(backendKey: ConversationBackendKey, began: number): Promise<void> {
    if (!this.isCurrent(began)) return;
    const watched = this.state.signIn;
    let answer: { sign_in: BackendSignIn | null };
    try {
      answer = await this.ports.read(backendKey);
    } catch {
      // Keep watching rather than inventing an outcome from a failed read.
      if (this.isCurrent(began)) this.watch(backendKey);
      return;
    }
    if (!this.isCurrent(began)) return;
    if (answer.sign_in === null) {
      // The server has no sign-in where this page was watching one. That is a restart.
      this.put({ signIn: watched === null ? null : endedAfterItVanished(watched) });
      await this.ports.reloadCards();
      return;
    }
    this.put({ signIn: answer.sign_in });
    if (signInIsPending(answer.sign_in)) {
      this.watch(backendKey);
      return;
    }
    await this.ports.reloadCards();
  }

  private stopWatching(): void {
    if (this.timer === null) return;
    this.ports.clearTimer(this.timer);
    this.timer = null;
  }

  private put(change: Partial<SignInPageState>): void {
    this.state = { ...this.state, ...change };
    this.announce(this.state);
  }
}
