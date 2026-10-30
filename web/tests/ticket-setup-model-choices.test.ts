import { expect, it, vi } from "vitest";
import { createTicketSetupModelChoices, initialTicketSetupChoicesState } from "../src/lib/conversation/ticketSetupModelChoices";
import type { DestinationModelChoicesResponse, TicketModelChoicesRequest } from "../src/lib/conversation/modelResolutionWire";
import { pickerRows, pickerValidation } from "./fixtures/modelPickerFacts";

const ticket = {
  id: "t_setup", record_sha256: "record-1", locale: "macos" as const,
  employee_backend: "claude", employee_launch_model: "opus", employee_launch_reasoning_effort: "high"
};
const answer = (): DestinationModelChoicesResponse => ({validation: pickerValidation, rows: [...pickerRows]});
const settle = async () => { await Promise.resolve(); await Promise.resolve(); };
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}

it("discards old destination replies and repeats the exact proposed scope without writing", async () => {
  const pending: ReturnType<typeof deferred<DestinationModelChoicesResponse>>[] = [];
  const read = vi.fn((_request: TicketModelChoicesRequest) => {
    const next = deferred<DestinationModelChoicesResponse>(); pending.push(next); return next.promise;
  });
  const save = vi.fn(async () => {}), move = vi.fn(async () => {});
  let state = initialTicketSetupChoicesState();
  const controller = createTicketSetupModelChoices({read, save, move, onChange: value => {state = value;}});
  controller.sync(ticket);
  const preview = controller.previewBackend("codex");
  pending[0].resolve(answer()); await settle();
  expect(state.response).toBeNull();
  const proposed = {...answer(), validation: {...pickerValidation, selection: {...pickerValidation.selection, backend_key: "codex" as const}}};
  pending[1].resolve(proposed); await preview;
  expect(state.response).toBe(proposed);
  const retry = controller.retry();
  expect(read.mock.calls[2][0]).toEqual({ticket_id: ticket.id, employee_backend: "codex"});
  expect(read.mock.calls[2][0]).not.toHaveProperty("employee_launch_reasoning_effort");
  controller.sync({...ticket, record_sha256: "record-2", locale: "vps"});
  pending[2].resolve(proposed); await retry;
  expect(state.response).toBeNull();
  expect(save).not.toHaveBeenCalled(); expect(move).not.toHaveBeenCalled();
  controller.dispose(); pending[3].resolve(answer()); await settle();
  expect(state.response).toBeNull();
});

it("commits deliberate same-token kind changes and explicit default effort without choosing backend defaults", async () => {
  const response = answer();
  const changed = {...pickerRows[0], selection_kind: "concrete" as const};
  response.rows = [changed];
  const read = vi.fn(async () => response), save = vi.fn(async () => {}), move = vi.fn(async () => {});
  const controller = createTicketSetupModelChoices({read, save, move, onChange: () => {}});
  controller.sync(ticket); await settle();
  await controller.chooseModel(changed, null);
  expect(save).toHaveBeenCalledExactlyOnceWith(ticket.id, {
    employee_backend: "claude", employee_launch_model: "opus",
    employee_launch_reasoning_effort: null, selection_kind: "concrete"
  });
  controller.dispose();
});

it("keeps unknown effort intent and uses captured move preconditions without optimistic destination changes", async () => {
  const response = answer();
  const moveResult = deferred<void>();
  const read = vi.fn(async () => response), save = vi.fn(async () => {});
  const move = vi.fn(() => moveResult.promise);
  let state = initialTicketSetupChoicesState();
  const controller = createTicketSetupModelChoices({read, save, move, onChange: value => {state = value;}});
  controller.sync(ticket); await settle();
  await controller.chooseModel(response.rows[0], "high");
  expect(save.mock.calls[0]).toEqual([ticket.id, {
    employee_backend: "claude", employee_launch_model: "opus",
    employee_launch_reasoning_effort: "high", selection_kind: "provider_alias"
  }]);
  const moving = controller.chooseDestination("vps");
  expect(move).toHaveBeenCalledExactlyOnceWith(ticket.id, "vps", "record-1");
  expect(state.response?.validation.selection.destination).toBe("macos");
  controller.sync({...ticket, id: "t_new", record_sha256: "new-record"}); await settle();
  const calls = read.mock.calls.length;
  moveResult.resolve(); await moving;
  expect(read.mock.calls).toHaveLength(calls);
  controller.dispose();
});


it("saves exact custom intent despite a failed catalog read", async () => {
  const read = vi.fn(async () => { throw new Error("Destination offline"); });
  const save = vi.fn(async () => {});
  const controller = createTicketSetupModelChoices({read, save, move: vi.fn(async () => {}), onChange: () => {}});
  controller.sync(ticket); await settle();
  await controller.chooseCustomModel("provider-next[1m]", "future-effort");
  expect(save).toHaveBeenLastCalledWith(ticket.id, {
    employee_backend: "claude", employee_launch_model: "provider-next[1m]",
    employee_launch_reasoning_effort: "future-effort", selection_kind: "legacy_unclassified"
  });
  await controller.chooseCustomModel("opus", null);
  expect(save).toHaveBeenLastCalledWith(ticket.id, {
    employee_backend: "claude", employee_launch_model: "opus",
    employee_launch_reasoning_effort: null, selection_kind: "legacy_unclassified"
  });
  controller.dispose();
});
