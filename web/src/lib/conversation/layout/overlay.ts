import { conversationMotion, CONVERSATION_CARD_MS } from "../surfaceMotion";
import { visibleHostBounds, retainClearance } from './geometry';
import { CONVERSATION_PEEK_FRACTION, TICKET_SHORT_LANDSCAPE_PEEK_FRACTION, SHORT_LANDSCAPE_WIDTH, SHORT_LANDSCAPE_HEIGHT } from './config';

/** One mounted host owns its card geometry and its document's high-water scroll tail. */
export function conversationOverlay(host: HTMLElement): { destroy(): void } {
  let clearance = 0;
  let card: HTMLElement | null = null;
  let composer: HTMLElement | null = null;
  let documentScroller: HTMLElement | null = null;
  let frame = 0;
  let cardState: string | undefined;
  let cardHeight = 0;
  let layer: HTMLElement | null = null;
  const viewport = window.visualViewport;
  const set = (name: string, value: number) => {
    const text = `${value}px`;
    if (host.style.getPropertyValue(name) !== text) host.style.setProperty(name, text);
  };
  function measure(): void {
    frame = 0;
    const nextCard = host.querySelector<HTMLElement>('[data-conversation-pane]');
    if (nextCard !== card) {
      if (card) sizes.unobserve(card);
      card = nextCard;
      if (card) sizes.observe(card);
    }
    const nextComposer = host.querySelector<HTMLElement>('[data-conversation-composer]');
    if (nextComposer !== composer) {
      if (composer) sizes.unobserve(composer);
      composer = nextComposer;
      if (composer) sizes.observe(composer);
    }
    const nextDocumentScroller = host.querySelector<HTMLElement>('.ticket-doc, .manager-doc');
    if (nextDocumentScroller !== documentScroller) {
      if (documentScroller) sizes.unobserve(documentScroller);
      documentScroller = nextDocumentScroller;
      if (documentScroller) sizes.observe(documentScroller);
    }
    const rect = host.getBoundingClientRect();
    const bounds = visibleHostBounds(rect, { top: viewport?.offsetTop ?? 0, left: viewport?.offsetLeft ?? 0, width: viewport?.width ?? window.innerWidth, height: viewport?.height ?? window.innerHeight });
    set('--conversation-visible-top', bounds.top);
    set('--conversation-visible-bottom', bounds.bottom);
    // Match the actual text channel even when a native scrollbar consumes width.
    const documentLeft = documentScroller
      ? documentScroller.getBoundingClientRect().left - rect.left + documentScroller.clientLeft : 0;
    const documentRight = documentScroller
      ? rect.width - documentLeft - documentScroller.clientWidth : 0;
    set('--conversation-visible-left', Math.max(bounds.left, documentLeft));
    set('--conversation-visible-right', Math.max(bounds.right, documentRight));
    set('--conversation-visible-height', bounds.height);
    const shortTicket = host.classList.contains('ticket-page') && rect.width <= SHORT_LANDSCAPE_WIDTH && rect.height <= SHORT_LANDSCAPE_HEIGHT;
    set('--conversation-peek-height', rect.height * (shortTicket ? TICKET_SHORT_LANDSCAPE_PEEK_FRACTION : CONVERSATION_PEEK_FRACTION));
    if (composer) {
      // Ordinary composer popovers extend outside it. Scroll only genuinely oversized
      // content, whose menus become part of that scrollable flow through the attribute.
      const popupRoom = Math.max(0, composer.getBoundingClientRect().top - rect.top - bounds.top);
      set('--conversation-popup-height', popupRoom);
      const targetHeight = parseFloat(getComputedStyle(card ?? composer).getPropertyValue('--conversation-target')) || 0;
      const gap = parseFloat(getComputedStyle(host).getPropertyValue('--space-6')) || 0;
      const menuOpen = composer.querySelector('.chat-menu, .chat-add-menu, .listbox-picker-panel') !== null;
      const constrained = composer.scrollHeight > composer.clientHeight + 1 || (menuOpen && popupRoom < targetHeight + gap);
      composer.toggleAttribute('data-conversation-composer-constrained', constrained);
    }
    if (card) {
      layer = card.closest<HTMLElement>('.conversation-layer');
      const nextState = card.dataset.conversationState;
      if (layer && nextState !== cardState) {
        const previousState = cardState;
        cardState = nextState;
        const motion = conversationMotion(card);
        const from = motion.active(layer) ? layer.getBoundingClientRect().height : cardHeight;
        motion.cancel(layer);
        layer.style.removeProperty('height');
        const wanted = layer.getBoundingClientRect().height;
        if (previousState !== undefined && from > 0) {
          const movingLayer = layer;
          motion.move(layer, fraction => {
            movingLayer.style.height = `${from + (wanted - from) * fraction}px`;
          }, CONVERSATION_CARD_MS, () => movingLayer.style.removeProperty('height'));
        }
      }
      if (layer) cardHeight = layer.getBoundingClientRect().height;
      clearance = retainClearance(clearance, rect.bottom - card.getBoundingClientRect().top);
      set('--conversation-clearance', clearance);
    }
  }
  function schedule(): void { if (!frame) frame = requestAnimationFrame(measure); }
  const sizes = new ResizeObserver(schedule);
  const children = new MutationObserver(records => {
    // State CSS exposes the destination immediately. Measure before the next paint,
    // rather than show that destination for one frame before the move starts.
    if (records.some(record => record.type === 'attributes' && record.attributeName === 'data-conversation-state')) {
      cancelAnimationFrame(frame);
      measure();
    } else schedule();
  });
  sizes.observe(host);
  children.observe(host, { childList: true, subtree: true, attributes: true, attributeFilter: ["data-conversation-state"] });
  viewport?.addEventListener('resize', schedule);
  viewport?.addEventListener('scroll', schedule);
  window.addEventListener('resize', schedule);
  // Parent scroll can move a pane relative to the visible viewport without resizing it.
  window.addEventListener('scroll', schedule, true);
  schedule();
  return { destroy() {
    cancelAnimationFrame(frame);
    if (card && layer) conversationMotion(card).cancel(layer);
    sizes.disconnect(); children.disconnect();
    viewport?.removeEventListener('resize', schedule);
    viewport?.removeEventListener('scroll', schedule);
    window.removeEventListener('resize', schedule);
    window.removeEventListener('scroll', schedule, true);
  } };
}
