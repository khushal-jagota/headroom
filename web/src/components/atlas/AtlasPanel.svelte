<script lang="ts">
  // The doing-surface: a real screen raised over the world.
  //
  // This component is a frame and nothing else. It gives the screen inside a
  // definite height — which is what `.ticket-screen { height: 100% }` needs, and
  // the reason the Workspace pane works — and it owns closing. What goes inside is
  // the app's own Ticket screen, Sprint Item screen, or conversation, unchanged.
  //
  // The screens inside link the way they link everywhere else, at `#/workspace/…`.
  // Inside Atlas such a link must move the world instead of leaving it, so the frame
  // catches the click here rather than in any screen. The frame does not decide where
  // Atlas goes: it reports the place and the route moves.
  import type { Snippet } from "svelte";
  import { selectionForLinkClick } from "../../lib/atlas/navigation";
  import type { AtlasSelection } from "../../lib/atlas/contracts";

  let {
    open,
    onClose,
    onNavigate,
    onBack,
    children
  }: {
    open: boolean;
    onClose: () => void;
    // A link inside named a place in the world.
    onNavigate?: (selection: AtlasSelection) => void;
    // Given only while there is somewhere to go back to.
    onBack?: (() => void) | null;
    children: Snippet;
  } = $props();

  let panelElement = $state<HTMLElement | null>(null);

  // One handler for every link inside, including the ones the server builds and no
  // screen here owns. A keyboard Enter on a link raises this same event.
  function onClick(event: MouseEvent): void {
    if (!onNavigate || event.defaultPrevented) return;
    const anchor = (event.target as Element | null)?.closest?.("a[href]");
    if (!anchor) return;
    const selection = selectionForLinkClick(
      // The attribute as written, not the resolved property: the app writes hash text.
      { href: anchor.getAttribute("href"), target: anchor.getAttribute("target") },
      event
    );
    if (!selection) return;
    event.preventDefault();
    onNavigate(selection);
  }

  // Escape belongs to what is open inside the panel first: an artifact opened on the
  // screen in here, and a conversation that is peeked or opened, which steps back one
  // state. Only when nothing inside claimed it does Escape close the panel, so they
  // never fight over the key. The question is asked of the DOM because this handler runs
  // before the ones the screens inside register.
  function onKeydown(event: KeyboardEvent): void {
    if (event.key !== "Escape" || event.defaultPrevented) return;
    if (panelElement?.querySelector("[data-ticket-artifact]")) return;
    const pane = panelElement?.querySelector("[data-conversation-pane]");
    const conversationState = pane?.getAttribute("data-conversation-state");
    if (conversationState && conversationState !== "rest") return;
    onClose();
  }
</script>

<svelte:window onkeydown={onKeydown} />

{#if open}
  <!-- The frame listens for clicks on the links inside it. Those links are anchors and
       keep their own keyboard behaviour: Enter on a link raises this same event. -->
  <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <aside class="atlas-panel" bind:this={panelElement} onclick={onClick} data-atlas-panel>
    <button type="button" class="atlas-panel-close" onclick={onClose} aria-label="Close">×</button>
    {#if onBack}
      <div class="atlas-panel-back">
        <button type="button" onclick={onBack} data-atlas-back>‹ Back</button>
      </div>
    {/if}
    <div class="atlas-panel-body">
      {@render children()}
    </div>
  </aside>
{/if}
