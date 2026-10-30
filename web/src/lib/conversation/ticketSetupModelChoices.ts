import type { EmployeeConfigurationSnapshot, TicketDetail } from "../types";
import type { BackendSnapshot, ConversationBackendKey, ConversationStartValues } from "./wire";
import type { SelectionKind, TicketBackendScope } from "./modelResolutionWire";

export type SetupConfigurationWrite = EmployeeConfigurationSnapshot & { selection_kind: SelectionKind };
type SetupTicket = Pick<TicketDetail, "id" | "record_sha256" | "locale" | "launch_validation" | "employee_backend" | "employee_launch_model" | "employee_launch_reasoning_effort">;
export type TicketSetupChoicesState = {
  response: readonly BackendSnapshot[] | null;
  intent: SetupConfigurationWrite | null;
  request: TicketBackendScope | null;
  loading: boolean;
  saving: boolean;
  error: unknown;
  saveError: unknown;
};
export const initialTicketSetupChoicesState = (): TicketSetupChoicesState => ({
  response: null, intent: null, request: null, loading: false, saving: false, error: null, saveError: null
});

/** Advisory reads never write settings. Only an explicit row choice or destination action writes. */
export function createTicketSetupModelChoices(dependencies: {
  read: (request: TicketBackendScope, refresh?: boolean) => Promise<readonly BackendSnapshot[]>;
  save: (ticketId: string, configuration: SetupConfigurationWrite) => Promise<unknown>;
  onChange: (state: TicketSetupChoicesState) => void;
}) {
  let state = initialTicketSetupChoicesState();
  let current: SetupTicket | null = null;
  let signature = "";
  let scopeKey = "";
  let generation = 0;
  let disposed = false;
  const intents = new Map<string, SetupConfigurationWrite>();
  const publish = (patch: Partial<TicketSetupChoicesState>) => {
    if (disposed) return;
    state = { ...state, ...patch };
    dependencies.onChange(state);
  };
  async function read(request: TicketBackendScope, refresh = false): Promise<void> {
    if (disposed || !current) return;
    const captured = { ...request };
    const source = scopeKey;
    const attempt = ++generation;
    publish({ request: captured, loading: true, error: null });
    try {
      const response = await dependencies.read(captured, refresh);
      if (!disposed && generation === attempt && scopeKey === source) publish({ response });
    } catch (error) {
      if (!disposed && generation === attempt && scopeKey === source) publish({ error });
    } finally {
      if (!disposed && generation === attempt && scopeKey === source) publish({ loading: false });
    }
  }
  async function mutate(action: () => Promise<unknown>, intent?: SetupConfigurationWrite): Promise<void> {
    if (disposed || state.saving || !current) return;
    const source = scopeKey;
    const previousIntent = state.intent;
    publish({ saving: true, saveError: null, ...(intent ? {intent} : {}) });
    try {
      await action();
      if (!disposed && source === scopeKey && intent) intents.set(intent.employee_backend, intent);
    } catch (saveError) {
      if (!disposed && source === scopeKey) publish({ saveError, intent: previousIntent });
    } finally {
      if (!disposed && source === scopeKey) publish({ saving: false });
    }
  }
  return {
    sync(ticket: SetupTicket | null, workspace?: Pick<ConversationStartValues, "workspace_folder" | "workspace_path_policy">): void {
      const nextScope = JSON.stringify([ticket?.id, ticket?.locale, workspace?.workspace_folder, workspace?.workspace_path_policy]);
      const next = ticket ? JSON.stringify([ticket.id, ticket.record_sha256, ticket.locale,
        ticket.employee_backend, ticket.employee_launch_model, ticket.employee_launch_reasoning_effort, ticket.launch_validation, nextScope]) : "";
      if (disposed || next === signature) return;
      const configurationChanged = current?.id !== ticket?.id || current?.employee_backend !== ticket?.employee_backend
        || current?.employee_launch_model !== ticket?.employee_launch_model
        || current?.employee_launch_reasoning_effort !== ticket?.employee_launch_reasoning_effort;
      const scopeChanged = scopeKey !== nextScope;
      scopeKey = nextScope;
      if (scopeChanged) intents.clear();
      signature = next;
      current = ticket;
      if (scopeChanged) ++generation;
      publish({ ...(scopeChanged ? {response: null, request: null, saving: false, loading: false, error: null} : {}), saveError: null, ...(configurationChanged ? {intent: null} : {}) });
      if (ticket && scopeChanged && workspace?.workspace_folder !== undefined) void read({ ticket_id: ticket.id });
    },
    retry(): Promise<void> {
      return state.request ? read(state.request, true) : Promise.resolve();
    },
    previewBackend(backend: ConversationBackendKey): Promise<void> {
      if (!current || state.saving) return Promise.resolve();
      if (state.intent) intents.set(state.intent.employee_backend, state.intent);
      else intents.set(current.employee_backend, {
        employee_backend: current.employee_backend, employee_launch_model: current.employee_launch_model,
        employee_launch_reasoning_effort: current.employee_launch_reasoning_effort,
        selection_kind: "legacy_unclassified"
      });
      const intent = intents.get(backend) ?? {
        employee_backend: backend, employee_launch_model: null,
        employee_launch_reasoning_effort: null, selection_kind: "legacy_unclassified" as const
      };
      publish({request: {ticket_id: current.id, employee_backend: backend}, intent});
      return Promise.resolve();
    },
    chooseCustomModel(model: string, effort: string | null): Promise<void> {
      if (!current || !model.trim()) return Promise.resolve();
      const ticketId = current.id;
      const backend = state.request?.employee_backend ?? current.employee_backend;
      const intent: SetupConfigurationWrite = {
        employee_backend: backend, employee_launch_model: model,
        employee_launch_reasoning_effort: effort, selection_kind: "legacy_unclassified"
      };
      return mutate(() => dependencies.save(ticketId, intent), intent);
    },
    dispose(): void { disposed = true; ++generation; }
  };
}
