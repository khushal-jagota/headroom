import { describe, expect, it } from "vitest";

import { resourceStateForQueries } from "../src/lib/resourceStateForQueries";

function observedQuery(
  label: string,
  values: { data: unknown; error: unknown; isFetching: boolean },
  reads: string[]
) {
  return {
    get data() {
      reads.push(`${label}.data`);
      return values.data;
    },
    get error() {
      reads.push(`${label}.error`);
      return values.error;
    },
    get isFetching() {
      reads.push(`${label}.isFetching`);
      return values.isFetching;
    }
  };
}

describe("resource state for several queries", () => {
  it("reads every field even when an earlier query already determines the state", () => {
    const reads: string[] = [];
    const first = observedQuery(
      "first",
      { data: undefined, error: null, isFetching: true },
      reads
    );
    const second = observedQuery(
      "second",
      { data: { ready: true }, error: null, isFetching: false },
      reads
    );

    expect(resourceStateForQueries(first, second)).toEqual({
      error: undefined,
      loading: true,
      hasData: false
    });
    expect(reads).toEqual([
      "first.error",
      "second.error",
      "first.isFetching",
      "second.isFetching",
      "first.data",
      "second.data"
    ]);
  });

  it("returns the first error after every query answers", () => {
    const reads: string[] = [];
    const failure = new Error("worker read failed");

    expect(
      resourceStateForQueries(
        observedQuery("first", { data: {}, error: null, isFetching: false }, reads),
        observedQuery("second", { data: {}, error: failure, isFetching: false }, reads)
      )
    ).toEqual({ error: failure, loading: false, hasData: true });
  });
});
