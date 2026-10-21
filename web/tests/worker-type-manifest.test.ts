/** Asking for one Worker type's manifest instead of all fourteen.
 *
 * The Ticket page and the review proposal card draw the stages of exactly one Worker
 * type, and used to read the whole list to find it: 21,544 bytes for fourteen types
 * against 1,865 for the one, on both screens and again on every reconnect.
 *
 * That moves one piece of meaning. "This Worker type is not installed" used to be an
 * absence from a list that had loaded; it is now the canonical not-found from a read of
 * that one type. A screen that treated it as an ordinary failure would replace a precise
 * sentence about an unknown Worker type with a generic one, so the translation is load
 * bearing and nothing else covers it.
 */

import { describe, expect, it } from "vitest";

import { PlannerFetchError } from "../src/lib/api";
import { manifestWorkerTypeIsUnknown } from "../src/lib/queryCatalogue";

describe("a single Worker type manifest that is not there", () => {
  it("reads an absent Worker type as absent, by status or by code", () => {
    expect(
      manifestWorkerTypeIsUnknown(
        new PlannerFetchError("no such worker type", "not_found", { status: 404 })
      )
    ).toBe(true);
    // The canonical envelope carries the code; a bare HTTP 404 carries only the status.
    expect(
      manifestWorkerTypeIsUnknown(new PlannerFetchError("HTTP 404", "http_error", { status: 404 }))
    ).toBe(true);
    expect(
      manifestWorkerTypeIsUnknown(new PlannerFetchError("gone", "not_found", {}))
    ).toBe(true);
  });

  it("does not read a broken read as an absent Worker type", () => {
    // These are the cases where the screen must say something went wrong rather than
    // claiming the Ticket's Worker type is not installed.
    expect(
      manifestWorkerTypeIsUnknown(new PlannerFetchError("HTTP 500", "http_error", { status: 500 }))
    ).toBe(false);
    expect(
      manifestWorkerTypeIsUnknown(
        new PlannerFetchError("not permitted", "authorization", { status: 403 })
      )
    ).toBe(false);
    expect(manifestWorkerTypeIsUnknown(new Error("network down"))).toBe(false);
    expect(manifestWorkerTypeIsUnknown(null)).toBe(false);
    expect(manifestWorkerTypeIsUnknown(undefined)).toBe(false);
  });
});
