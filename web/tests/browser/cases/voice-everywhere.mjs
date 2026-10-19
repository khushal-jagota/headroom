import { writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export async function prepare({ scratchRoot }) {
  const root = fileURLToPath(new URL("../../../", import.meta.url));
  const webRoot = root;
  const scratch = scratchRoot;
const repositoryRoot = join(webRoot, "..");
const hostPath = join(scratchRoot, `voice-everywhere-host-${process.pid}.svelte`);
const mainPath = join(scratchRoot, `voice-everywhere-main-${process.pid}.ts`);
const indexPath = join(scratchRoot, `voice-everywhere-index-${process.pid}.html`);

  await writeFile(
    hostPath,
    String.raw`<script lang="ts">
  import { QueryClient, QueryClientProvider } from "@tanstack/svelte-query";
  import ConversationComposer from "../src/components/conversation/ConversationComposer.svelte";
  import ReviewProposalCard from "../src/components/ReviewProposalCard.svelte";
  import { provideNavigationApplication } from "../src/lib/navigation/application.svelte";
  import { navigationReviewPresentation } from "../src/lib/navigation/presentation";

  const parameters = new URLSearchParams(location.search);
  const review = parameters.get("mode") === "review";
  const navigation=review ? provideNavigationApplication() : undefined;
  let identity=$derived(navigation?.selectors.effective ? navigationReviewPresentation(navigation.selectors.effective).items.find(item=>item.ticket_id==='review-voice') : undefined);
  const conversationId = parameters.get("conversation") === "1" ? "conv-existing" : null;
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  let sent = $state<string[]>([]);
  (window as any).__sentVoiceMessages = () => sent;
</script>

{#if review}
  <QueryClientProvider client={queryClient}>
    {#if identity}<ReviewProposalCard {identity} ticketId="review-voice" field="implementation" />{/if}
  </QueryClientProvider>
{:else}
  <ConversationComposer
    {conversationId}
    conversationExists={conversationId !== null}
    showRunPicker={false}
    onSend={async (content) => {
      sent = content.flatMap((piece) => piece.piece === "text" ? [piece.text] : []);
      return true;
    }}
  />
{/if}
`,
    "utf8"
  );
  await writeFile(
    mainPath,
    String.raw`globalThis.EventSource=class extends EventTarget {onerror=null;close(){}};
const parameters = new URLSearchParams(location.search);
Object.defineProperty(window, "matchMedia", {
  value: () => ({
    matches: parameters.get("coarse") === "1",
    addEventListener() {},
    removeEventListener() {}
  })
});
if (parameters.get("supported") !== "0") {
  Object.defineProperty(navigator, "mediaDevices", {
    value: { getUserMedia: async () => ({ getTracks: () => [{ stop() {} }] }) }
  });
  class FixtureMediaRecorder {
    static isTypeSupported() { return true; }
    mimeType = "audio/webm";
    ondataavailable = null;
    onstop = null;
    start() {}
    stop() {
      this.ondataavailable?.({ data: new Blob(["spoken"], { type: "audio/webm" }) });
      this.onstop?.();
    }
  }
  Object.defineProperty(window, "MediaRecorder", { value: FixtureMediaRecorder });
} else {
  Object.defineProperty(window, "MediaRecorder", { value: undefined });
  Object.defineProperty(navigator, "mediaDevices", { value: undefined });
}
import "../../assets/tokens.css";
import "../../assets/app.css";
import { mount } from "svelte";
import Host from "./${hostPath.split("/").at(-1)}";
mount(Host, { target: document.getElementById("app")! });
`,
    "utf8"
  );
  await writeFile(
    indexPath,
    `<!doctype html><html><body><div id="app"></div><script type="module" src="./${mainPath
      .split("/")
      .at(-1)}"></script></body></html>`,
    "utf8"
  );

  return {inputs: [indexPath], arguments: [indexPath], python: fileURLToPath(new URL("./voice-everywhere.py", import.meta.url))};
}
