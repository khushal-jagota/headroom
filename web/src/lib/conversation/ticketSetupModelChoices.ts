import type { EmployeeConfigurationSnapshot, TicketDetail } from "../types";
import type { TicketLocale } from "../ticketMachines";
import type { ConversationBackendKey } from "./wire";
import type { DestinationModelChoicesResponse, PickerRow, SelectionKind, TicketModelChoicesRequest } from "./modelResolutionWire";

export type SetupConfigurationWrite = EmployeeConfigurationSnapshot & { selection_kind: SelectionKind };
type SetupTicket = Pick<TicketDetail, "id" | "record_sha256" | "locale" | "launch_validation" | "employee_backend" | "employee_launch_model" | "employee_launch_reasoning_effort">;
export type TicketSetupChoicesState = {
  response: DestinationModelChoicesResponse | null;
  request: TicketModelChoicesRequest | null;
  loading: boolean;
  saving: boolean;
  error: unknown;
  saveError: unknown;
};
export const initialTicketSetupChoicesState = (): TicketSetupChoicesState => ({
  response: null, request: null, loading: false, saving: false, error: null, saveError: null
});

/** Advisory reads never write settings. Only an explicit row choice or destination action writes. */
export function createTicketSetupModelChoices(dependencies: {
  read: (request: TicketModelChoicesRequest) => Promise<DestinationModelChoicesResponse>;
  save: (ticketId: string, configuration: SetupConfigurationWrite) => Promise<unknown>;
  move: (ticketId: string, locale: TicketLocale, expectedRecord: string) => Promise<unknown>;
  onChange: (state: TicketSetupChoicesState) => void;
}) {
  let state = initialTicketSetupChoicesState();
  let current: SetupTicket | null = null;
  let signature = "";
  let generation = 0;
  let disposed = false;
  const publish = (patch: Partial<TicketSetupChoicesState>) => {
    if (disposed) return;
    state = { ...state, ...patch };
    dependencies.onChange(state);
  };
  async function read(request: TicketModelChoicesRequest): Promise<void> {
    if (disposed || !current) return;
    const captured = { ...request };
    const source = signature;
    const attempt = ++generation;
    publish({ request: captured, response: null, loading: true, error: null });
    try {
      const response = await dependencies.read(captured);
      if (!disposed && generation === attempt && signature === source) publish({ response });
    } catch (error) {
      if (!disposed && generation === attempt && signature === source) publish({ error });
    } finally {
      if (!disposed && generation === attempt && signature === source) publish({ loading: false });
    }
  }
  async function mutate(action: () => Promise<unknown>): Promise<void> {
    if (disposed || state.saving || !current) return;
    const source = signature;
    ++generation;
    publish({ saving: true, loading: false, saveError: null });
    try {
      await action();
      // A navigation refresh may already have installed a newer canonical record.
      if (!disposed && source === signature && current) await read({ ticket_id: current.id });
    } catch (saveError) {
      if (!disposed && source === signature) publish({ saveError });
    } finally {
      if (!disposed) publish({ saving: false });
    }
  }
  return {
    sync(ticket: SetupTicket | null): void {
      const next = ticket ? JSON.stringify([ticket.id, ticket.record_sha256, ticket.locale,
        ticket.employee_backend, ticket.employee_launch_model, ticket.employee_launch_reasoning_effort, ticket.launch_validation]) : "";
      if (disposed || next === signature) return;
      signature = next;
      current = ticket;
      ++generation;
      publish({ response: null, request: null, loading: false, error: null, saveError: null });
      if (ticket) void read({ ticket_id: ticket.id });
    },
    retry(): Promise<void> {
      return state.request ? read(state.request) : Promise.resolve();
    },
    previewBackend(backend: ConversationBackendKey): Promise<void> {
      if (!current || state.saving) return Promise.resolve();
      // Omitted model/effort preserve exact saved intent; no backend default is selected.
      return read({ ticket_id: current.id, employee_backend: backend });
    },
    chooseModel(row: PickerRow, effort: string | null): Promise<void> {
      if (!current || state.loading || !state.response?.rows.includes(row)) return Promise.resolve();
      const ticketId = current.id;
      const backend = state.response.validation.selection.backend_key;
      return mutate(() => dependencies.save(ticketId, {
        employee_backend: backend,
        employee_launch_model: row.submitted_token,
        employee_launch_reasoning_effort: effort,
        selection_kind: row.selection_kind
      }));
    },
    chooseCustomModel(model: string, effort: string | null): Promise<void> {
      if (!current || !model.trim()) return Promise.resolve();
      const ticketId = current.id;
      const backend = state.request?.employee_backend ?? current.employee_backend;
      return mutate(() => dependencies.save(ticketId, {
        employee_backend: backend,
        employee_launch_model: model,
        employee_launch_reasoning_effort: effort,
        selection_kind: "legacy_unclassified"
      }));
    },
    chooseDestination(locale: TicketLocale): Promise<void> {
      if (!current || locale === current.locale) return Promise.resolve();
      const captured = current;
      if (!captured.record_sha256) {
        publish({ saveError: new Error("Read the current Ticket before moving it.") });
        return Promise.resolve();
      }
      const expectedRecord = captured.record_sha256;
      return mutate(() => dependencies.move(captured.id, locale, expectedRecord));
    },
    dispose(): void { disposed = true; ++generation; }
  };
}
