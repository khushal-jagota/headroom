import { expect, it, vi } from "vitest";
import { createTicketSetupModelChoices, initialTicketSetupChoicesState } from "../src/lib/conversation/ticketSetupModelChoices";
import type { BackendSnapshot } from "../src/lib/conversation/wire";

const ticket = {
  id: "t_setup", record_sha256: "record-1", locale: "macos" as const,
  employee_backend: "claude", employee_launch_model: "opus", employee_launch_reasoning_effort: "high"
};
const workspace = {workspace_folder:"/project", workspace_path_policy:"native_absolute" as const};
const settle = async () => { await Promise.resolve(); await Promise.resolve(); };
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}

it("retains snapshots during refresh and rejects a late response after canonical workspace change", async () => {
  const pending: ReturnType<typeof deferred<readonly BackendSnapshot[]>>[] = [];
  const read = vi.fn(() => { const next = deferred<readonly BackendSnapshot[]>(); pending.push(next); return next.promise; });
  const save = vi.fn(async () => {});
  let state = initialTicketSetupChoicesState();
  const controller = createTicketSetupModelChoices({read, save, onChange: value => {state = value;}});
  controller.sync(ticket, workspace);
  await controller.chooseCustomModel("provider-next", null);
  expect(state.loading).toBe(true);
  save.mockClear();
  const first: BackendSnapshot[] = [];
  pending[0].resolve(first); await settle();
  const refresh = controller.retry();
  expect(state.response).toBe(first);
  expect(read).toHaveBeenLastCalledWith({ticket_id: ticket.id}, true);
  controller.sync(ticket, {...workspace, workspace_folder:"/other-project"});
  pending[1].resolve([]); await refresh;
  expect(state.response).toBeNull();
  expect(save).not.toHaveBeenCalled();
  controller.dispose(); pending[2].resolve([]); await settle();
  expect(state.response).toBeNull();
});

it("keeps each backend's chosen intent through an offline catalog and a refused save", async () => {
  const read = vi.fn(async () => {throw new Error("offline");});
  const save = vi.fn(async () => {});
  let state = initialTicketSetupChoicesState();
  const controller = createTicketSetupModelChoices({read, save, onChange: value => {state = value;}});
  controller.sync(ticket, workspace); await settle();
  await controller.chooseCustomModel("sonnet", null);
  await controller.previewBackend("codex");
  await controller.chooseCustomModel("gpt-6.1-terra", "ultra");
  await controller.previewBackend("claude");
  expect(state.intent).toMatchObject({employee_launch_model: "sonnet", employee_launch_reasoning_effort: null});
  save.mockRejectedValueOnce(new Error("Save failed"));
  await controller.chooseCustomModel("refused", null);
  expect(state.intent).toMatchObject({employee_launch_model: "sonnet", employee_launch_reasoning_effort: null});
  expect(state.saveError).toEqual(new Error("Save failed"));
  await controller.previewBackend("codex");
  expect(state.intent).toMatchObject({employee_launch_model: "gpt-6.1-terra", employee_launch_reasoning_effort: "ultra"});
  expect(read).toHaveBeenCalledTimes(1);
  controller.dispose();
});
