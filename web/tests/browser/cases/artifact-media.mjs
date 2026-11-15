import {writeFile} from "node:fs/promises";
import {join} from "node:path";
import {fileURLToPath} from "node:url";

const picture = width => `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="300" viewBox="0 0 ${width} 300"><rect width="${width}" height="300" fill="#3a6"/></svg>`;

export async function prepare({scratchRoot}) {
  const stem = `artifact-media-${process.pid}`;
  const host = join(scratchRoot, `${stem}.svelte`);
  const main = join(scratchRoot, `${stem}.ts`);
  const index = join(scratchRoot, `${stem}.html`);
  // Real Markdown through the real managed surface, in the chat column it is read in.
  await writeFile(host, `<script lang="ts">
  import MarkdownBlock from "../src/components/MarkdownBlock.svelte";
  import MessagePieces from "../src/components/conversation/MessagePieces.svelte";
  const pixel = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aXioAAAAASUVORK5CYII=";
  const pasted = [{piece: "image" as const, data: pixel, media_type: "image/png", file_name: "one.png"},
    {piece: "image" as const, data: pixel, media_type: "image/png", file_name: "two.png"}];
  const text = "The plan is in [the review plan](/files/tickets/t_media/plan.html) for review.\\n\\n"
    + "![Wide picture](/files/tickets/t_media/wide.svg)\\n\\n"
    + "Two together:\\n\\n![First](/files/tickets/t_media/wide.svg)\\n![Second](/files/tickets/t_media/narrow.svg)\\n";
</script>
<div class="chat-thread" data-artifact-media-fixture><MarkdownBlock {text} /></div>
<div class="chat-thread" data-pasted-fixture><MessagePieces content={pasted} conversationId="fixture" /></div>`);
  await writeFile(main, `import {mount} from 'svelte'; import Host from './${stem}.svelte'; import '../../assets/tokens.css'; import '../../assets/app.css'; mount(Host, {target: document.getElementById('app')!});`);
  await writeFile(index, `<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="app"></div><script type="module" src="./${stem}.ts"></script></body></html>`);
  return {
    inputs: [index], arguments: [index],
    python: fileURLToPath(new URL("./artifact-media.py", import.meta.url)),
    resources: [
      {path: "/files/tickets/t_media/plan.html", contentType: "text/html", body: "<h1>Plan</h1>"},
      {path: "/files/tickets/t_media/wide.svg", contentType: "image/svg+xml", body: picture(640)},
      {path: "/files/tickets/t_media/narrow.svg", contentType: "image/svg+xml", body: picture(200)},
    ],
  };
}
