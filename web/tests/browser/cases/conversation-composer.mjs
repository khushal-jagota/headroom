import { writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export async function prepare({ scratchRoot }) {
  const root = fileURLToPath(new URL("../../../", import.meta.url));
  const webRoot = root;
  const scratch = scratchRoot;
const repositoryRoot = join(webRoot, "..");
const hostPath = join(scratchRoot, `composer-redesign-host-${process.pid}.svelte`);
const mainPath = join(scratchRoot, `composer-redesign-main-${process.pid}.ts`);
const indexPath = join(scratchRoot, `composer-redesign-index-${process.pid}.html`);

  await writeFile(
    hostPath,
    String.raw`<script lang="ts">
  import UnifiedModelPicker from "../src/components/conversation/UnifiedModelPicker.svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import { queries } from "../src/lib/queryCatalogue";
  import { queryClient } from "../src/lib/queryClient";
  import WorkerConfigurationSetup from "../src/components/WorkerConfigurationSetup.svelte";
  import { resolveModelPicker } from "../src/lib/conversation/modelPicker";
  const pickerModels = [
    {enabled:true,model_id:"claude-opus-5-5",display_name:"Opus 5.5",selected_variant:"opus",version:[5,5],reasoning_effort_options:["high"]},
    {enabled:true,model_id:"claude-opus-5",display_name:"Opus 5",selected_variant:"opus",version:[5],reasoning_effort_options:["high"]},
    {enabled:true,model_id:"claude-sonnet-5-5",display_name:"Sonnet 5.5",selected_variant:"sonnet",version:[5,5],reasoning_effort_options:[]}
  ];
  const editorProof = new URLSearchParams(location.search).has("editor");
  const workspace = {workspace_folder:"/fixture", workspace_path_policy:"absolute"} as any;
  const shared = createQuery(() => ({...queries.ticketBackends("fixture", ["macos", "/fixture", "absolute"]), enabled:editorProof}), () => queryClient);
  let configuration = $state({employee_backend:"claude", employee_launch_model:"claude-opus-5-5", employee_launch_reasoning_effort:"high"});
  (window as any).__setupSaves = [];
  (window as any).__setupFail = false;
  async function saveSetup(next: any): Promise<any> {
    (window as any).__setupSaves.push(next);
    if ((window as any).__setupFail) throw new Error("Save refused");
    configuration = next;
    return next;
  }
  const sharedView = $derived(resolveModelPicker({backendKey:"claude", model:"claude-opus-5-5", reasoningEffort:"high", backends:shared.data ?? []}));
  const pickerProof = new URLSearchParams(location.search).has("picker");
  const missingRetry = new URLSearchParams(location.search).has("missingRetry");
  let proofModels = $state.raw(pickerModels);
  let proofError = $state<string | null>(null);
  let proofSelection = $state<any>({backendKey:"claude",model:"claude-opus-5-5",reasoningEffort:"high"});
  (window as any).__pickerPending = () => {
    proofSelection = {backendKey:"claude",model:"sonnet",reasoningEffort:null};
    proofModels = [];
  };
  (window as any).__pickerOld = () => {proofSelection = {backendKey:"claude",model:"claude-opus-5",reasoningEffort:"high"};};
  (window as any).__pickerHermes = () => {
    proofSelection = {backendKey:"hermes",model:"hermes-model",reasoningEffort:null};
    proofModels = [{enabled:true,model_id:"hermes-model",display_name:"Hermes model",selected_variant:"hermes",version:[1],reasoning_effort_options:["high"]},
      {enabled:true,model_id:"hermes-old",display_name:"Hermes old",selected_variant:"hermes",version:[0],reasoning_effort_options:["high"]}];
  };
  (window as any).__pickerStale = () => {proofModels = pickerModels;};
  (window as any).__pickerAvailability = () => {proofError = "Claude is not installed on Mac.";};
  const view = $derived(resolveModelPicker({backendKey: proofSelection.backendKey, model: proofSelection.model, reasoningEffort: proofSelection.reasoningEffort, backends: [], models: proofModels}));
  (window as any).__pickerReads = [];
  (window as any).__pickerChoices = [];
  const scopedRead = Object.freeze({ticket: "fixture", destination: "macos", token: "opus", effort: "high"});
  const retry = async () => {(window as any).__pickerReads.push(scopedRead);};
  import ConversationComposer from "../src/components/conversation/ConversationComposer.svelte";
  let running = $state(false);
  let ask = $state<any>(null);
  let heldPromptRows = $state<any[]>([]);
  let errorNote = $state<string | null>(null);
  (window as any).__setState = (state: string) => {
    running = state === "running";
    ask = state === "ask" ? {askId:"permission",title:"Run the migration?",detail:"Copied database only",options:[
      {option_id:"deny",label:"Deny",option_kind:"reject_once"},
      {option_id:"approve",label:"Approve once",option_kind:"allow_once"}]} : null;
    heldPromptRows = state === "held" ? [{key:"held",heldPromptId:"held",content:[{piece:"text",text:"After that, show the diff"}],state:"held",queueReason:null}] : [];
    errorNote = state === "refused" ? "Message refused: connection lost" : null;
  };
  (window as any).__attempts = [];
  async function send(content: any[], mode: string, picked: any): Promise<boolean> {
    (window as any).__attempts.push({content,mode,picked});
    return new Promise(resolve => {
      (window as any).__resolveSend = (accepted: boolean) => {
        errorNote = accepted ? null : "Message refused: connection lost";
        resolve(accepted);
      };
    });
  }
</script>
<main data-conversation-pane style="--conversation-target:32px;--conversation-glyph:22px">
  <div class="thread">The conversation remains above the composer.</div>
  {#if editorProof}
    <WorkerConfigurationSetup ticketId="fixture" destination="macos" {workspace}
      employeeBackend={configuration.employee_backend} employeeLaunchModel={configuration.employee_launch_model}
      employeeLaunchReasoningEffort={configuration.employee_launch_reasoning_effort} onSave={saveSetup} />
    <div data-shared-picker>
      <UnifiedModelPicker view={sharedView} snapshots={shared.data ?? []} models={shared.data?.[0]?.available_models ?? []}
        backendEffortOptions={[]} onChooseBackend={() => {}} onChooseModel={() => {}}
        onRetry={() => queryClient.fetchQuery({...queries.ticketBackends("fixture", ["macos", "/fixture", "absolute"], true), staleTime:0}).then(() => {})} />
    </div>
  {:else if pickerProof}
    <div class="picker-proof">
      <WorkerConfigurationSetup ticketId="fixture" destination="macos" employeeBackend="claude" employeeLaunchModel="opus" employeeLaunchReasoningEffort="high" onSave={async () => { throw new Error("No fixture Ticket writes"); }} />
      <UnifiedModelPicker {view} snapshots={[]} models={proofModels} backendEffortOptions={[]} readError={proofError} onRetry={missingRetry ? undefined : retry} scrollPanel chevron="none"
        onChooseBackend={() => {}} onChooseModel={(model, effort) => {
          (window as any).__pickerChoices.push({model, effort});
        }} />
    </div>
  {:else}
  <ConversationComposer conversationId="fixture" conversationExists backendKey="claude"
    draftKey={new URLSearchParams(location.search).get("draft") ?? "composer-fixture-" + crypto.randomUUID()}
    {running} {ask} {heldPromptRows} {errorNote} supportsSteer
    current={{model:"claude-opus-5-5",reasoningEffort:"high"}}
    models={[{model_id:"opus",display_name:"Opus with a deliberately long model display name",enabled:true},{model_id:"sonnet",display_name:"Sonnet",enabled:true}]}
    effortOptions={["high"]} onSend={send} onStop={() => running = false} onAnswer={() => ask = null}
  />
  {/if}
</main>
<style>
  :global(html), :global(body), :global(#app) {height:100%;margin:0;}
  main {display:flex;flex-direction:column;width:100%;height:720px;min-height:0;overflow:hidden;}
  .picker-proof { padding:16px; }
  .thread {flex:1;min-height:0;overflow:auto;padding:16px;}
</style>
`,
    "utf8"
  );
  await writeFile(
    mainPath,
    `import "../../assets/tokens.css";\nimport "../../assets/app.css";\nimport { mount } from "svelte";\nimport Host from "./${hostPath.split("/").at(-1)}";\nmount(Host, { target: document.getElementById("app")! });\n`,
    "utf8"
  );
  await writeFile(
    indexPath,
    `<!doctype html><html><body><div id="app"></div><script type="module" src="./${mainPath
      .split("/")
      .at(-1)}"></script></body></html>`,
    "utf8"
  );


  return {inputs: [indexPath], arguments: [indexPath], python: fileURLToPath(new URL("./conversation-composer.py", import.meta.url))};
}
