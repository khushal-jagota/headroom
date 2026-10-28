import { conversationMotion, CONVERSATION_CARD_MS } from '../surfaceMotion';
import { visibleHostBounds } from './geometry';

/** Reserve the dock row, and retain the reader's geometry beneath full conversation. */
export function conversationDock(host: HTMLElement): { destroy(): void } {
  let pane: HTMLElement | null = null;
  let composer: HTMLElement | null = null;
  let reader: HTMLElement | null = null;
  let layer: HTMLElement | null = null;
  let state: string | undefined;
  let lastHeight = 0;
  let readerHeight = 0;
  let frame = 0;
  let hovered = false;
  let mousePoint: { x: number; y: number } | null = null;
  // Remember the press before outside dismissal can return an open pane to rest.
  let backgroundPress: { id: number; x: number; y: number } | null = null;
  const viewport = window.visualViewport;
  function outsideInput(target: EventTarget | null): target is Element {
    return target instanceof Element && target.closest('[data-conversation-box]') === null;
  }
  function allowedBackground(target: EventTarget | null): target is Element {
    return outsideInput(target) && !!layer?.contains(target)
      && target.closest('button, a, input, textarea, select, [role="button"], [role="listbox"], [contenteditable="true"]') === null;
  }
  function press(event: PointerEvent): void {
    backgroundPress = event.isPrimary && event.button === 0
      && pane?.dataset.conversationState === 'rest' && allowedBackground(event.target)
      ? { id: event.pointerId, x: event.clientX, y: event.clientY } : null;
  }
  function movePress(event: PointerEvent): void {
    if (backgroundPress && (event.pointerId !== backgroundPress.id
      || event.clientX !== backgroundPress.x || event.clientY !== backgroundPress.y)) backgroundPress = null;
  }
  function cancelPress(): void { backgroundPress = null; }
  function activateBackground(event: MouseEvent): void {
    const origin = backgroundPress;
    backgroundPress = null;
    if (!origin || event.button !== 0 || pane?.dataset.conversationState !== 'rest'
      || !allowedBackground(event.target)) return;
    // Reuse the pane's action, including the mounted composer and focus behavior.
    pane.querySelector<HTMLButtonElement>('[data-conversation-expand]')?.click();
  }
  const set = (name: string, value: number) => {
    const text = `${value}px`;
    if (host.style.getPropertyValue(name) !== text) host.style.setProperty(name, text);
  };
  function shade(): string {
    const previewFocused = !!layer?.querySelector('[data-conversation-rest-bar]:focus-visible');
    return getComputedStyle(host).getPropertyValue(state === 'opened' || hovered || previewFocused ? '--surface-1' : '--surface-recessed').trim();
  }
  function pointer(event: PointerEvent): void {
    movePress(event);
    if (!layer) return;
    const wasHovered = hovered;
    mousePoint = event.pointerType === 'mouse' && event.type !== 'pointerleave' ? { x: event.clientX, y: event.clientY } : null;
    hovered = event.pointerType === 'mouse' && event.type !== 'pointerleave'
      && event.target instanceof Node && layer.contains(event.target)
      && outsideInput(event.target);
    host.toggleAttribute('data-conversation-dock-hover', hovered);
    // During motion its interpolation owns the shade. A later measurement retargets it.
    if (wasHovered !== hovered && (!pane || !conversationMotion(pane).active(layer))) layer.style.backgroundColor = shade();
  }
  function releaseReader(): void {
    if (!reader) return;
    reader.style.removeProperty('height');
    reader.style.removeProperty('flex');
    reader.inert = false;
    reader.removeAttribute('aria-hidden');
  }
  function measure(): void {
    frame = 0;
    const nextPane = host.querySelector<HTMLElement>('[data-conversation-pane]');
    if (nextPane !== pane) { if (pane) sizes.unobserve(pane); pane = nextPane; if (pane) sizes.observe(pane); }
    const nextComposer = host.querySelector<HTMLElement>('[data-conversation-composer]');
    if (nextComposer !== composer) { if (composer) sizes.unobserve(composer); composer = nextComposer; if (composer) sizes.observe(composer); }
    reader = host.querySelector<HTMLElement>('.ticket-reading, .manager-reading');
    layer = pane?.closest<HTMLElement>('.conversation-layer') ?? null;
    const rect = host.getBoundingClientRect();
    const bounds = visibleHostBounds(rect, { top: viewport?.offsetTop ?? 0, left: viewport?.offsetLeft ?? 0, width: viewport?.width ?? window.innerWidth, height: viewport?.height ?? window.innerHeight });
    set('--conversation-visible-top', bounds.top);
    set('--conversation-visible-bottom', bounds.bottom);
    set('--conversation-visible-left', bounds.left);
    set('--conversation-visible-right', bounds.right);
    set('--conversation-visible-height', bounds.height);
    if (pane && layer) {
      const nextState = pane.dataset.conversationState;
      if (nextState !== state) {
        const previousState = state;
        const motion = conversationMotion(pane);
        const from = motion.active(layer) ? layer.getBoundingClientRect().height : lastHeight;
        const fromShade = getComputedStyle(layer).backgroundColor;
        motion.cancel(layer);
        if (previousState !== undefined && reader && !reader.style.height) {
          reader.style.height = `${readerHeight || reader.getBoundingClientRect().height}px`;
          reader.style.flex = 'none';
        }
        state = nextState;
        host.dataset.conversationDockState = nextState ?? 'rest';
        if (reader) {
          reader.inert = nextState === 'opened';
          if (nextState === 'opened') reader.setAttribute('aria-hidden', 'true');
          else reader.removeAttribute('aria-hidden');
        }
        layer.style.removeProperty('height');
        const wanted = nextState === 'opened' ? bounds.height : layer.getBoundingClientRect().height;
        if (nextState !== 'opened') {
          const target = mousePoint ? document.elementFromPoint(mousePoint.x, mousePoint.y) : null;
          hovered = !!target && layer.contains(target) && outsideInput(target);
          host.toggleAttribute('data-conversation-dock-hover', hovered);
        }
        const toShade = shade();
        if (previousState !== undefined && from > 0) {
          // Keep it bottom anchored throughout return. Rejoining the row is the final step.
          host.setAttribute('data-conversation-dock-transition', '');
          const movingLayer = layer;
          const startColor = fromShade.match(/[\d.]+/g)?.slice(0, 3).map(Number) ?? [13, 13, 13];
          const colorProbe = document.createElement('span');
          colorProbe.style.color = toShade;
          host.append(colorProbe);
          const endColor = getComputedStyle(colorProbe).color.match(/[\d.]+/g)?.slice(0, 3).map(Number) ?? [21, 21, 21];
          colorProbe.remove();
          void motion.move(layer, fraction => {
            const eased = 1 - Math.pow(1 - fraction, 3);
            movingLayer.style.height = `${from + (wanted - from) * eased}px`;
            movingLayer.style.backgroundColor = `rgb(${startColor.map((channel, index) => channel + (endColor[index] - channel) * eased).join(',')})`;
          }, CONVERSATION_CARD_MS, () => {
            movingLayer.style.removeProperty('height');
            movingLayer.style.backgroundColor = shade();
            host.removeAttribute('data-conversation-dock-transition');
            if (state !== 'opened') {
              const target = mousePoint ? document.elementFromPoint(mousePoint.x, mousePoint.y) : null;
              hovered = !!target && movingLayer.contains(target) && outsideInput(target);
              host.toggleAttribute('data-conversation-dock-hover', hovered);
              movingLayer.style.backgroundColor = shade();
            }
            if (state !== 'opened') releaseReader();
            schedule();
          });
        } else {
          layer.style.backgroundColor = toShade;
          if (nextState !== 'opened') releaseReader();
        }
      }
      lastHeight = layer.getBoundingClientRect().height;
      if (!conversationMotion(pane).active(layer)) layer.style.backgroundColor = shade();
      if (reader && state !== 'opened' && !host.hasAttribute('data-conversation-dock-transition')) readerHeight = reader.getBoundingClientRect().height;
    }
    if (composer) {
      const popupRoom = Math.max(0, composer.getBoundingClientRect().top - rect.top - bounds.top);
      set('--conversation-popup-height', popupRoom);
      const targetHeight = parseFloat(getComputedStyle(pane ?? composer).getPropertyValue('--conversation-target')) || 0;
      const gap = parseFloat(getComputedStyle(host).getPropertyValue('--space-6')) || 0;
      const menuOpen = composer.querySelector('.chat-menu, .chat-add-menu, .listbox-picker-panel') !== null;
      composer.toggleAttribute('data-conversation-composer-constrained', composer.scrollHeight > composer.clientHeight + 1 || (menuOpen && popupRoom < targetHeight + gap));
    }
  }
  function schedule(): void { if (!frame) frame = requestAnimationFrame(measure); }
  const sizes = new ResizeObserver(schedule);
  const children = new MutationObserver(records => {
    if (records.some(record => record.type === 'attributes' && record.attributeName === 'data-conversation-state')) {
      cancelAnimationFrame(frame); measure();
    } else schedule();
  });
  sizes.observe(host);
  children.observe(host, { childList: true, subtree: true, attributes: true, attributeFilter: ['data-conversation-state'] });
  host.addEventListener('pointerdown', press, true);
  host.addEventListener('click', activateBackground);
  window.addEventListener('pointermove', movePress, true);
  window.addEventListener('pointerup', movePress, true);
  window.addEventListener('pointercancel', cancelPress, true);
  host.addEventListener('pointerover', pointer);
  host.addEventListener('pointermove', pointer);
  host.addEventListener('pointerleave', pointer);
  host.addEventListener('focusin', schedule);
  host.addEventListener('focusout', schedule);
  viewport?.addEventListener('resize', schedule);
  viewport?.addEventListener('scroll', schedule);
  window.addEventListener('resize', schedule);
  window.addEventListener('scroll', schedule, true);
  schedule();
  return { destroy() {
    cancelAnimationFrame(frame);
    if (pane && layer) conversationMotion(pane).cancel(layer);
    releaseReader();
    sizes.disconnect(); children.disconnect();
    host.removeEventListener('pointerdown', press, true);
    host.removeEventListener('click', activateBackground);
    window.removeEventListener('pointermove', movePress, true);
    window.removeEventListener('pointerup', movePress, true);
    window.removeEventListener('pointercancel', cancelPress, true);
    host.removeEventListener('pointerover', pointer);
    host.removeEventListener('pointermove', pointer);
    host.removeEventListener('pointerleave', pointer);
    host.removeEventListener('focusin', schedule);
    host.removeEventListener('focusout', schedule);
    viewport?.removeEventListener('resize', schedule);
    viewport?.removeEventListener('scroll', schedule);
    window.removeEventListener('resize', schedule);
    window.removeEventListener('scroll', schedule, true);
    delete host.dataset.conversationDockState;
    host.removeAttribute('data-conversation-dock-transition');
  } };
}
