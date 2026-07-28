import { beforeEach, describe, expect, it, vi } from "vitest";
import type { FetchOptions } from "../src/lib/api";

const fakes = vi.hoisted(() => ({
  fetchJson: vi.fn<(path: string, options?: FetchOptions) => Promise<unknown>>(),
  invalidateQueries: vi.fn<() => Promise<void>>()
}));

vi.mock("../src/lib/api", () => ({
  fetchJson: fakes.fetchJson
}));

vi.mock("../src/lib/queryClient", () => ({
  queryClient: {
    invalidateQueries: fakes.invalidateQueries
  }
}));

import { mutateJson } from "../src/lib/mutate";

describe("mutateJson", () => {
  beforeEach(() => {
    fakes.fetchJson.mockReset();
    fakes.fetchJson.mockResolvedValue({});
    fakes.invalidateQueries.mockReset();
    fakes.invalidateQueries.mockResolvedValue();
  });

  it("returns a successful write and invalidates cached reads afterwards", async () => {
    const written = { id: "t_written" };
    fakes.fetchJson.mockResolvedValue(written);

    await expect(
      mutateJson("/api/tickets/t_written", {
        method: "PATCH",
        body: { title: "x" }
      })
    ).resolves.toEqual(written);

    expect(fakes.fetchJson).toHaveBeenCalledWith("/api/tickets/t_written", {
      method: "PATCH",
      body: { title: "x" }
    });
    expect(fakes.invalidateQueries).toHaveBeenCalledTimes(1);
    expect(fakes.fetchJson.mock.invocationCallOrder[0]).toBeLessThan(
      fakes.invalidateQueries.mock.invocationCallOrder[0]
    );
  });

  it("forwards omitted options as an empty object and still invalidates", async () => {
    await mutateJson("/api/tickets/t_written/conversation");

    expect(fakes.fetchJson).toHaveBeenCalledWith(
      "/api/tickets/t_written/conversation",
      {}
    );
    expect(fakes.invalidateQueries).toHaveBeenCalledTimes(1);
  });

  it("rethrows a rejected write without invalidating", async () => {
    const failure = new Error("write rejected");
    fakes.fetchJson.mockRejectedValue(failure);

    await expect(
      mutateJson("/api/tickets/t_failed", { method: "POST" })
    ).rejects.toBe(failure);
    expect(fakes.invalidateQueries).not.toHaveBeenCalled();
  });

  it("does not settle until cache invalidation settles", async () => {
    let releaseInvalidation!: () => void;
    const invalidation = new Promise<void>((resolve) => {
      releaseInvalidation = resolve;
    });
    fakes.invalidateQueries.mockReturnValue(invalidation);

    let settled = false;
    const pending = mutateJson("/api/tickets/t_awaited", { method: "POST" }).then(
      () => {
        settled = true;
      }
    );
    await Promise.resolve();
    await Promise.resolve();

    expect(fakes.invalidateQueries).toHaveBeenCalledTimes(1);
    expect(settled).toBe(false);

    releaseInvalidation();
    await pending;
    expect(settled).toBe(true);
  });
});
