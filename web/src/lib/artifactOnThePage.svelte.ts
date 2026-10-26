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
  /** The pane this artifact is drawn in, so the keyboard can be given back to something
   *  in it that the reader can still see. */
  root: () => HTMLElement | null;
}) {
  let reloadSignal = $state(0);
  let element = $state<HTMLElement | null>(null);
  let whatHadFocus: HTMLElement | null = null;

  /** Hand the keyboard to the artifact as soon as the artifact can hold it.
   *
   * It cannot always hold it at once. A full conversation covers the reading layer and
   * the dock makes that layer inert while it does, and the link the reader pressed is in
   * that conversation, so the artifact is drawn into a layer that refuses focus. The dock
   * clears inert on its next measurement, a frame later, which is not a change this piece
   * can see. A single focus() at open is thrown away there and the keyboard is left on the
   * page. So wait for the layer to let go, then take it.
   *
   * Taking it is also what makes Escape reach this pane: a field is an editable surface
   * and Escape belongs to the editor while the caret is in one, so an artifact opened from
   * a link inside a field must move the focus out of it.
   *
   * Returns the way to call the hand-off off, which the effect does whenever the artifact
   * closes or another one replaces it. A hand-off nobody is waiting for any more must
   * never arrive: a reader who moved on keeps the keyboard where they put it. */
  function handOverTheKeyboard(target: HTMLElement): () => void {
    let cancelled = false;
    let waitingOn: MutationObserver | null = null;
    function give(): void {
      if (cancelled || !target.isConnected) return;
      const shut = target.closest("[inert]");
      if (shut) {
        // Watch whatever is holding the layer shut, and look again when it lets go,
        // because by then a different ancestor can be the one holding it.
        waitingOn?.disconnect();
        waitingOn = new MutationObserver(give);
        waitingOn.observe(shut, { attributes: true, attributeFilter: ["inert"] });
        return;
      }
      cancelled = true;
      waitingOn?.disconnect();
      target.focus();
    }
    give();
    return () => {
      cancelled = true;
      waitingOn?.disconnect();
    };
  }

  $effect(() => {
    const target = host.shown() ? element : null;
    if (!target) return;
    return handOverTheKeyboard(target);
  });

  /** Give the keyboard back to something the reader can see and use.
   *
   * Usually that is the link the artifact was opened from. When the link was read in the
   * conversation, it leaves the screen with the transcript as the conversation returns to
   * its bar, and focusing something nobody can see drops the keyboard on the page. The
   * conversation's own bar takes it instead: the same conversation the artifact was opened
   * from, one press away from the words it was named in. */
  function giveTheKeyboardBack(had: HTMLElement | null): void {
    if (had?.isConnected && !had.closest("[inert]")) {
      had.focus();
      if (document.activeElement === had) return;
    }
    host
      .root()
      ?.querySelector<HTMLElement>("[data-conversation-rest-bar], [data-conversation-expand]")
      ?.focus();
  }

  function show(file: ManagedFileTarget | null): void {
    if (file && !host.shown()) {
      whatHadFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    }
    host.keep(file);
    // A full conversation is the whole pane, and an artifact opened behind it is an
    // artifact nobody can see, so the conversation steps out of the way to let it through.
    if (file && host.conversationState() === "opened") host.dismissConversation();
    if (!file) {
      const had = whatHadFocus;
      whatHadFocus = null;
      queueMicrotask(() => giveTheKeyboardBack(had));
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
