import { VISIBLE_CONTENT_INACTIVE_LIMIT, VISIBLE_CONTENT_ROOT_MARGIN, VISIBLE_CONTENT_INITIAL_HEIGHT } from './visibleContent/config';

type Lease = {
  element: HTMLElement;
  release(): void;
  inactive(): boolean;
  inactiveOrder(): number;
};
type Scope = { leases: Set<Lease>; inactive: Set<Lease>; clock: number };
const scopes = new WeakMap<Document, Scope>();
const geometry = new WeakMap<HTMLElement, { hold(): void; reset(): void }>();

/** Keep measured geometry while a mounted view replaces asynchronous content. */
export function holdVisibleContentHeight(element: HTMLElement): void { geometry.get(element)?.hold(); }
/** A new identity or width cannot reuse geometry from the previous content. */
export function resetVisibleContentHeight(element: HTMLElement): void { geometry.get(element)?.reset(); }

/** Keep a bounded number of inactive mounted views, without a detached data cache.
 * Visibility admits a view once. Only eviction or owner destruction releases it.
 */
export function observeVisibleContent(
  element: HTMLElement,
  update: (visible: boolean, measuredHeight: number) => void,
  options: { canRetain?: () => boolean; initiallyMounted?: boolean } = {}
): () => void {
  const owner = element.ownerDocument;
  let scope = scopes.get(owner);
  if (!scope) { scope = { leases: new Set(), inactive: new Set(), clock: 0 }; scopes.set(owner, scope); }
  const retention = scope;
  const root = element.closest('[data-conversation-thread]');
  let intersecting = false;
  let admitted = options.initiallyMounted ?? false;
  let destroyed = false;
  let height = 0;
  let demanded = false;
  let inactiveOrder = 0;
  let width = element.getBoundingClientRect().width;
  let failed = false;
  let reserved = false;
  let originalMinHeight = '';
  let originalHeight = '';
  let originalMaxHeight = '';
  let originalOverflow = '';
  let frame = 0;
  const loadedFrames = new WeakSet<HTMLIFrameElement>();

  function stopFrame() { cancelAnimationFrame(frame); frame = 0; }
  function reserve() {
    if (height <= 0) return;
    if (!reserved) {
      originalMinHeight = element.style.minHeight;
      originalHeight = element.style.height;
      originalMaxHeight = element.style.maxHeight;
      originalOverflow = element.style.overflow;
    }
    reserved = true;
    const style = getComputedStyle(element);
    const inset = style.boxSizing === 'border-box' ? 0 :
      parseFloat(style.paddingTop) + parseFloat(style.paddingBottom) +
      parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth);
    const held = `${Math.max(0, height - inset)}px`;
    element.style.minHeight = held;
    // A minimum alone still lets a generic document skeleton enlarge a short
    // loaded preview. Constrain both directions until actual content is ready.
    element.style.height = held;
    element.style.maxHeight = held;
    element.style.overflow = 'hidden';
  }
  function contentReady() {
    if (element.matches('img') && !(element as HTMLImageElement).complete) return false;
    if (element.querySelector('[aria-busy="true"], [data-visible-content-restoring]')) return false;
    if ([...element.querySelectorAll('img')].some(image => !image.complete)) return false;
    if ([...element.querySelectorAll('iframe')].some(child => !loadedFrames.has(child))) return false;
    return true;
  }
  function reset() {
    if (reserved) {
      element.style.minHeight = originalMinHeight;
      element.style.height = originalHeight;
      element.style.maxHeight = originalMaxHeight;
      element.style.overflow = originalOverflow;
    }
    reserved = false;
    delete element.dataset.visibleContentRestoring;
  }
  function ready() {
    if (!admitted || destroyed || !reserved || !contentReady()) return;
    reset();
  }
  geometry.set(element, { reset, hold() {
    if (!reserved) height = element.getBoundingClientRect().height;
    reserve();
    if (admitted && reserved) element.dataset.visibleContentRestoring = '';
    scheduleReady();
  } });
  function scheduleReady() {
    if (!reserved || !admitted || destroyed) return;
    stopFrame();
    // Give mounted actions and IntersectionObserver admissions a frame before
    // removing geometry. Markdown can insert its first visible groups there.
    frame = requestAnimationFrame(() => { frame = requestAnimationFrame(() => { frame = 0; ready(); }); });
  }
  function loaded(event: Event) {
    if (event.type === 'error') failed = true;
    if (event.target instanceof HTMLIFrameElement) loadedFrames.add(event.target);
    scheduleReady();
  }
  const mutations = new MutationObserver(records => {
    for (const record of records) {
      if (record.type === 'attributes' && (record.attributeName === 'src' || record.attributeName === 'srcdoc')) {
        failed = false;
        if (record.target instanceof HTMLIFrameElement) loadedFrames.delete(record.target);
      }
    }
    scheduleReady();
  });
  mutations.observe(element, { childList: true, subtree: true, attributes: true,
    attributeFilter: ['aria-busy', 'src', 'srcdoc', 'data-visible-content-restoring'] });
  element.addEventListener('load', loaded, true);
  element.addEventListener('error', loaded, true);

  const lease: Lease = { element, release,
    inactive: () => admitted && !demanded,
    inactiveOrder: () => inactiveOrder
  };
  function rebalance() {
    // One dormant ancestor owns its entire subtree. Child observers cannot make
    // one document consume the whole inactive budget by themselves.
    const inactive = [...retention.leases].filter(entry => entry.inactive());
    const roots = inactive.filter(entry => !entry.element.parentElement?.closest('[data-visible-content-state="retained"]'));
    retention.inactive = new Set(roots.sort((a, b) => a.inactiveOrder() - b.inactiveOrder()));
    while (retention.inactive.size > VISIBLE_CONTENT_INACTIVE_LIMIT) {
      retention.inactive.values().next().value?.release();
    }
  }
  retention.leases.add(lease);
  element.dataset.visibleContentState = admitted ? 'retained' : 'released';
  if (admitted) {
    // A mounted view can gain visibility management after a size threshold
    // changes. Adopt its ownership before the first asynchronous observation.
    inactiveOrder = ++retention.clock;
    for (const child of element.querySelectorAll('iframe')) loadedFrames.add(child);
    rebalance();
  }
  if (element.getBoundingClientRect().height === 0) { height = VISIBLE_CONTENT_INITIAL_HEIGHT; reserve(); }
  function release() {
    retention.inactive.delete(lease);
    if (!admitted || destroyed) return;
    // Capture before the consumer removes its children. ResizeObserver can lag
    // a visibility notification by one frame.
    const measured = element.getBoundingClientRect().height;
    if (!reserved && measured > 0) height = measured;
    reserve();
    admitted = false;
    stopFrame();
    delete element.dataset.visibleContentRestoring;
    element.dataset.visibleContentState = 'released';
    // Svelte teardown is asynchronous. Release descendants now, so they cannot
    // retain native resources outside the budget until that teardown runs.
    for (const child of [...retention.leases]) {
      if (child !== lease && element.contains(child.element)) child.release();
    }
    update(false, height);
  }
  function publish() {
    if (destroyed) return;
    const ancestorReleased = element.parentElement?.closest('[data-visible-content-state="released"]');
    const wasDemanded = demanded;
    demanded = intersecting && owner.visibilityState !== 'hidden' && !ancestorReleased;
    if (demanded) {
      retention.inactive.delete(lease);
      element.dataset.visibleContentState = 'active';
      if (!admitted) {
        admitted = true;
        if (reserved) element.dataset.visibleContentRestoring = '';
        update(true, height);
        scheduleReady();
      }
    } else if (admitted) {
      if (ancestorReleased || failed || !contentReady() || options.canRetain?.() === false || element.querySelector('[data-visible-content-failed]')) {
        release();
      } else {
        if (wasDemanded) inactiveOrder = ++retention.clock;
        element.dataset.visibleContentState = 'retained';
      }
    }
    rebalance();
  }
  const resize = new ResizeObserver(entries => {
    const nextWidth = element.getBoundingClientRect().width;
    if (reserved && Math.abs(nextWidth - width) > 1) {
      reset(); height = 0;
      width = nextWidth;
      // This entry describes the constrained box from before reset. Let the
      // next observation measure the content at its new width.
      return;
    }
    width = nextWidth;
    const next = entries[0]?.borderBoxSize[0]?.blockSize ?? element.getBoundingClientRect().height;
    if (next > 0 && !reserved) height = next;
  });
  const intersection = new IntersectionObserver(entries => {
    intersecting = entries[0]?.isIntersecting ?? false;
    publish();
  }, { root, rootMargin: VISIBLE_CONTENT_ROOT_MARGIN });
  resize.observe(element);
  intersection.observe(element);
  owner.addEventListener('visibilitychange', publish);
  return () => {
    destroyed = true;
    retention.inactive.delete(lease);
    retention.leases.delete(lease);
    stopFrame();
    resize.disconnect();
    intersection.disconnect();
    mutations.disconnect();
    element.removeEventListener('load', loaded, true);
    element.removeEventListener('error', loaded, true);
    owner.removeEventListener('visibilitychange', publish);
    reset();
    geometry.delete(element);
    delete element.dataset.visibleContentState;
  };
}
