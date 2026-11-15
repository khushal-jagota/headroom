/** One read of a managed file, shared by every preview that names it.
 *
 * Two messages that link the same artifact draw two previews, and a lens that brings
 * rows back draws them again. Each preview used to fetch for itself, so a file named
 * three times was read three times. They now share the read that is already running.
 *
 * Only a read that is *running* is shared. The entry goes the moment the read settles,
 * either way, so nothing resolved is kept here: a later read goes back to the server,
 * and the conditional answer there decides whether any bytes travel. Retained views
 * reuse their mounted content without another call. Evicted views read again. That leaves one
 * place that knows whether a copy is still good, instead of two with different rules.
 */
type SharedRead = {
  body: Promise<string>;
  request: AbortController;
  /** Drops every "this preview left" listener once the read is over. Without it those
   *  listeners hold this entry, and the entry holds the whole response text, for as long
   *  as the previews stay mounted — which for a large artifact is the thing the bounded
   *  preview exists to avoid. */
  listeners: AbortController;
  readers: number;
};

const running = new Map<string, SharedRead>();

export type ManagedFileReadOptions = { artifactRootHref?: string; fresh?: boolean };

export function readManagedFile(href: string, untilTheReaderLeaves: AbortSignal, options: ManagedFileReadOptions = {}): Promise<string> {
  const key = JSON.stringify([href, options.artifactRootHref ?? null, Boolean(options.fresh)]);
  const shared = running.get(key) ?? startRead(href, key, options);
  shared.readers += 1;
  // One preview leaving must not take the read away from the others, so the request is
  // abandoned only when the last of them has gone.
  untilTheReaderLeaves.addEventListener(
    "abort",
    () => {
      shared.readers -= 1;
      if (shared.readers > 0) return;
      // Dropped before the abort, not after. Aborting settles the read, and a preview
      // that arrives in between would otherwise join a request already on its way out
      // and be handed its failure.
      forget(key, shared);
      shared.request.abort();
    },
    { signal: shared.listeners.signal }
  );
  return shared.body;
}

function startRead(href: string, key: string, options: ManagedFileReadOptions): SharedRead {
  const request = new AbortController();
  const body = fetch(href, {
    signal: request.signal,
    ...(options.artifactRootHref ? {headers: {'X-Panels-Artifact-Root': options.artifactRootHref}, redirect: 'error' as const} : {}),
    ...(options.fresh ? {cache: 'no-store' as const} : {})
  }).then((response) => {
    if (!response.ok) throw new Error(`file fetch failed: ${response.status}`);
    return response.text();
  });
  const shared: SharedRead = { body, request, listeners: new AbortController(), readers: 0 };
  running.set(key, shared);
  const done = (): void => {
    forget(key, shared);
    // Nothing more can be decided about a read that is over, and letting the listeners
    // go is what lets the response text be collected.
    shared.listeners.abort();
  };
  body.then(done, done);
  return shared;
}

/** Drop this read, and only this one. A newer read of the same file keeps its place. */
function forget(href: string, shared: SharedRead): void {
  if (running.get(href) === shared) running.delete(href);
}
