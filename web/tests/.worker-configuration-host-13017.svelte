
<script lang="ts">
  import WorkerConfigurationSetup from "../src/components/WorkerConfigurationSetup.svelte";
  import ManagedLaunchDefaults from "../src/components/ManagedLaunchDefaults.svelte";
  import type {
    EmployeeConfigurationSnapshot,
    TicketDetail
  } from "../src/lib/types";

  type PendingRequest = {
    url: string;
    aborted: boolean;
    settled: boolean;
    resolve: (response: Response) => void;
    reject: (error: unknown) => void;
  };

  let editable = $state(true);
  let saved = $state<EmployeeConfigurationSnapshot>({
    employee_backend: "hermes",
    employee_launch_model: null,
    employee_launch_reasoning_effort: null
  });
  const saveCalls: EmployeeConfigurationSnapshot[] = [];
  const requests: PendingRequest[] = [];

  globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    return new Promise<Response>((resolve, reject) => {
      const request: PendingRequest = { url, aborted: false, settled: false, resolve, reject };
      requests.push(request);
      init?.signal?.addEventListener("abort", () => {
        request.aborted = true;
        if (!request.settled) {
          request.settled = true;
          reject(new DOMException("aborted", "AbortError"));
        }
      }, { once: true });
    });
  }) as typeof fetch;

  function response(status: number, payload: unknown): Response {
    return {
      ok: status >= 200 && status < 300,
      status,
      text: async () => JSON.stringify(payload)
    } as Response;
  }

  async function onSave(configuration: EmployeeConfigurationSnapshot): Promise<TicketDetail> {
    saveCalls.push(structuredClone(configuration));
    saved = { ...configuration };
    return {
      employee_backend: saved.employee_backend,
      employee_launch_model: saved.employee_launch_model,
      employee_launch_reasoning_effort: saved.employee_launch_reasoning_effort
    } as TicketDetail;
  }

  (window as any).__requests = () => requests.map((request) => ({
    url: request.url,
    aborted: request.aborted,
    settled: request.settled
  }));
  (window as any).__respond = (index: number, payload: unknown) => {
    const request = requests[index];
    if (!request || request.settled) return;
    request.settled = true;
    request.resolve(response(200, payload));
  };
  (window as any).__fail = (index: number) => {
    const request = requests[index];
    if (!request || request.settled) return;
    request.settled = true;
    request.resolve(response(503, {
      error: { code: "catalog_unavailable", message: "catalog unavailable" }
    }));
  };
  (window as any).__saveCalls = () => saveCalls;
  (window as any).__freeze = () => (editable = false);

  let showLaunchDefaults = $state(false);
  let launchDefaults = $state<EmployeeConfigurationSnapshot>({
    employee_backend: "codex",
    employee_launch_model: null,
    employee_launch_reasoning_effort: null
  });
  const launchDefaultsSaveCalls: EmployeeConfigurationSnapshot[] = [];

  async function onLaunchDefaultsSave(
    next: EmployeeConfigurationSnapshot
  ): Promise<EmployeeConfigurationSnapshot> {
    launchDefaultsSaveCalls.push(structuredClone(next));
    launchDefaults = { ...next };
    return launchDefaults;
  }

  (window as any).__showLaunchDefaults = () => (showLaunchDefaults = true);
  (window as any).__launchDefaultsSaveCalls = () => launchDefaultsSaveCalls;
  (window as any).__setLaunchDefaults = (next: EmployeeConfigurationSnapshot) =>
    (launchDefaults = next);
</script>

{#if editable}
  <WorkerConfigurationSetup
    ticketId="ticket-ui"
    employeeBackend={saved.employee_backend}
    employeeLaunchModel={saved.employee_launch_model}
    employeeLaunchReasoningEffort={saved.employee_launch_reasoning_effort}
    {onSave}
  />
{/if}

{#if showLaunchDefaults}
  <ManagedLaunchDefaults
    label="Coding"
    value={launchDefaults}
    onSave={onLaunchDefaultsSave}
  />
{/if}
