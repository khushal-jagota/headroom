<script lang="ts">
  /** The pane: who this is, what it has said, and the one box you speak into.
   *
   * The thread follows the answer rather than the bottom edge. Sending settles the message
   * you just sent near the top of the view and keeps the space below it for the reply, and
   * then nothing moves at all for as long as the reply fits in that space. Only when the
   * turn outgrows the view does the thread start moving, and then by the least it can while
   * keeping the newest line in sight. It never moves backwards.
   *
   * The room kept for an answer is given back as the answer earns it: what is left is only
   * ever as much as is holding the view where it is, so it shrinks to nothing once the
   * answer has filled it and never takes the reader with it when it goes.
   *
   * Whether the reader has gone off to read something else is read from the reader — a
   * wheel, a finger, a hand on the scrollbar, a key — and never from the scroll position
   * changing, because the position changes when this code moves it and that is nobody
   * deciding anything. While they are elsewhere nothing arriving moves the view, and when
   * something above them changes height or disappears from under them they stay where they
   * are looking. Whenever the newest line is not in sight — whether they are above it or
   * below it in the room left for an answer — the jump button is there, and it is the one
   * thing here allowed to move the view backwards.
   *
   * The thread also draws the messages this browser has sent that the record does not have
   * yet, after the rows and looking exactly like them. Each one goes the moment its row
   * arrives, so what replaces it is already in the place it was drawn in.
   *
   * A page that wants the conversation as a layer says how far open it is, and this is one
   * conversation at three heights rather than three of anything: at rest the head and the
   * thread give up their height and the rest bar says what happened last, and peeking and
   * opening give it back. The same thread, the same composer, the same draft throughout —
   * a move changes the height and nothing else, which is why what it costs the reader is
   * their place in the thread and why that place is held over every move.
   */
  import { tick, untrack, type Snippet } from "svelte";
  import ConversationComposer from "./ConversationComposer.svelte";
  import ConversationRestBar from "./ConversationRestBar.svelte";
  import ConversationTranscript from "./ConversationTranscript.svelte";
  import type { RunValues } from "../../lib/conversation/composer";
  import type { ConversationState } from "../../lib/conversation/conversationState";
  import { outgoingMessageNote, type OutgoingMessage } from "../../lib/conversation/outgoing";
  import { restLineFrom } from "../../lib/conversation/restLine";
  import { messageContentText } from "../../lib/conversation/wire";
  import type { TranscriptRow } from "../../lib/conversation/transcript";
  import type {
    AgentCommand,
    BackendModel,
    ConversationBackendKey,
    PermissionAskOption,
    PromptDeliveryMode
  } from "../../lib/conversation/wire";

  /** How far from the newest line still counts as being with it. */
  const NEAR_NEWEST_LINE_PIXELS = 48;
  /** Where a message you have just sent comes to rest: clear of the fade at the top. */
  const SENT_MESSAGE_TOP_GAP_PIXELS = 24;
  /** What the newest line keeps under it, so it stops above the fade at the bottom. */
  const NEWEST_LINE_BOTTOM_GAP_PIXELS = 24;
  /** How long after the reader's last wheel, finger or key they still count as the one
   *  doing the scrolling — long enough to cover the scrolling their input goes on causing
   *  after it, which is not this code's doing either. */
  const READER_DRIVING_MILLISECONDS = 400;

  let {
    conversationId,
    label,
    backendKey = null,
    workspaceFolder = null,
    rows = [],
    outgoingMessages = [],
    running = false,
    ask = null,
    askNote = null,
    current = { model: null, reasoningEffort: null },
    models = [],
    ownSenderLabel = null,
    livenessPulse = 0,
    effortOptions = [],
    availableCommands = [],
    defaultModelId = null,
    defaultReasoningEffort = null,
    heldPromptCount = 0,
    fateNote = null,
    errorNote = null,
    connectionTrouble = false,
    composerPlaceholder = "Message the agent...",
    composerDisabled = false,
    conversationState = $bindable(null),
    emptyState,
    onSend,
    onStop,
    onAnswer,
    onCancelTurn,
    onDiscardHeldPrompt,
    onNewConversation
  }: {
    /** Which conversation is on the screen. A message's files are fetched under it, so a
     *  piece can only ever reach a file kept for the conversation it belongs to. */
    conversationId: string;
    label: string;
    backendKey?: ConversationBackendKey | null;
    workspaceFolder?: string | null;
    rows?: readonly TranscriptRow[];
    /** Messages this browser has sent that the record does not have yet, oldest first. */
    outgoingMessages?: readonly OutgoingMessage[];
    running?: boolean;
    ask?: {
      askId: string;
      title: string;
      detail: string | null;
      options: readonly PermissionAskOption[];
    } | null;
    askNote?: string | null;
    current?: RunValues;
    models?: readonly BackendModel[];
    /** The label this pane sends under, so your own messages are not labelled as yours. */
    ownSenderLabel?: string | null;
    livenessPulse?: number;
    effortOptions?: readonly string[];
    /** The commands this conversation's agent reports, which the composer offers under a
     *  line being written as one. */
    availableCommands?: readonly AgentCommand[];
    defaultModelId?: string | null;
    defaultReasoningEffort?: string | null;
    heldPromptCount?: number;
    fateNote?: string | null;
    errorNote?: string | null;
    connectionTrouble?: boolean;
    composerPlaceholder?: string;
    composerDisabled?: boolean;
    /** How far open the conversation is, or null for a page that is not making a layer of
     *  it — which fills its container exactly as it always has. The page sets what it
     *  opens in; this writes back when the person moves it. */
    conversationState?: ConversationState | null;
    emptyState?: Snippet;
    onSend: (text: string, mode: PromptDeliveryMode, picked: RunValues) => Promise<boolean>;
    onStop?: () => void;
    onAnswer?: (optionId: string) => void;
    onCancelTurn?: () => void;
    /** Throw away one message that is still waiting for the agent, by its own id. */
    onDiscardHeldPrompt?: (messageId: string) => void;
    onNewConversation?: () => void;
  } = $props();

  let paneElement = $state<HTMLDivElement | null>(null);
  let threadElement = $state<HTMLDivElement | null>(null);
  let reservedSpaceElement = $state<HTMLDivElement | null>(null);
  /** The room kept under a message you have just sent, for the answer to arrive into. */
  let reservedSpacePixels = $state(0);
  /** The view is keeping the newest line in sight rather than staying where it is. */
  let following = $state(true);
  let jumpVisible = $state(false);
  /** Where the thread's last piece of content ends, measured from the top of everything in
   *  it. Scrolling does not change it, so it is measured when the thread changes and read
   *  from as the reader scrolls rather than measured again on every scroll event. */
  let newestLineBottomPixels = 0;
  /** The thread's own size. Watched because a thread that changes shape rewraps what is in
   *  it, which puts the end of its content somewhere new without a row arriving. */
  let threadWidthPixels = $state(0);
  let threadHeightPixels = $state(0);
  let settledOnOpening = false;
  let scrollRenderRequest = 0;
  /** The sent messages already settled into place. A message is settled once, however
   *  many times the thread is redrawn around it. */
  let settledMessageIds = new Set<string>();
  let readerDrivingUntilMilliseconds = 0;
  let readerPointerIsDown = false;
  /** How far open the pane was last drawn, so a run of the effect below that is not a move
   *  — the thread's binding arriving, say — corrects nothing. */
  let stateOnScreen: ConversationState | null = null;
  let stateHasBeenDrawn = false;
  /** What the reader was looking at when the thread last gave up its height, kept for the
   *  whole trip. There is nothing to hold on the way back out: the thread the way out
   *  would measure has no height yet. */
  let viewHeldAcrossTheMove: HeldView | null = null;

  let menuOpen = $state(false);
  let confirmArmed = $state(false);
  let menuElement = $state<HTMLDivElement | null>(null);
  let menuButton = $state<HTMLButtonElement | null>(null);

  let headerException = $derived.by(() => {
    if (ask) return { text: "waiting for you", accent: true };
    if (running) return { text: "working", accent: false };
    return null;
  });

  // Only at rest is there a bar to put it in, and nowhere else does the line mean
  // anything: peeked and opened have the turn head itself, six pixels away.
  // An empty own label is a pane that calls nothing its own, which is what the transcript
  // makes of a null one too.
  let restLine = $derived(
    conversationState === "rest" ? restLineFrom(rows, ownSenderLabel ?? "") : null
  );

  /** The one control through the states, and what it means where it is standing.
   *
   * Peeked has somewhere further to go and opened has somewhere to come back to; rest is
   * reached by pressing the ticket or Escape, and a conversation that is not a layer has
   * nowhere to be moved to at all.
   */
  let stateControl = $derived(
    conversationState === "peeked"
      ? { opensIt: true, glyph: "⤢", label: "Open the conversation full height" }
      : conversationState === "opened"
        ? { opensIt: false, glyph: "⤡", label: "Put the conversation back to a card" }
        : null
  );

  function closeMenu(): void {
    menuOpen = false;
    confirmArmed = false;
  }

  function onWindowPointerDown(event: PointerEvent): void {
    if (menuOpen && menuElement && !menuElement.contains(event.target as Node)) closeMenu();
    theComposersInputWasPressed(event);
  }

  /** Pressing the input at rest opens the conversation a little.
   *
   * The composer is a child, and one that knows nothing about how far open this is —
   * every page it draws on has the same box. So the press is read here, off the listener
   * already watching the window for presses, and only when it landed on this pane's own
   * input. It is the press rather than the focus because focus is not always a person:
   * the composer puts the cursor back itself when a send gets nowhere, and nothing may
   * move this that the person did not do.
   */
  function theComposersInputWasPressed(event: PointerEvent): void {
    if (conversationState !== "rest") return;
    const pressed = event.target;
    if (!(pressed instanceof Element) || paneElement === null) return;
    if (!paneElement.contains(pressed)) return;
    if (pressed.closest("[data-conversation-input]") === null) return;
    conversationState = "peeked";
  }

  function onWindowKeydown(event: KeyboardEvent): void {
    if (event.key !== "Escape") return;
    // Somebody nearer the key already answered it. Escape means "out of the thing I am
    // in", and the thing a person is in is rarely this: a ticket field being edited beside
    // a peeked conversation takes its own Escape to cancel the edit, and a second meaning
    // taken from the same press would close the conversation out from under them.
    if (event.defaultPrevented) return;
    if (menuOpen) {
      closeMenu();
      menuButton?.focus();
      return;
    }
    // One press, one state back. Rest is as far back as it goes, and a conversation that
    // is not a layer has no state to be moved.
    if (conversationState === "opened") conversationState = "peeked";
    else if (conversationState === "peeked") conversationState = "rest";
  }

  /** The control was pressed: forward from peeked, back from opened. */
  function moveThroughTheStates(): void {
    if (conversationState === "peeked") conversationState = "opened";
    else if (conversationState === "opened") conversationState = "peeked";
  }

  function confirmNewConversation(): void {
    onNewConversation?.();
    closeMenu();
  }

  /** What a message says about the way it was sent, in the transcript's own words. */
  function modeChip(mode: PromptDeliveryMode): string | null {
    if (mode === "send_now") return "sent now";
    if (mode === "steer") return "steered";
    return null;
  }

  // --- where things are in the thread ---------------------------------------------------

  function topWithin(thread: HTMLDivElement, element: Element): number {
    return (
      element.getBoundingClientRect().top
      - thread.getBoundingClientRect().top
      + thread.scrollTop
    );
  }

  /** Everything in the thread that takes up room, in the order it is laid out in.
   *
   * The transcript puts its rows in the thread's own layout rather than in a box of its
   * own, so what is in the DOM under the thread is not what is on screen under it: a
   * wrapper that lays out as its contents has no shape to measure. Whatever has no shape
   * is looked through to the things inside it, which are the ones that do. The reserved
   * space is left out on purpose — it is room for an answer, not something to read.
   */
  function contentElements(thread: HTMLDivElement): Element[] {
    const laidOut: Element[] = [];
    const consider = (element: Element): void => {
      if (element === reservedSpaceElement) return;
      if (element.getClientRects().length > 0) {
        laidOut.push(element);
        return;
      }
      for (const inside of element.children) consider(inside);
    };
    for (const child of thread.children) consider(child);
    return laidOut;
  }

  /** Whether the thread is on screen at all. At rest it is not: it keeps its rows and
   *  gives up its height.
   *
   * Nothing here has anything to say about a thread with no height. There is no view to
   * hold, no room to give back, and nothing for a message to settle into — every measure
   * it would take reads zero and every correction it would make lands on an element the
   * browser is not laying out. So it all stands down, and what was being held is still
   * being held when the thread has a shape again.
   */
  function theThreadHasAShape(thread: HTMLDivElement): boolean {
    return thread.clientHeight > 0;
  }

  function measureNewestLineBottom(thread: HTMLDivElement): number {
    const content = contentElements(thread);
    const last = content[content.length - 1];
    return last === undefined
      ? 0
      : topWithin(thread, last) + last.getBoundingClientRect().height;
  }

  /** The least this thread would have to be scrolled for its newest line to be in sight. */
  function scrollTopThatKeepsTheNewestLineInSight(thread: HTMLDivElement): number {
    return newestLineBottomPixels + NEWEST_LINE_BOTTOM_GAP_PIXELS - thread.clientHeight;
  }

  /** Whether the newest line is on screen — which it is not when the reader is above it,
   *  and not when they are below it either, down in the room kept for an answer. */
  function newestLineIsInSight(thread: HTMLDivElement): boolean {
    const beyondTheFold = scrollTopThatKeepsTheNewestLineInSight(thread) - thread.scrollTop;
    if (beyondTheFold > NEAR_NEWEST_LINE_PIXELS) return false;
    return newestLineBottomPixels >= thread.scrollTop;
  }

  /** Move only far enough that the newest line is in sight, and only ever forwards. */
  function keepTheNewestLineInSight(thread: HTMLDivElement): void {
    const least = scrollTopThatKeepsTheNewestLineInSight(thread);
    if (least > thread.scrollTop) thread.scrollTop = least;
  }

  /** Give back the room that is not holding the view where it is.
   *
   * The room only exists to keep the view still while an answer arrives into it, so as the
   * answer fills it there is less of it left to do that — and once the answer has filled it
   * there is none. Keeping exactly what the current position needs is what makes this free
   * of movement: the thread never becomes too short for where it is already scrolled to.
   */
  function releaseTheRoomThatIsNotHoldingTheView(thread: HTMLDivElement): void {
    // What the thread puts below its last line whatever else is going on — its own bottom
    // padding, and the gap the room sits after. It scrolls to just as the room does, so it
    // is part of what is already holding the view up.
    const alwaysBelowTheLastLine =
      thread.scrollHeight - newestLineBottomPixels - reservedSpacePixels;
    const stillHolding = Math.max(
      0,
      Math.ceil(
        thread.scrollTop + thread.clientHeight - newestLineBottomPixels - alwaysBelowTheLastLine
      )
    );
    if (stillHolding < reservedSpacePixels) reservedSpacePixels = stillHolding;
  }

  /** Read the thread's shape again, after anything that can have changed it.
   *
   * Scrolling is not one of those things — where the content ends does not depend on where
   * the reader is — so this is not on the path of a scroll event.
   */
  function readTheThreadAgain(thread: HTMLDivElement): void {
    newestLineBottomPixels = measureNewestLineBottom(thread);
    releaseTheRoomThatIsNotHoldingTheView(thread);
    jumpVisible = !newestLineIsInSight(thread);
  }

  // --- whether the reader has taken over --------------------------------------------------

  function readerIsDrivingTheScroll(): boolean {
    return readerPointerIsDown || performance.now() <= readerDrivingUntilMilliseconds;
  }

  /** A wheel, a finger, or a key: the reader working the thread themselves. */
  function readerDroveTheThread(): void {
    readerDrivingUntilMilliseconds = performance.now() + READER_DRIVING_MILLISECONDS;
  }

  function onThreadScroll(): void {
    if (!threadElement) return;
    const inSight = newestLineIsInSight(threadElement);
    // Only a person's own scrolling says anything about what they want. This code moves
    // the position too, and reading intent out of that would have the thread decide it
    // had been scrolled away from by its own following.
    if (readerIsDrivingTheScroll()) following = inSight;
    jumpVisible = !inSight;
  }

  // --- moving the thread --------------------------------------------------------------------

  /** The reader asking for the newest line. The one move here allowed to go backwards:
   *  they are the ones who asked, and from below it there is no other way back. */
  async function jumpToTheNewestLine(): Promise<void> {
    following = true;
    await tick();
    const thread = threadElement;
    if (thread === null) return;
    thread.scrollTop = Math.max(0, scrollTopThatKeepsTheNewestLineInSight(thread));
    readTheThreadAgain(thread);
  }

  /** The message a settle can be made against: the copy this browser drew, or — when its
   *  row has already arrived and taken over — the newest message in the record. */
  function theSentMessageOnScreen(thread: HTMLDivElement, messageId: string): Element | null {
    const drawn = Array.from(
      thread.querySelectorAll<HTMLElement>("[data-conversation-outgoing]")
    ).find((element) => element.dataset.conversationOutgoing === messageId);
    if (drawn !== undefined) return drawn;
    const recorded = thread.querySelectorAll('[data-conversation-row="prompt"]');
    return recorded[recorded.length - 1] ?? null;
  }

  /** The message the room is being held open under: the newest thing the person said,
   *  whether that is still this browser's own copy of it or the row that replaced it. */
  function theMessageTheRoomIsFor(thread: HTMLDivElement): Element | null {
    const drawn = thread.querySelectorAll("[data-conversation-outgoing]");
    const recorded = thread.querySelectorAll('[data-conversation-row="prompt"]');
    return drawn[drawn.length - 1] ?? recorded[recorded.length - 1] ?? null;
  }

  /** Measure the room again, against the height the thread has now.
   *
   * The room is a viewport less the message that asked for it, so a thread that has
   * changed height is holding an amount that was right for a screen it no longer has. It
   * cannot fix itself: giving room back is all it does on its own, so a thread that has
   * grown stays too short for where the reader was and the browser pulls them off the
   * message they just sent to fit what is left. Measured here the way it was measured when
   * it was first kept, and given back straight afterwards by the usual read — so this only
   * ever restores the room to what the new height would have kept in the first place.
   *
   * A room nobody is waiting on is not a room. Nothing here grows one back.
   */
  function measureTheRoomAgainstTheHeightWeHaveNow(thread: HTMLDivElement): void {
    if (reservedSpacePixels === 0) return;
    const asking = theMessageTheRoomIsFor(thread);
    if (asking === null) return;
    reservedSpacePixels = Math.max(
      0,
      Math.ceil(
        thread.clientHeight
        - asking.getBoundingClientRect().height
        - SENT_MESSAGE_TOP_GAP_PIXELS
      )
    );
  }

  /** Put a message that has just been sent near the top, with the answer's room under it.
   *
   * The room is a viewport less the message itself, which is what makes the message
   * reachable at the top of a thread that has nothing under it yet — and what keeps the
   * view still afterwards, because everything the answer adds grows into that room rather
   * than past the bottom of the thread.
   *
   * Says whether it settled. A round trip quick enough to beat the first frame leaves
   * nothing to settle on for a moment, and a settle that found nothing is worth trying
   * again rather than calling done.
   */
  async function settleTheSentMessage(messageId: string): Promise<boolean> {
    const thread = threadElement;
    if (thread === null) return false;
    const sent = theSentMessageOnScreen(thread, messageId);
    if (sent === null) return false;
    following = true;
    reservedSpacePixels = Math.max(
      0,
      Math.ceil(
        thread.clientHeight
        - sent.getBoundingClientRect().height
        - SENT_MESSAGE_TOP_GAP_PIXELS
      )
    );
    await tick();
    if (threadElement === null) return false;
    const stillThere = sent.isConnected
      ? sent
      : theSentMessageOnScreen(threadElement, messageId);
    if (stillThere === null) return false;
    threadElement.scrollTop = Math.max(
      0,
      topWithin(threadElement, stillThere) - SENT_MESSAGE_TOP_GAP_PIXELS
    );
    return true;
  }

  /** What the reader is looking at, in the order they are looking at it.
   *
   * More than one, because the thing at the top of their view is exactly the thing most
   * likely to disappear: a turn settling folds its tool calls away, and those are what a
   * reader scrolled up into the middle of a turn is looking at. The first of these that
   * survives is what the position is restored against.
   */
  type HeldView = {
    /** Where the reader was, before whatever happened next. */
    scrollTop: number;
    lines: { element: Element; top: number }[];
  };

  const MOST_HELD_LINES = 8;

  function holdWhatTheReaderIsLookingAt(thread: HTMLDivElement): HeldView {
    const lines: HeldView["lines"] = [];
    for (const element of contentElements(thread)) {
      const top = topWithin(thread, element);
      if (top + element.getBoundingClientRect().height <= thread.scrollTop) continue;
      lines.push({ element, top });
      if (lines.length === MOST_HELD_LINES) break;
    }
    return { scrollTop: thread.scrollTop, lines };
  }

  /** Whatever changed height above them — or vanished from under them — they are still
   *  looking at the same place.
   *
   * Measured from where the reader was rather than from where they are now, because a
   * thread that has just got shorter has already had the browser pull the position back
   * to fit it. Correcting from there would count that pull twice.
   *
   * Says whether it found anything of theirs left to measure from. Usually there is —
   * something changed height above them and everything around it survived. But the longer
   * they have been away the more of the page can go, and a caller that knows the gap was
   * a long one needs to know when there is nothing left of where they were.
   */
  function keepTheReaderWhereTheyWere(thread: HTMLDivElement, held: HeldView): boolean {
    const survivor = held.lines.find((line) => thread.contains(line.element));
    if (survivor === undefined) return false;
    const wanted = Math.max(0, held.scrollTop + topWithin(thread, survivor.element) - survivor.top);
    if (wanted !== thread.scrollTop) thread.scrollTop = wanted;
    return true;
  }

  function messageToSettleOn(): string | null {
    const newest = outgoingMessages[outgoingMessages.length - 1];
    if (newest === undefined || settledMessageIds.has(newest.messageId)) return null;
    return newest.messageId;
  }

  $effect.pre(() => {
    rows;
    outgoingMessages;
    const thread = threadElement;
    if (thread === null) return;
    if (outgoingMessages.length === 0) settledMessageIds = new Set();
    // Above the hold, not below it: holding a view means walking every row in the thread,
    // and a thread with no shape gives back nothing for the whole walk — every element in
    // it is looked through to its children, all the way down, for a view that is then
    // thrown away. Nothing under here has anything to say until it is on screen.
    if (!theThreadHasAShape(thread)) return;
    const held = untrack(() => holdWhatTheReaderIsLookingAt(thread));
    const justSent = untrack(() => messageToSettleOn());
    const wasFollowing = untrack(() => following);
    const request = ++scrollRenderRequest;
    void tick().then(async () => {
      if (request !== scrollRenderRequest || !threadElement) return;
      if (!theThreadHasAShape(threadElement)) return;
      newestLineBottomPixels = measureNewestLineBottom(threadElement);
      if (!settledOnOpening) {
        // Opening a conversation puts you at the end of it, wherever that is.
        settledOnOpening = true;
        following = true;
        keepTheNewestLineInSight(threadElement);
      } else if (justSent !== null) {
        if (await settleTheSentMessage(justSent)) settledMessageIds.add(justSent);
      } else if (wasFollowing) {
        keepTheNewestLineInSight(threadElement);
      } else {
        keepTheReaderWhereTheyWere(threadElement, held);
      }
      if (request !== scrollRenderRequest || !threadElement) return;
      readTheThreadAgain(threadElement);
    });
  });

  /** Something in the thread was worked: a turn's fold opened or closed under the reader.
   *
   * A fold is the one thing in here that changes the thread's height without a row
   * arriving, so it is the one thing an effect on the rows cannot see. The position is
   * taken before the click has had its effect and put back once it has had it.
   *
   * Put back whatever the view was doing, which is what makes this different from a row
   * landing. Nothing arrived and nothing is asking to be followed: a person opened or shut
   * something, and the line they were reading stays where it was on their screen. Only
   * once they are back where they were does following get its say, and all it can do then
   * is move forwards.
   */
  /** The thread changed shape, or something in it finished arriving.
   *
   * Neither is a row and neither is a person: a window resized, a pane resized, or an
   * image that was still loading when its row landed. All of them put the end of the
   * content somewhere new, and nothing else here would notice. Nobody is moved who was
   * not already following.
   */
  function theThreadIsADifferentShapeNow(): void {
    const thread = threadElement;
    if (thread === null || !theThreadHasAShape(thread)) return;
    // Nothing to put back: nobody was reading anything that moved, so whoever was
    // following goes on following and whoever was not stays exactly where they are.
    settleTheViewAfterAChangeOfShape(thread, null);
  }

  /** The thread is a different size than it was: work out where the reader belongs in it.
   *
   * The same three answers this file gives everywhere. A thread that has never had a shape
   * has not been opened yet whatever its rows say, so the first shape it gets is what
   * opening means — a conversation whose page starts it at rest has had rows all along and
   * has still never been looked at. Otherwise the reader goes back to the line they were
   * on, and following, which only ever moves forwards, gets the last word.
   *
   * With one way out. A view is held over a gap, and the longer the gap the more of the
   * page can go in it: a turn settling folds its whole run of tool calls out of the
   * document, so somebody who dropped to rest in the middle of one can come back to find
   * not one line of what they held still on the page. There is nothing to measure from
   * then, and leaving them where they landed means the top of a long conversation. The end
   * of it is the better answer, and being at the end is what following means.
   */
  function settleTheViewAfterAChangeOfShape(
    thread: HTMLDivElement,
    held: HeldView | null
  ): void {
    newestLineBottomPixels = measureNewestLineBottom(thread);
    if (!settledOnOpening) {
      // Opening a conversation puts you at the end of it, wherever that is.
      settledOnOpening = true;
      following = true;
      keepTheNewestLineInSight(thread);
    } else {
      if (held !== null && !keepTheReaderWhereTheyWere(thread, held)) following = true;
      if (following) keepTheNewestLineInSight(thread);
    }
    readTheThreadAgain(thread);
  }

  $effect(() => {
    threadWidthPixels;
    threadHeightPixels;
    if (threadElement === null) return;
    // After the rewrapping the new size caused, not before it.
    void tick().then(theThreadIsADifferentShapeNow);
  });

  /** The person moved how far open the conversation is.
   *
   * A move is a change of height and nothing else — the same thread, the same composer,
   * the same draft — so the only thing it can cost is the reader's place, and this is
   * where that is paid for. Before the DOM, because after it the browser has already
   * pulled the position to fit the new height and the line the reader was on may have no
   * height left to measure from; and after the rows effect above, so that a move landing
   * in the same breath as a row is the one that gets to say where the reader ends up.
   *
   * Rest is the state with no height at all. The way out of it has nothing of its own to
   * hold, so the view taken on the way in is what it restores against.
   */
  $effect.pre(() => {
    const wanted = conversationState;
    const thread = threadElement;
    if (thread === null) return;
    untrack(() => {
      const previous = stateOnScreen;
      const isAMove = stateHasBeenDrawn && previous !== wanted;
      stateOnScreen = wanted;
      stateHasBeenDrawn = true;
      if (!isAMove) return;
      // The DOM has not changed yet, so this is the thread as the person still sees it: a
      // shape here means there is a view worth holding, and no shape means they were at
      // rest and what was held on the way in is still the only answer.
      if (theThreadHasAShape(thread)) {
        viewHeldAcrossTheMove = holdWhatTheReaderIsLookingAt(thread);
      }
      const held = viewHeldAcrossTheMove;
      // Going to rest there is nothing to correct and nothing that could be corrected:
      // what was just held is what the way back out will be measured against.
      if (wanted === "rest") return;
      viewHeldAcrossTheMove = null;
      const request = ++scrollRenderRequest;
      void tick().then(async () => {
        if (request !== scrollRenderRequest || threadElement === null) return;
        if (!theThreadHasAShape(threadElement)) return;
        // The room before the reader, and a frame for it to be drawn at its new size in.
        // The room is what makes the thread long enough to hold where they were, so
        // putting them back before it has grown only has the browser refuse the move.
        measureTheRoomAgainstTheHeightWeHaveNow(threadElement);
        await tick();
        if (request !== scrollRenderRequest || threadElement === null) return;
        if (!theThreadHasAShape(threadElement)) return;
        settleTheViewAfterAChangeOfShape(threadElement, held);
      });
    });
  });

  function onThreadClick(): void {
    const thread = threadElement;
    if (thread === null) return;
    const held = holdWhatTheReaderIsLookingAt(thread);
    const wasFollowing = following;
    void tick().then(() => {
      if (!threadElement) return;
      newestLineBottomPixels = measureNewestLineBottom(threadElement);
      keepTheReaderWhereTheyWere(threadElement, held);
      if (wasFollowing) keepTheNewestLineInSight(threadElement);
      readTheThreadAgain(threadElement);
    });
  }
</script>

<svelte:window
  onpointerdown={onWindowPointerDown}
  onkeydown={onWindowKeydown}
  onpointerup={() => (readerPointerIsDown = false)}
  onpointercancel={() => (readerPointerIsDown = false)}
/>

<div
  class="chat-panel"
  data-conversation-pane
  data-conversation-state={conversationState}
  bind:this={paneElement}
>
  <div class="chat-head">
    {#if connectionTrouble}
      <span class="chat-conn-dot" role="img" aria-label="Connection trouble"></span>
    {/if}
    <span class="chat-lbl">{label}</span>
    {#if headerException}
      <span class={`chat-state ${headerException.accent ? "chat-state--attn" : ""}`}>
        {headerException.text}
      </span>
    {/if}
    <div class="chat-head-right">
      {#if workspaceFolder}
        <span class="chat-usage" data-conversation-workspace>{workspaceFolder}</span>
      {/if}
      <!-- The other way through the states, for the person who would rather press
           something than press Escape. Only a layer has anywhere to go.
           One control that changes what it means, and never two that replace each other:
           a button that is destroyed on being pressed takes the keyboard down to the body
           with it, and the person who pressed it is left nowhere. So it is the same
           element throughout and only the words on it move. -->
      {#if stateControl !== null}
        <button
          type="button"
          class="chat-overflow-btn"
          data-conversation-expand={stateControl.opensIt ? true : undefined}
          data-conversation-collapse={stateControl.opensIt ? undefined : true}
          aria-label={stateControl.label}
          title={stateControl.label}
          onclick={moveThroughTheStates}
        >{stateControl.glyph}</button>
      {/if}
      <div class="chat-overflow" bind:this={menuElement}>
        <button
          type="button"
          class="chat-overflow-btn"
          bind:this={menuButton}
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          aria-label="Conversation options"
          onclick={() => (menuOpen ? closeMenu() : (menuOpen = true))}
        >⋯</button>
        {#if menuOpen}
          <div class="chat-overflow-menu" role="menu">
            {#if confirmArmed}
              <button
                type="button"
                class="chat-overflow-item chat-overflow-item--confirm"
                role="menuitem"
                data-conversation-new-confirm
                onclick={confirmNewConversation}
              >Confirm — this kills the old one</button>
            {:else}
              <button
                type="button"
                class="chat-overflow-item"
                role="menuitem"
                data-conversation-new-arm
                onclick={() => (confirmArmed = true)}
              >New conversation</button>
            {/if}
          </div>
        {/if}
      </div>
    </div>
  </div>

  <div class="chat-thread-shell">
    <!-- A region you can reach: a scrolling box that cannot be focused cannot be scrolled
         from the keyboard at all, and a key that scrolls it is the reader taking it over
         exactly as a wheel is. The label is what that region is called once it is
         reachable. The two rules waived below are the ones that would have this box be
         focusable only if it were a control — it is not a control, it is a thing a person
         has to be able to scroll and read. -->
    <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
    <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
    <div
      class="chat-thread"
      data-conversation-thread
      role="region"
      aria-label="Conversation"
      tabindex="0"
      bind:this={threadElement}
      bind:clientWidth={threadWidthPixels}
      bind:clientHeight={threadHeightPixels}
      onloadcapture={theThreadIsADifferentShapeNow}
      onscroll={onThreadScroll}
      onwheel={readerDroveTheThread}
      ontouchstart={readerDroveTheThread}
      ontouchmove={readerDroveTheThread}
      onkeydown={readerDroveTheThread}
      onpointerdown={() => (readerPointerIsDown = true)}
      onclick={onThreadClick}
    >
      {#if emptyState && rows.length === 0 && outgoingMessages.length === 0}
        {@render emptyState()}
      {/if}
      <ConversationTranscript
        {rows}
        {models}
        {ownSenderLabel}
        {livenessPulse}
        {conversationId}
      />
      {#each outgoingMessages as message (message.messageId)}
        {@const chip = modeChip(message.mode)}
        {@const note = outgoingMessageNote(message)}
        <article class="chat-u" data-conversation-outgoing={message.messageId}>
          {#if note || chip}
            <div class="c2-label" data-conversation-outgoing-label>
              {#if note}{note}{/if}
              {#if chip}<span class="c2-chip">{chip}</span>{/if}
              <!-- Offered only on a message the system said it is holding. One that has
                   already reached the agent is not a message anybody can take back. -->
              {#if message.knownFate === "waiting_for_the_agent" && onDiscardHeldPrompt}
                <button
                  type="button"
                  class="c2-discard"
                  data-conversation-outgoing-discard={message.messageId}
                  aria-label="Do not send this message"
                  onclick={() => onDiscardHeldPrompt?.(message.messageId)}
                >×</button>
              {/if}
            </div>
          {/if}
          <!-- A message on its way holds what it is about to send rather than what the
               record will name, so its words are drawn from that, plainly. It becomes the
               rendered row the moment the record has it. -->
          {messageContentText(message.content)}
        </article>
      {/each}
      {#if reservedSpacePixels > 0}
        <div
          class="c2-reserved"
          data-conversation-reserved-space
          bind:this={reservedSpaceElement}
          style:height={`${reservedSpacePixels}px`}
          aria-hidden="true"
        ></div>
      {/if}
    </div>
    {#if jumpVisible}
      <button
        type="button"
        class="chat-jump"
        aria-label="Jump to latest message"
        onclick={() => void jumpToTheNewestLine()}
      ><span>Latest</span><span aria-hidden="true">↓</span></button>
    {/if}
  </div>

  <!-- At rest the one line above the composer is the whole of the conversation on screen,
       so it is only ever here: peeked and opened have the turn head itself a few pixels
       away, saying the same thing twice. -->
  {#if conversationState === "rest"}
    <ConversationRestBar line={restLine} />
  {/if}

  <ConversationComposer
    {backendKey}
    {running}
    {ask}
    {askNote}
    {current}
    {models}
    {effortOptions}
    {availableCommands}
    {defaultModelId}
    {defaultReasoningEffort}
    {heldPromptCount}
    {fateNote}
    {errorNote}
    placeholder={composerPlaceholder}
    disabled={composerDisabled}
    {onSend}
    {onStop}
    {onAnswer}
    {onCancelTurn}
  />
</div>

<style>
  :global([data-conversation-pane]) { gap: var(--space-2); }
  /* This pane corrects the reader's position itself when something above them changes
     height, so the browser must not correct it as well and double the move. */
  :global([data-conversation-thread]) { overflow-anchor: none; }
  /* At rest the conversation is the composer and one line above it. The head and the
     thread give up their height and keep everything else — their rows, their scroll, the
     turn they are in the middle of — because this is the same conversation at a different
     height and not a different conversation. The composer is not in here: it is the one
     thing that never moves, and a draft in it survives every move by never being touched. */
  :global([data-conversation-pane][data-conversation-state="rest"] .chat-head),
  :global([data-conversation-pane][data-conversation-state="rest"] .chat-thread-shell) {
    display: none;
  }
  /* Room for an answer, held open under the message that asked for it. It is not content:
     it never shrinks to make room for content, and nothing ever scrolls to it. */
  .c2-reserved { flex: none; }
  /* A message that has been sent and is not in the record yet is drawn as the record will
     draw it, so these must read exactly as the transcript's own label and chip do. */
  .c2-label {
    color: var(--text-faint);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    letter-spacing: var(--tracking-mono);
    margin-block-end: var(--space-1);
  }
  .c2-chip {
    margin-inline-start: var(--space-2);
    border: var(--border-hairline) solid var(--border-color);
    border-radius: var(--radius-pill);
    color: var(--accent-bright);
    padding: 0 var(--space-2);
  }
  /* Quiet until wanted: taking a message back is available on every waiting message and
     asked for by almost none of them, so it sits at the weight of the line it is on. */
  .c2-discard {
    margin-inline-start: var(--space-2);
    border: 0;
    background: none;
    color: inherit;
    cursor: pointer;
    font: inherit;
    line-height: 1;
    padding: 0 var(--space-1);
  }
  .c2-discard:hover { color: var(--accent-error); }
</style>
