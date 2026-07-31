/** Serialize an async action while coalescing requests that arrive during it.
 *
 * A request made while the action runs asks for one trailing pass. A true flag wins
 * across all requests waiting for that pass. The promise returned to every caller owns
 * settlement too: if intent arrives after the drain finishes but before its owner
 * detaches, the owner's `finally` hands off to a new drain and waits for it.
 */
export function createCoalescedAsyncCall(
  run: (force: boolean) => Promise<void>
): (force?: boolean) => Promise<void> {
  let active: Promise<void> | null = null;
  let requested = false;
  let forceRequested = false;

  function request(force = false): Promise<void> {
    requested = true;
    forceRequested ||= force;

    if (active === null) {
      const drain = (async () => {
        while (requested) {
          const forceThisPass = forceRequested;
          requested = false;
          forceRequested = false;
          await run(forceThisPass);
        }
      })();

      const owned = drain.finally(() => {
        active = null;
        return requested ? request() : undefined;
      });
      active = owned;
    }

    return active;
  }

  return request;
}
