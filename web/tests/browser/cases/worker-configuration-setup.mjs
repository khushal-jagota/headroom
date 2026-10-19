import { writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { prepareConfigurationGaps } from './configuration-gaps.mjs';

export async function prepare({ scratchRoot }) {
  const root = fileURLToPath(new URL("../../../", import.meta.url));
  const webRoot = root;
  const scratch = scratchRoot;
const repositoryRoot = join(webRoot, "..");
const hostPath = join(scratchRoot, `model-picker-host-${process.pid}.svelte`);
const mainPath = join(scratchRoot, `model-picker-main-${process.pid}.ts`);
const indexPath = join(scratchRoot, `model-picker-index-${process.pid}.html`);


  await writeFile(hostPath, `
<script lang="ts">
  import WorkerConfigurationSetup from "../src/components/WorkerConfigurationSetup.svelte";
  import ManagedLaunchDefaults from "../src/components/ManagedLaunchDefaults.svelte";
  import type { EmployeeConfigurationSnapshot, TicketDetail } from "../src/lib/types";

  const requests: Array<{
    input: RequestInfo | URL;
    init?: RequestInit;
    resolve: (response: Response) => void;
    reject: (reason: Error) => void;
  }> = [];
  globalThis.fetch = ((input, init) => new Promise<Response>((resolve, reject) => {
    requests.push({ input, init, resolve, reject });
  })) as typeof fetch;
  const response = (payload: unknown) => ({
    ok: true,
    status: 200,
    text: async () => JSON.stringify(payload)
  }) as Response;

  let ticket = $state<EmployeeConfigurationSnapshot>({
    employee_backend: "codex",
    employee_launch_model: "codex-gone",
    employee_launch_reasoning_effort: "extreme"
  });
  let defaults = $state<EmployeeConfigurationSnapshot>({ ...ticket });
  let showDefaults = $state(false);
  let rejectNextTicketSave = false;
  let holdNextTicketSave = false;
  let releaseTicketSave: (() => void) | null = null;
  let holdNextDefaultSave = false;
  let rejectNextDefaultSave = false;
  let releaseDefaultSave: (() => void) | null = null;
  const ticketSaves: EmployeeConfigurationSnapshot[] = [];
  const defaultSaves: EmployeeConfigurationSnapshot[] = [];

  async function saveTicket(next: EmployeeConfigurationSnapshot): Promise<TicketDetail> {
    ticketSaves.push(structuredClone(next));
    if (rejectNextTicketSave) {
      rejectNextTicketSave = false;
      throw new Error("Ticket save was refused.");
    }
    if (holdNextTicketSave) {
      holdNextTicketSave = false;
      await new Promise<void>((resolve) => (releaseTicketSave = resolve));
      releaseTicketSave = null;
    }
    ticket = { ...next };
    return { ...next } as TicketDetail;
  }
  async function saveDefaults(next: EmployeeConfigurationSnapshot): Promise<EmployeeConfigurationSnapshot> {
    defaultSaves.push(structuredClone(next));
    if (rejectNextDefaultSave) {
      rejectNextDefaultSave = false;
      throw new Error("Default save was refused.");
    }
    if (holdNextDefaultSave) {
      holdNextDefaultSave = false;
      await new Promise<void>((resolve) => (releaseDefaultSave = resolve));
      releaseDefaultSave = null;
    }
    defaults = { ...next };
    return defaults;
  }

  (window as any).__respond = (index: number, payload: unknown) => requests[index]?.resolve(response(payload));
  (window as any).__reject = (index: number) => requests[index]?.reject(new Error("transport stopped"));
  (window as any).__request = (index: number) => ({
    url: String(requests[index]?.input),
    method: requests[index]?.init?.method ?? "GET"
  });
  (window as any).__requestCount = () => requests.length;
  (window as any).__ticketSaves = () => ticketSaves;
  (window as any).__defaultSaves = () => defaultSaves;
  (window as any).__showDefaults = () => (showDefaults = true);
  (window as any).__hideDefaults = () => (showDefaults = false);
  (window as any).__rejectNextDefaultSave = () => (rejectNextDefaultSave = true);
  (window as any).__rejectNextTicketSave = () => (rejectNextTicketSave = true);
  (window as any).__holdNextTicketSave = () => (holdNextTicketSave = true);
  (window as any).__releaseTicketSave = () => releaseTicketSave?.();
  (window as any).__holdNextDefaultSave = () => (holdNextDefaultSave = true);
  (window as any).__releaseDefaultSave = () => releaseDefaultSave?.();
</script>

<WorkerConfigurationSetup
  ticketId="ticket-ui"
  employeeBackend={ticket.employee_backend}
  employeeLaunchModel={ticket.employee_launch_model}
  employeeLaunchReasoningEffort={ticket.employee_launch_reasoning_effort}
  onSave={saveTicket}
/>
{#if showDefaults}
  <ManagedLaunchDefaults label="Coding" value={defaults} onSave={saveDefaults} />
{/if}
`, "utf8");
  await writeFile(mainPath, `
import { mount } from "svelte";
import Host from "./${hostPath.split("/").at(-1)}";
import "../../assets/tokens.css";
import "../../assets/app.css";
mount(Host, { target: document.getElementById("app")! });
`, "utf8");
  await writeFile(indexPath, `<!doctype html><html><body><div id="app"></div><script type="module" src="./${mainPath.split("/").at(-1)}"></script></body></html>`, "utf8");

  const gaps = await prepareConfigurationGaps(scratchRoot);
  return {inputs: [indexPath, gaps], arguments: [indexPath, gaps], python: fileURLToPath(new URL("./worker-configuration-setup.py", import.meta.url))};
}
