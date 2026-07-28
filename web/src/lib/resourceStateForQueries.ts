// A query result, as much of one as a loading gate looks at.
type QueryResultForGate = {
  readonly data: unknown;
  readonly error: unknown;
  readonly isFetching: boolean;
};

// The three things ResourceState needs, worked out from several reads at once.
//
// Every field of every result is read here, before any of them decides
// anything. That is the whole point of this function, and it is not visible
// from the values it returns. A gate written in place as
// `first.isFetching || second.isFetching` stops as soon as one read settles the
// answer, so on a render where the first read is still pending the later ones
// are looked at for their error and nothing else. A screen is only told about
// the parts of a read it has already looked at, so when those later answers
// arrive the screen is never told, goes on showing the pending snapshot it was
// given, and stays on its loading line until the page is reloaded. Nothing is
// wrong with the values — the screen has simply stopped being woken.
export function resourceStateForQueries(
  ...results: readonly QueryResultForGate[]
): { error: unknown; loading: boolean; hasData: boolean } {
  const errors = results.map((result) => result.error);
  const fetching = results.map((result) => result.isFetching);
  const answered = results.map((result) => result.data !== undefined);
  return {
    error: errors.find((error) => error != null),
    loading: fetching.some((value) => value),
    hasData: answered.every((value) => value)
  };
}
