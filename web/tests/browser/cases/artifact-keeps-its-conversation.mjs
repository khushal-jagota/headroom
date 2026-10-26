import {writeFile} from "node:fs/promises";
import {join} from "node:path";
import {fileURLToPath} from "node:url";

export async function prepare({scratchRoot}) {
  const host = join(scratchRoot, `artifact-conversation-host-${process.pid}.svelte`);
  const main = join(scratchRoot, `artifact-conversation-main-${process.pid}.ts`);
  const index = join(scratchRoot, `artifact-conversation-index-${process.pid}.html`);
  await writeFile(host, `<script lang="ts">
    import {flushSync} from "svelte";
    import ManagerFrame from "../src/components/ManagerFrame.svelte";
    import type {ManagedFileTarget} from "../src/lib/filePreview";
    import type {ConversationState} from "../src/lib/conversation/conversationState";
    let paneState = $state<ConversationState>("rest");
    let addressState = $state<ConversationState>("rest");
    // The Chief's pane keeps its artifact where a host asks it to. This stands in for
    // the Workspace address, and records that the frame wrote through it.
    let addressed = $state<ManagedFileTarget | null>(null);
    (window as any).__state = (value: ConversationState) => flushSync(() => paneState = value);
    (window as any).__addressed = () => addressed;
  </script>
  <section data-pane-kept style="height:420px">
    <ManagerFrame bind:conversationState={paneState} data-frame="kept">
      <p data-doc>
        <a href="/files/tickets/t_pane/plan.md">Document plan</a>
        <a href="/files/managers/si_pane/archive.zip">Unsupported</a>
        <a href="/files/tickets/t_pane/plan.md" download="">Marked download</a>
      </p>
      {#snippet conversation()}
        <div data-conversation-pane data-conversation-state={paneState}>
          <p>Here is <a href="/files/tickets/t_pane/plan.md">the plan</a> from the other Ticket.</p>
        </div>
      {/snippet}
    </ManagerFrame>
  </section>
  <section data-address-kept style="height:420px">
    <ManagerFrame bind:conversationState={addressState} data-frame="addressed"
      openedFile={addressed} onOpenFile={(file) => addressed = file}>
      <p>Nothing to read here.</p>
      {#snippet conversation()}
        <div data-conversation-pane>
          <p>And <a href="#/preview?source=ticket&ticket=t_pane&path=plan.md">Open plan.md</a> here.</p>
        </div>
      {/snippet}
    </ManagerFrame>
  </section>`);
  await writeFile(main, `import {mount} from 'svelte'; import Host from './${host.split('/').at(-1)}';
    import '../../assets/tokens.css'; import '../../assets/app.css';
    mount(Host,{target:document.getElementById('app')!});`);
  await writeFile(index, `<!doctype html><html><body><div id="app"></div><script type="module" src="./${main.split('/').at(-1)}"></script></body></html>`);
  return {
    resources: [{path: "/files/tickets/t_pane/plan.md", body: "# The plan\n\nRead from the pane it was named in.\n", contentType: "text/markdown"}],
    inputs: [index],
    arguments: [index],
    python: fileURLToPath(new URL("./artifact-keeps-its-conversation.py", import.meta.url))
  };
}
