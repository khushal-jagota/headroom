/** The Backends page's orchestration of a sign-in: entering, watching, cancelling, leaving.
 *
 * None of this is presentation. It is ordering, and a page that gets it wrong looks
 * exactly like a page that works: a reconnect that forgets a pending sign-in, a timer
 * still firing after the screen is gone, a slow read putting a cancelled sign-in back on
 * screen. Each of those is a test here.
 *
 * The ports are all fakes. Timers are driven by hand rather than by waiting, so nothing
 * here depends on real time passing.
 */

import { describe, expect, it } from "vitest";

import {
  BackendSignInSession,
  type SignInPageState,
  type SignInPorts
} from "../src/lib/conversation/backendSignInSession";
import type { BackendSignIn } from "../src/lib/conversation/wire";

function signIn(overrides: Partial<BackendSignIn> = {}): BackendSignIn {
  return {
    session_id: "s1",
    state: "pending",
    verification_url: "https://auth.openai.com/codex/device",
    user_code: "ABCD-1234",
    started_at: "2026-10-16T00:00:00+00:00",
    deadline_at: "2026-10-16T00:10:00+00:00",
    detail: null,
    account_label: null,
    ...overrides
  };
}

type Deferred<T> = { promise: Promise<T>; settle: (value: T) => void };

function deferred<T>(): Deferred<T> {
  let settle!: (value: T) => void;
  const promise = new Promise<T>((resolve) => {
    settle = resolve;
  });
  return { promise, settle };
}

class Bench {
  readings: Array<{ sign_in: BackendSignIn | null } | Deferred<{ sign_in: BackendSignIn | null }>> = [];
  startAnswer: { sign_in: BackendSignIn } | Error = { sign_in: signIn() };
  cancelAnswer: { sign_in: BackendSignIn } | Error = {
    sign_in: signIn({ state: "ended", user_code: null, verification_url: null })
  };
  reads = 0;
  starts = 0;
  cancels = 0;
  cardReloads = 0;
  timers: Array<{ run: () => void; ms: number; cleared: boolean }> = [];
  states: SignInPageState[] = [];
  session: BackendSignInSession;

  constructor() {
    const ports: SignInPorts = {
      start: async () => {
        this.starts += 1;
        if (this.startAnswer instanceof Error) throw this.startAnswer;
        return this.startAnswer;
      },
      read: async () => {
        const next = this.readings[Math.min(this.reads, this.readings.length - 1)];
        this.reads += 1;
        if (next === undefined) return { sign_in: null };
        if (next instanceof Error) throw next;
        if ("promise" in next) return next.promise;
        return next;
      },
      cancel: async () => {
        this.cancels += 1;
        if (this.cancelAnswer instanceof Error) throw this.cancelAnswer;
        return this.cancelAnswer;
      },
      reloadCards: async () => {
        this.cardReloads += 1;
      },
      setTimer: (run, ms) => {
        const entry = { run, ms, cleared: false };
        this.timers.push(entry);
        return entry;
      },
      clearTimer: (handle) => {
        (handle as { cleared: boolean }).cleared = true;
      }
    };
    this.session = new BackendSignInSession(ports, (state) => this.states.push(state));
  }

  /** Fire the one timer that is still armed, the way a browser would. */
  async fireTimer(): Promise<void> {
    const live = this.timers.filter((timer) => !timer.cleared);
    expect(live.length).toBe(1);
    live[0].cleared = true;
    live[0].run();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  }

  armedTimers(): number {
    return this.timers.filter((timer) => !timer.cleared).length;
  }
}

describe("entering the page", () => {
  it("picks up a sign-in that is already pending and shows the same code", async () => {
    // This is the reconnect the page had no implementation of: nothing is persisted, so
    // the only way to find a running sign-in is to ask for it on entry.
    const bench = new Bench();
    bench.readings = [{ sign_in: signIn({ user_code: "WXYZ-9999" }) }];

    await bench.session.enter("codex");

    expect(bench.reads).toBe(1);
    expect(bench.session.current().signIn?.user_code).toBe("WXYZ-9999");
    expect(bench.armedTimers()).toBe(1);
  });

  it("shows nothing and watches nothing when the server has no sign-in", async () => {
    const bench = new Bench();
    bench.readings = [{ sign_in: null }];

    await bench.session.enter("codex");

    expect(bench.session.current().signIn).toBeNull();
    expect(bench.armedTimers()).toBe(0);
  });

  it("keeps what it had when the reading does not arrive", async () => {
    const bench = new Bench();
    bench.readings = [new Error("network") as never];

    await bench.session.enter("codex");

    expect(bench.session.current().signIn).toBeNull();
    expect(bench.armedTimers()).toBe(0);
  });
});

describe("watching a pending sign-in", () => {
  it("keeps asking while it is pending and stops once it settles", async () => {
    const bench = new Bench();
    bench.readings = [
      { sign_in: signIn() },
      { sign_in: signIn() },
      { sign_in: signIn({ state: "confirmed", account_label: "owner@example.com" }) }
    ];

    await bench.session.enter("codex");
    await bench.fireTimer();
    expect(bench.session.current().signIn?.state).toBe("pending");
    expect(bench.armedTimers()).toBe(1);

    await bench.fireTimer();
    expect(bench.session.current().signIn?.state).toBe("confirmed");
    expect(bench.armedTimers()).toBe(0);
    // A settled sign-in changes what identity says, so the cards are re-read.
    expect(bench.cardReloads).toBe(1);
  });

  it("says a pending sign-in ended when the server no longer has it", async () => {
    // The restart case. The page was watching something pending and the server came back
    // without it. Showing a code nobody will honour would be worse than saying it ended.
    const bench = new Bench();
    bench.readings = [{ sign_in: signIn() }, { sign_in: null }];

    await bench.session.enter("codex");
    await bench.fireTimer();

    const shown = bench.session.current().signIn;
    expect(shown?.state).toBe("ended");
    expect(shown?.user_code).toBeNull();
    expect(shown?.detail).toContain("ended");
    expect(bench.armedTimers()).toBe(0);
  });

  it("keeps watching when a poll fails rather than inventing an outcome", async () => {
    const bench = new Bench();
    bench.readings = [{ sign_in: signIn() }, new Error("network") as never];

    await bench.session.enter("codex");
    await bench.fireTimer();

    expect(bench.session.current().signIn?.state).toBe("pending");
    expect(bench.armedTimers()).toBe(1);
  });
});

describe("leaving the page", () => {
  it("stops asking and cancels nothing on the server", async () => {
    const bench = new Bench();
    bench.readings = [{ sign_in: signIn() }];

    await bench.session.enter("codex");
    expect(bench.armedTimers()).toBe(1);

    bench.session.leave();

    expect(bench.armedTimers()).toBe(0);
    // The whole point of a device code is finishing it elsewhere, so closing a screen
    // must never end the sign-in.
    expect(bench.cancels).toBe(0);
  });
});

describe("a stale read cannot overwrite a fresh result", () => {
  it("drops a poll that answers after a cancel has landed", async () => {
    // Without the generation guard this puts the cancelled sign-in back on screen.
    const bench = new Bench();
    const slow = deferred<{ sign_in: BackendSignIn | null }>();
    bench.readings = [{ sign_in: signIn() }, slow];

    await bench.session.enter("codex");
    // The poll goes out and has not answered yet.
    const live = bench.timers.filter((timer) => !timer.cleared);
    live[0].cleared = true;
    live[0].run();
    await Promise.resolve();

    await bench.session.cancel("codex");
    expect(bench.session.current().signIn?.state).toBe("ended");

    // Now the old read finally answers, still saying pending.
    slow.settle({ sign_in: signIn() });
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    expect(bench.session.current().signIn?.state).toBe("ended");
    expect(bench.armedTimers()).toBe(0);
  });

  it("drops an entry reading that answers after a start has landed", async () => {
    const bench = new Bench();
    const slow = deferred<{ sign_in: BackendSignIn | null }>();
    bench.readings = [slow];
    bench.startAnswer = { sign_in: signIn({ session_id: "s2", user_code: "NEW-CODE" }) };

    const entering = bench.session.enter("codex");
    await bench.session.start("codex");
    expect(bench.session.current().signIn?.user_code).toBe("NEW-CODE");

    slow.settle({ sign_in: null });
    await entering;

    expect(bench.session.current().signIn?.session_id).toBe("s2");
  });
});

describe("starting and cancelling", () => {
  it("watches the sign-in it just started", async () => {
    const bench = new Bench();
    bench.readings = [{ sign_in: signIn() }];

    await bench.session.start("codex");

    expect(bench.starts).toBe(1);
    expect(bench.session.current().signIn?.user_code).toBe("ABCD-1234");
    expect(bench.session.current().busy).toBe(false);
    expect(bench.armedTimers()).toBe(1);
  });

  it("reports a refused start and stays out of the way", async () => {
    const bench = new Bench();
    bench.startAnswer = new Error("codex is not installed on this machine");

    await bench.session.start("codex");

    expect(bench.session.current().error).toContain("not installed");
    expect(bench.session.current().busy).toBe(false);
    expect(bench.armedTimers()).toBe(0);
  });

  it("stops watching when a cancel lands, and re-reads the cards", async () => {
    const bench = new Bench();
    bench.readings = [{ sign_in: signIn() }];

    await bench.session.enter("codex");
    await bench.session.cancel("codex");

    expect(bench.session.current().signIn?.state).toBe("ended");
    expect(bench.armedTimers()).toBe(0);
    expect(bench.cardReloads).toBe(1);
  });

  it("asks the server what is true when a cancel is refused as stale", async () => {
    // A refused cancel means this page was holding a session that is not the one in
    // force, so the server's answer replaces what the page thought.
    const bench = new Bench();
    bench.readings = [{ sign_in: signIn() }, { sign_in: signIn({ session_id: "s9" }) }];
    bench.cancelAnswer = new Error("that sign-in is not the one in force any more");

    await bench.session.enter("codex");
    await bench.session.cancel("codex");

    expect(bench.session.current().error).toContain("not the one in force");
    expect(bench.session.current().signIn?.session_id).toBe("s9");
  });

  it("ignores a second press while one is in flight", async () => {
    const bench = new Bench();
    bench.readings = [{ sign_in: signIn() }];

    const first = bench.session.start("codex");
    await bench.session.start("codex");
    await first;

    expect(bench.starts).toBe(1);
  });
});

describe("leaving while work is in flight", () => {
  it("drops a deferred entry reading, announcing nothing and arming nothing", async () => {
    // The defect: leave cleared the one armed timer, but an entry read already in flight
    // came back afterwards, announced, and armed a fresh watcher.
    const bench = new Bench();
    const slow = deferred<{ sign_in: BackendSignIn | null }>();
    bench.readings = [slow];

    const entering = bench.session.enter("codex");
    bench.session.leave();
    const announcementsAtLeave = bench.states.length;

    slow.settle({ sign_in: signIn() });
    await entering;

    expect(bench.states.length).toBe(announcementsAtLeave);
    expect(bench.armedTimers()).toBe(0);
    expect(bench.session.current().signIn).toBeNull();
  });

  it("drops a deferred poll, and asks nothing further", async () => {
    const bench = new Bench();
    const slow = deferred<{ sign_in: BackendSignIn | null }>();
    bench.readings = [{ sign_in: signIn() }, slow];

    await bench.session.enter("codex");
    const live = bench.timers.filter((timer) => !timer.cleared);
    live[0].cleared = true;
    live[0].run();
    await Promise.resolve();

    bench.session.leave();
    const announcementsAtLeave = bench.states.length;
    const readsAtLeave = bench.reads;

    slow.settle({ sign_in: signIn() });
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    expect(bench.states.length).toBe(announcementsAtLeave);
    expect(bench.armedTimers()).toBe(0);
    expect(bench.reads).toBe(readsAtLeave);
  });

  it("drops a deferred start, so a departed page never begins watching", async () => {
    const bench = new Bench();
    const slow = deferred<{ sign_in: BackendSignIn }>();
    bench.readings = [{ sign_in: null }];
    // A start whose answer arrives after the page is gone.
    let settleStart!: (value: { sign_in: BackendSignIn }) => void;
    bench.startAnswer = { sign_in: signIn() };
    const ports = bench.session as unknown as { ports: SignInPorts };
    ports.ports = {
      ...ports.ports,
      start: async () => {
        bench.starts += 1;
        return slow.promise;
      }
    };
    settleStart = slow.settle;

    const starting = bench.session.start("codex");
    bench.session.leave();
    const announcementsAtLeave = bench.states.length;

    settleStart({ sign_in: signIn() });
    await starting;

    expect(bench.states.length).toBe(announcementsAtLeave);
    expect(bench.armedTimers()).toBe(0);
  });

  it("drops a deferred cancel, and reloads no cards for a page that has gone", async () => {
    const bench = new Bench();
    bench.readings = [{ sign_in: signIn() }];
    const slow = deferred<{ sign_in: BackendSignIn }>();
    const ports = bench.session as unknown as { ports: SignInPorts };
    ports.ports = {
      ...ports.ports,
      cancel: async () => {
        bench.cancels += 1;
        return slow.promise;
      }
    };

    await bench.session.enter("codex");
    const cancelling = bench.session.cancel("codex");
    bench.session.leave();
    const announcementsAtLeave = bench.states.length;

    slow.settle({ sign_in: signIn({ state: "ended" }) });
    await cancelling;

    expect(bench.states.length).toBe(announcementsAtLeave);
    expect(bench.cardReloads).toBe(0);
    expect(bench.armedTimers()).toBe(0);
  });

  it("reconnects normally when the page is entered again", async () => {
    const bench = new Bench();
    const slow = deferred<{ sign_in: BackendSignIn | null }>();
    bench.readings = [slow, { sign_in: signIn({ user_code: "BACK-AGAIN" }) }];

    const entering = bench.session.enter("codex");
    bench.session.leave();
    slow.settle({ sign_in: signIn() });
    await entering;
    expect(bench.session.current().signIn).toBeNull();

    // Entering again is a live page once more, and it finds the pending sign-in.
    await bench.session.enter("codex");

    expect(bench.session.current().signIn?.user_code).toBe("BACK-AGAIN");
    expect(bench.armedTimers()).toBe(1);
  });
});
