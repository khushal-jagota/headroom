import {writeFile} from "node:fs/promises";
import {join} from "node:path";
import {fileURLToPath} from "node:url";

export async function prepare({scratchRoot}) {
  const host = `artifact-conversation-host-${process.pid}.svelte`;
  const main = `artifact-conversation-main-${process.pid}.ts`;
  const index = join(scratchRoot, `artifact-conversation-index-${process.pid}.html`);
  // The whole shell, so the real router reads the address on every load and on a reload,
  // and the panes under test are the actual hosts: the Chief's pane through BoardRoute,
  // which keeps its artifact in the Workspace address, and a Manager's pane, which keeps
  // its own. Both live transports take their frames from the case fixture over HTTP.
  await writeFile(join(scratchRoot, host), `<script lang="ts">
import AppWithQueryClient from '../src/AppWithQueryClient.svelte';
// Wire snapshots arrive through the production EventSource transports, so the real
// navigation parser and the real conversation view reader are the ones under test. Each
// stream address has a 'snapshot' sibling the case fixture answers.
const sources = new Set<any>();
class FixtureEventSource {
  onopen = null; onmessage = null; onerror: null | (() => void) = null;
  listeners = new Map<string, (event: {data: string}) => void>();
  closed = false;
  url: string;
  constructor(url: string) {
    this.url = url;
    const snapshot = url.startsWith('/api/navigation/view/events?')
      ? url.replace('/view/events?', '/view/snapshot?')
      : url.includes('/view/tail?')
        ? url.replace('/view/tail?', '/view/snapshot?')
        : null;
    // The change signal has nothing to say in this case, so /api/changes stays quiet.
    if (snapshot === null) return;
    sources.add(this);
    void this.refresh(snapshot);
  }
  async refresh(from: string) {
    let data: string;
    try {
      const response = await fetch(from);
      if (!response.ok) throw new Error(String(response.status));
      data = await response.text();
    } catch {
      // A missing frame is reported rather than left hanging, so a fixture gap shows up
      // as the reader's own trouble instead of a screen that never finishes loading.
      if (!this.closed) this.onerror?.();
      return;
    }
    if (this.closed) return;
    this.listeners.get('snapshot')?.({data});
  }
  addEventListener(name: string, listener: any) { this.listeners.set(name, listener); }
  removeEventListener(name: string) { this.listeners.delete(name); }
  close() { this.closed = true; sources.delete(this); }
}
(globalThis as any).EventSource = FixtureEventSource;
</script>
<AppWithQueryClient />
<style>:global(html),:global(body),:global(#app){height:100%;margin:0;overflow:hidden;}</style>
`);
  await writeFile(join(scratchRoot, main), `import {mount} from 'svelte'; import Host from './${host}';
import '../../assets/tokens.css'; import '../../assets/app.css';
mount(Host,{target:document.getElementById('app')!});`);
  await writeFile(index, `<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="app"></div><script type="module" src="./${main}"></script></body></html>`);
  return {
    // The artifacts both conversations name. Each belongs to a Ticket that is not the
    // conversation's owner, which is the case the owner reported.
    resources: [
      {
        path: "/files/tickets/t_elsewhere/artifacts/plan.md",
        body: "# The rollout plan\n\nRead from the pane it was named in.\n",
        contentType: "text/markdown"
      },
      {
        path: "/files/tickets/t_faraway/artifacts/budget.md",
        body: "# The budget note\n\nRead from the Chief's pane.\n",
        contentType: "text/markdown"
      },
      // The Manager's own artifact, named in its document rather than its conversation.
      // A press there is the same claim, and it is where the focus the artifact borrows
      // can be given back, because the document is still on the screen behind it.
      {
        path: "/files/managers/si_fixture/notes.md",
        body: "# The handover notes\n\nNamed in the Manager's own document.\n",
        contentType: "text/markdown"
      }
    ],
    inputs: [index],
    arguments: [index],
    python: fileURLToPath(new URL("./artifact-keeps-its-conversation.py", import.meta.url))
  };
}
