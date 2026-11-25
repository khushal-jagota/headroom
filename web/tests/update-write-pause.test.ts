import { afterEach, describe, expect, it, vi } from "vitest";
import { get } from "svelte/store";
import { createUpdateWritePause, updateRefusal } from "../src/lib/updates/writePause";
import type { UpdateStatus } from "../src/lib/updates/contracts";

vi.mock("../src/lib/updates/handoff", () => ({
  isUpdateScreenReplacement: vi.fn(() => false),
  retainRefusedWrite: vi.fn(async () => "retained-action"),
  settleRefusedWrite: vi.fn(async () => {})
}));

function status(update: Partial<UpdateStatus> = {}): UpdateStatus {
  return { writes_admitted: true, app_sha: "old", attempt: null, ...update } as UpdateStatus;
}

describe("update write admission", () => {
  it("holds through preparation, a completed trial behind the barrier, and old screens", async () => {
    const pause = createUpdateWritePause();
    pause.observe(status({ attempt: { state: "preparing" } as UpdateStatus["attempt"] }), "old");
    const resumed = vi.fn();
    const waiting = pause.wait().then(resumed);
    pause.observe(status({ writes_admitted: false, attempt: { state: "done" } as UpdateStatus["attempt"] }), "old");
    await Promise.resolve();
    expect(resumed).not.toHaveBeenCalled();
    pause.observe(status({ app_sha: "new" }), "old");
    expect(get(pause.state)).toEqual({ blocked: true, openingScreens: true });
    pause.observe(status({ app_sha: "new" }), "new");
    await waiting;
    expect(resumed).toHaveBeenCalledOnce();
  });

  it("retains the pause across a lost status and releases after server rollback", async () => {
    const pause = createUpdateWritePause();
    pause.refuse("update_in_progress");
    pause.observe(undefined, "old");
    expect(get(pause.state).blocked).toBe(true);
    const controller = new AbortController();
    const waiting = pause.wait(controller.signal);
    controller.abort(new Error("cancelled"));
    await expect(waiting).rejects.toThrow("cancelled");
    pause.observe(status({ attempt: { state: "failed" } as UpdateStatus["attempt"] }), "old");
    expect(get(pause.state).blocked).toBe(false);
  });

  it("requires explicit middleware proof rather than replaying domain or unrelated refusals", async () => {
    const response = (code: string, status: number, proved = false) => new Response(JSON.stringify({ error: { code } }), {
      status, headers: proved ? { "X-Panels-Write-Admitted": "false" } : {}
    });
    expect(await updateRefusal(response("update_in_progress", 503))).toBeNull();
    expect(await updateRefusal(response("validation", 409, true))).toBeNull();
    expect(await updateRefusal(response("screens_outdated", 409, true))).toBe("screens_outdated");
    expect(await updateRefusal(response("update_in_progress", 503, true))).toBe("update_in_progress");
  });
});

it("retains an upload refused at cutover and resumes it only after admission, without replaying an uncertain result", async () => {
  const { clientFetch } = await import("../src/lib/clientIdentity");
  const { observeUpdateAdmission, updatePause } = await import("../src/lib/updates/writePause");
  const { retainRefusedWrite, settleRefusedWrite } = await import("../src/lib/updates/handoff");
  observeUpdateAdmission(status(), "old");
  vi.stubGlobal("document", { querySelector: () => ({ content: "old" }) });
  const body = new Blob(["file data"]);
  const request = vi.fn<typeof fetch>()
    .mockResolvedValueOnce(new Response(JSON.stringify({ error: { code: "update_in_progress" } }), { status: 503, headers: { "X-Panels-Write-Admitted": "false" } }))
    .mockRejectedValueOnce(new Error("connection lost"));
  vi.stubGlobal("fetch", request);
  const pending = clientFetch("/api/uploads", { method: "POST", body });
  await vi.waitFor(() => expect(get(updatePause).blocked).toBe(true));
  expect(request).toHaveBeenCalledTimes(1);
  expect(retainRefusedWrite).toHaveBeenCalledWith("/api/uploads", expect.objectContaining({ body }));
  observeUpdateAdmission(status(), "old");
  await expect(pending).rejects.toThrow("connection lost");
  expect(request).toHaveBeenCalledTimes(2);
  expect(request.mock.calls[1][1]?.body).toBe(body);
  expect(settleRefusedWrite).toHaveBeenCalledWith("retained-action");
});

afterEach(() => vi.unstubAllGlobals());

// Progress uploads use XHR rather than fetch. The existing composer tests mock this adapter.
it("keeps the progress-upload transport behind the same admission and explicit-refusal boundary", async () => {
  const { videoUploadAdapter } = await import("../src/lib/conversation/videoUploads/adapter");
  const { observeUpdateAdmission, updatePause } = await import("../src/lib/updates/writePause");
  observeUpdateAdmission(status(), "old");
  vi.stubGlobal("document", { querySelector: () => ({ content: "old" }) });
  const sent: Blob[] = [];
  class UploadRequest {
    upload = {};
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    onabort: (() => void) | null = null;
    status = 200;
    responseText = "{}";
    open() {}
    setRequestHeader() {}
    getResponseHeader() { return this.status === 503 ? "false" : null; }
    abort() { this.onabort?.(); }
    send(file: Blob) {
      sent.push(file);
      this.status = sent.length === 1 ? 503 : 200;
      this.responseText = JSON.stringify(this.status === 503 ? { error: { code: "update_in_progress" } } : { upload_id: "upload" });
      this.onload?.();
    }
  }
  vi.stubGlobal("XMLHttpRequest", UploadRequest);
  const file = new File(["upload bytes"], "attachment.bin");
  const pending = videoUploadAdapter.transfer("upload", file, new AbortController().signal, vi.fn());
  await vi.waitFor(() => expect(get(updatePause).blocked).toBe(true));
  expect(sent).toEqual([file]);
  observeUpdateAdmission(status(), "old");
  await expect(pending).resolves.toMatchObject({ upload_id: "upload" });
  expect(sent).toEqual([file, file]);
});

it("keeps a refused action in the handoff when renderer replacement aborts its caller", async () => {
  const { clientFetch } = await import("../src/lib/clientIdentity");
  const { noteUpdateRefusal, observeUpdateAdmission } = await import("../src/lib/updates/writePause");
  const { isUpdateScreenReplacement, retainRefusedWrite, settleRefusedWrite } = await import("../src/lib/updates/handoff");
  vi.mocked(isUpdateScreenReplacement).mockReturnValue(true);
  noteUpdateRefusal("screens_outdated");
  const request = vi.fn<typeof fetch>();
  vi.stubGlobal("fetch", request);
  const controller = new AbortController();
  const pending = clientFetch("/api/example", { method: "POST", body: "kept action", signal: controller.signal });
  await vi.waitFor(() => expect(retainRefusedWrite).toHaveBeenCalled());
  controller.abort(new Error("renderer replaced"));
  await expect(pending).rejects.toThrow("renderer replaced");
  expect(request).not.toHaveBeenCalled();
  expect(settleRefusedWrite).not.toHaveBeenCalled();
  vi.mocked(isUpdateScreenReplacement).mockReturnValue(false);
  observeUpdateAdmission(status(), "old");
});
