/** One read of a managed file, shared — and let go of the moment it settles.
 *
 * These are the rules the previews depend on. The fetch is stubbed, because what is
 * under test is when a read is started, shared, abandoned and forgotten, not what the
 * network does.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { readManagedFile } from "../src/lib/managedFileRead";

type Gate = { settle: (value: Response) => void; fail: (reason: unknown) => void };

function stubFetch(): { calls: { href: string; signal: AbortSignal }[]; gates: Gate[] } {
  const calls: { href: string; signal: AbortSignal }[] = [];
  const gates: Gate[] = [];
  vi.stubGlobal("fetch", (href: string, init: { signal: AbortSignal }) => {
    calls.push({ href, signal: init.signal });
    return new Promise<Response>((resolve, reject) => {
      gates.push({ settle: resolve, fail: reject });
    });
  });
  return { calls, gates };
}

const answer = (text: string, ok = true): Response =>
  ({ ok, status: ok ? 200 : 500, text: () => Promise.resolve(text) }) as unknown as Response;

const reader = (): AbortController => new AbortController();

describe("reading a managed file", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("reads once for three previews that name the same file", async () => {
    const { calls, gates } = stubFetch();
    const previews = [
      readManagedFile("/files/tickets/t_1/note.md", reader().signal),
      readManagedFile("/files/tickets/t_1/note.md", reader().signal),
      readManagedFile("/files/tickets/t_1/note.md", reader().signal)
    ];

    expect(calls).toHaveLength(1);
    gates[0].settle(answer("the note"));
    expect(await Promise.all(previews)).toEqual(["the note", "the note", "the note"]);
  });

  it("keeps nothing, so a later read sees a rewritten file", async () => {
    const { calls, gates } = stubFetch();
    const first = readManagedFile("/f/a.md", reader().signal);
    gates[0].settle(answer("before the rewrite"));
    expect(await first).toBe("before the rewrite");

    // Nothing was kept: the next reader has to go and ask again.
    const later = readManagedFile("/f/a.md", reader().signal);
    expect(calls).toHaveLength(2);
    gates[1].settle(answer("after the rewrite"));
    expect(await later).toBe("after the rewrite");
  });

  it("lets go after a failure, so a retry is a retry", async () => {
    const { calls, gates } = stubFetch();
    const failed = readManagedFile("/f/b.md", reader().signal);
    gates[0].fail(new Error("the link went down"));
    await expect(failed).rejects.toThrow("the link went down");

    const retried = readManagedFile("/f/b.md", reader().signal);
    expect(calls).toHaveLength(2);
    gates[1].settle(answer("it came back"));
    expect(await retried).toBe("it came back");
  });

  it("reports a refused status as a failure rather than a body", async () => {
    const { gates } = stubFetch();
    const read = readManagedFile("/f/c.md", reader().signal);
    gates[0].settle(answer("", false));
    await expect(read).rejects.toThrow("file fetch failed: 500");
  });

  it("one preview leaving does not take the read away from another", async () => {
    const { calls, gates } = stubFetch();
    const leaving = reader();
    const staying = reader();
    const abandoned = readManagedFile("/f/d.md", leaving.signal);
    const wanted = readManagedFile("/f/d.md", staying.signal);

    leaving.abort();
    expect(calls[0].signal.aborted).toBe(false);
    gates[0].settle(answer("it still arrived"));
    expect(await wanted).toBe("it still arrived");
    await expect(abandoned).resolves.toBe("it still arrived");
  });

  it("abandons the read when the last preview leaves", async () => {
    const { calls } = stubFetch();
    const only = reader();
    const read = readManagedFile("/f/e.md", only.signal);

    only.abort();
    expect(calls[0].signal.aborted).toBe(true);
    void read.catch(() => undefined);
  });

  it("a preview arriving right after the last one left starts a clean read", async () => {
    // The race the old shape would lose: the leaving reader aborts, and the entry is
    // still in the map until its rejection lands. An arrival in that window must not be
    // handed the request that is on its way out.
    const { calls, gates } = stubFetch();
    const leaving = reader();
    const abandoned = readManagedFile("/f/g.md", leaving.signal);
    leaving.abort();

    const arriving = readManagedFile("/f/g.md", reader().signal);
    expect(calls).toHaveLength(2);
    expect(calls[1].signal.aborted).toBe(false);

    gates[0].fail(new DOMException("aborted", "AbortError"));
    gates[1].settle(answer("read for the new preview"));
    expect(await arriving).toBe("read for the new preview");
    void abandoned.catch(() => undefined);
  });

  it("the abandoned read does not evict the one that replaced it", async () => {
    const { gates } = stubFetch();
    const leaving = reader();
    const abandoned = readManagedFile("/f/h.md", leaving.signal);
    leaving.abort();
    const arriving = readManagedFile("/f/h.md", reader().signal);

    gates[0].fail(new DOMException("aborted", "AbortError"));
    await Promise.resolve();
    await Promise.resolve();

    // The read that replaced it is still the one on offer: a third preview joins it
    // rather than starting a fourth request.
    const third = readManagedFile("/f/h.md", reader().signal);
    expect(gates).toHaveLength(2);

    gates[1].settle(answer("still here"));
    expect(await arriving).toBe("still here");
    expect(await third).toBe("still here");
    void abandoned.catch(() => undefined);
  });

  it("unmounting and mounting again reads again", async () => {
    const { calls, gates } = stubFetch();
    const first = reader();
    const read = readManagedFile("/f/i.md", first.signal);
    gates[0].settle(answer("first time"));
    expect(await read).toBe("first time");
    first.abort();

    readManagedFile("/f/i.md", reader().signal);
    expect(calls).toHaveLength(2);
  });

  it("two different files are two reads", async () => {
    const { calls } = stubFetch();
    readManagedFile("/f/one.md", reader().signal);
    readManagedFile("/f/two.md", reader().signal);
    expect(calls.map((call) => call.href)).toEqual(["/f/one.md", "/f/two.md"]);
  });
});
