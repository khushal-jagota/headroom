import { artifactForPressOnThePage } from "./managedArtifactPress";
import type { ManagedFileTarget } from "./filePreview";
import type { ConversationState } from "./conversation/conversationState";

/** The artifact a pane is showing, and everything that goes with showing it there.
 *
 * Every surface that shows a conversation also shows artifacts, and each one used to
 * carry its own copy of this: the press to claim, the focus to lend and give back, the
 * Escape, and the full conversation to step out of the way. The copies drifted. This is
 * the one of them.
 *
 * The host says where the file is kept, because that differs and nothing else does: the
 * Workspace keeps a Ticket's and the Chief's in the address, so a reload and a shared
 * link show them, and a pane with no address of its own keeps its in the pane.
 */
export function createArtifactOnThePage(host: {
  shown: () => ManagedFileTarget | null;
  keep: (file: ManagedFileTarget | null) => void;
  conversationState: () => ConversationState;
  dismissConversation: () => void;
}) {
  let reloadSignal = $state(0);
  let element = $state<HTMLElement | null>(null);
  let whatHadFocus: HTMLElement | null = null;

  /** The artifact takes focus when it opens, so the keyboard is where the reading is.
   *
   * It is also what makes Escape reach this pane: a field is an editable surface and
   * Escape belongs to the editor while the caret is in one, so an artifact opened from a
   * link inside a field must move the focus out of it. */
  $effect(() => {
    if (host.shown() && element) element.focus();
  });

  function show(file: ManagedFileTarget | null): void {
    if (file && !host.shown()) {
      whatHadFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    }
    host.keep(file);
    // A full conversation is the whole pane, and an artifact opened behind it is an
    // artifact nobody can see, so the conversation steps out of the way to let it through.
    if (file && host.conversationState() === "opened") host.dismissConversation();
    if (!file) {
      const restoreFocus = whatHadFocus;
      whatHadFocus = null;
      queueMicrotask(() => restoreFocus?.focus());
    }
  }

  return {
    get shown(): ManagedFileTarget | null {
      return host.shown();
    },
    get reloadSignal(): number {
      return reloadSignal;
    },
    get element(): HTMLElement | null {
      return element;
    },
    set element(value: HTMLElement | null) {
      element = value;
    },
    refresh(): void {
      reloadSignal += 1;
    },
    close(): void {
      show(null);
    },
    /** A press on a link to an artifact shows it here instead of leaving the pane.
     *
     * The pane stays underneath, in the place the reader left it, and the conversation
     * stays in its own layer — which is the point: the artifact and the conversation it
     * was named in are on the screen at once. */
    claimPress(event: MouseEvent): void {
      const file = artifactForPressOnThePage(event);
      if (!file) return;
      event.preventDefault();
      show(file);
    },
    /** Escape closes the artifact, unless the conversation is open over it.
     *
     * One press to one thing, and the thing on top answers for it. A full conversation
     * covers the artifact, and Escape returns that conversation to its dock. */
    closeOnEscape(event: KeyboardEvent): void {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      if (!host.shown() || host.conversationState() === "opened") return;
      event.preventDefault();
      show(null);
    }
  };
}
