// One observed wheel operation owns completion and the callback that consumes it.
const close = (a, b) => Math.abs(a - b) <= .5;
const position = g => g.top - g.origin;
const sameGeometry = (a, b) => ['top', 'origin', 'extent', 'height', 'padding', 'room', 'anchor', 'left', 'width'].every(key => close(a[key], b[key]));
const lineConsistent = (a, b) => close(a.anchor - b.anchor, a.origin - b.origin - (a.top - b.top));
const geometryValid = g => g && ['top', 'origin', 'extent', 'height', 'padding', 'room', 'anchor', 'left', 'width'].every(key => Number.isFinite(g[key]));

export function createWheelAdmission({ token, thread, decide = wheelCompletionDecision }) {
  const state = {token, thread, receipts: [], start: null, failure: null, consumed: false,
    displaced: false, completion: null, intervals: 0, last: null};
  return {
    state,
    receive(receipt) { state.receipts.push(receipt); decide(state, receipt); return state; },
    consume(fresh, callback) {
      this.receive(fresh);
      if (state.failure || state.consumed || !state.completion || state.intervals < 2) return false;
      if (!sameGeometry(fresh.geometry, state.last)) return false;
      state.consumed = true;
      state.receipts.push({...fresh, kind: 'baseline'});
      callback(fresh.geometry);
      return true;
    },
  };
}

export function wheelCompletionDecision(s, r) {
  if (s.failure || s.consumed) return;
  const fail = reason => { s.failure = {reason, receipt: r}; };
  if (r.token !== s.token || r.thread !== s.thread || r.same === false) return fail('SETUP_WHEEL_IDENTITY');
  if (r.kind === 'input') return fail('SETUP_COMPETING_INPUT');
  if (r.kind === 'wheel') {
    if (s.wheel || !r.trusted || r.deltaMode !== 0 || !r.delta || r.deltaX) return fail('SETUP_WHEEL_INPUT');
    if (!geometryValid(r.geometry)) return fail('SETUP_WHEEL_GEOMETRY');
    s.wheel = r;
    s.dispatchGeometry = r.geometry;
    s.last = r.geometry;
    s.deadline = r.ms + 5000;
    return;
  }
  if (!s.wheel) return;
  if (r.ms >= s.deadline) return fail('SETUP_NATIVE_WHEEL_DEADLINE');
  if (r.kind === 'post-handler') {
    if (s.start || r.prevented || !r.preDefault || !geometryValid(r.geometry) || !sameGeometry(r.geometry, s.dispatchGeometry)) return fail('SETUP_WHEEL_DISPATCH_TAIL');
    if (s.inset && !close(r.geometry.anchor, s.inset.before.anchor)) return fail('SETUP_WHEEL_DISPATCH_TAIL');
    s.inset = null;
    s.start = r.geometry;
    s.expected = Math.max(0, Math.min(s.start.extent - s.start.height, s.start.top + s.wheel.delta)) - s.start.origin;
    if (close(s.expected, position(s.start))) return fail('SETUP_WHEEL_BOUNDARY_NOOP');
    s.last = s.start;
    return;
  }
  if (!s.start && r.kind !== 'style' && r.kind !== 'write') return;
  const g = r.geometry;
  if (!geometryValid(g)) return fail('SETUP_WHEEL_GEOMETRY');
  if (r.kind === 'style') {
    const b = r.before;
    if (!geometryValid(b)) return fail('SETUP_WHEEL_GEOMETRY');
    if (!lineConsistent(b, s.last)) return fail('BEHAVIOR_WHEEL_CANCELLATION_ANCHOR');
    // Insets can change content coordinates; they cannot move the selected line.
    // Tail retirement changes only the extent. Integer extents round CSS fractions.
    const inset = g.origin - b.origin, padding = g.padding - b.padding;
    const correction = !close(inset, 0) && close(inset, padding)
      && close(g.anchor - b.anchor, inset - (g.top - b.top));
    const tailOnly = close(inset, 0) && close(g.top, b.top) && close(g.anchor, b.anchor);
    if ((!s.start && !sameGeometry(b, s.dispatchGeometry)) || !close(g.height, b.height) || !close(g.left, b.left) || !close(g.width, b.width)
        || Math.abs((g.extent - b.extent) - (g.room - b.room) - padding) > 1
        || !(correction || tailOnly)) return fail('BEHAVIOR_WHEEL_CANCELLATION_GEOMETRY');
    if (!close(inset, 0)) s.inset = {before: b, after: g};
    s.last = g;
    if (!s.start) s.dispatchGeometry = g;
    if (s.completion && !sameGeometry(g, b)) { s.completion = null; s.intervals = 0; }
    return;
  }
  if (r.kind === 'write') {
    // Explicit no-op writes are observable but cannot count as native movement.
    // An inset correction needs the immediately preceding measured style transaction.
    const correction = s.inset;
    const b = r.before;
    if (r.method && r.method !== 'scrollTop') return fail('BEHAVIOR_WHEEL_CANCELLATION_WRITE');
    if (!geometryValid(b) || !lineConsistent(b, s.last) || (!s.start && !sameGeometry(b, s.dispatchGeometry)) || (!sameGeometry(g, b) && !(correction
        && close(g.anchor, correction.before.anchor)
        && close(g.top - correction.before.top, g.origin - correction.before.origin)
        && !close(g.origin, correction.before.origin)))) return fail('BEHAVIOR_WHEEL_CANCELLATION_WRITE');
    s.inset = null;
    s.last = g;
    if (!s.start) s.dispatchGeometry = g;
    return;
  }
  if (s.inset && !close(g.anchor, s.inset.before.anchor)) return fail('BEHAVIOR_WHEEL_CANCELLATION_INSET');
  s.inset = null;
  if (!close(g.origin, s.last.origin) || !close(g.extent, s.last.extent)
      || !close(g.padding, s.last.padding) || !close(g.room, s.last.room)
      || !close(g.height, s.last.height) || !close(g.left, s.last.left) || !close(g.width, s.last.width)) return fail('SETUP_UNEXPLAINED_WHEEL_GEOMETRY');
  if (!lineConsistent(g, s.last)) return fail('BEHAVIOR_WHEEL_CANCELLATION_ANCHOR');
  const moved = Math.abs(position(g) - position(s.start)) > .5;
  const atDestination = moved && close(position(g), s.expected);
  if (s.completion && !sameGeometry(g, s.completion)) return fail('BEHAVIOR_WHEEL_CANCELLATION_AFTER_COMPLETION');
  // Wrong-direction travel is automatic motion that the wheel must cancel.
  if (!close(g.top, s.last.top) && (position(g) - position(s.start)) * s.wheel.delta < 0) return fail('BEHAVIOR_WHEEL_CANCELLATION_TRAVEL');
  if (r.kind === 'scroll' && r.trusted && atDestination) s.displaced = true;
  if (s.displaced && !atDestination) return fail('BEHAVIOR_WHEEL_CANCELLATION_TRAVEL');
  if (r.kind === 'scrollend' && r.trusted && s.displaced && atDestination) {
    s.completion = g; s.intervals = 0; s.frameSeen = false;
  }
  if (r.kind === 'frame' && s.completion) {
    if (s.frameSeen) s.intervals += 1;
    s.frameSeen = true;
  }
  s.last = g;
}

export function observeNativeWheel(thread) {
  const token = `wheel-${performance.now()}`;
  const admission = createWheelAdmission({token, thread: token});
  const content = thread.querySelector('[data-conversation-content]');
  let anchor = null, active = false, stopped = false, nesting = 0;
  const restore = [], listeners = [];
  const geometry = () => {
    const bounds = thread.getBoundingClientRect(), css = getComputedStyle(thread);
    const room = thread.querySelector('[data-conversation-reserved-space]');
    return {top: thread.scrollTop, origin: content.getBoundingClientRect().top - bounds.top + thread.scrollTop,
      extent: thread.scrollHeight, height: thread.clientHeight, padding: parseFloat(css.paddingTop),
      room: room?.getBoundingClientRect().height ?? 0, anchor: anchor?.getBoundingClientRect().top - bounds.top,
      left: bounds.left, width: bounds.width};
  };
  const receive = (kind, extra = {}) => admission.receive({kind, token, thread: token, ms: performance.now(),
    same: thread.isConnected && document.querySelector('[data-conversation-thread]') === thread
      && (!active || anchor?.isConnected && content.contains(anchor)), geometry: geometry(), ...extra});
  const listen = (node, type, callback, options) => { node.addEventListener(type, callback, options); listeners.push(() => node.removeEventListener(type, callback, options)); };
  const patch = (object, key, descriptor) => {
    const own = Object.getOwnPropertyDescriptor(object, key);
    Object.defineProperty(object, key, {...descriptor, configurable: true});
    restore.push(() => own ? Object.defineProperty(object, key, own) : delete object[key]);
  };
  const descriptor = (object, key) => {
    for (let p = object; p; p = Object.getPrototypeOf(p)) { const d = Object.getOwnPropertyDescriptor(p, key); if (d) return d; }
  };
  const around = (kind, operation, extra = {}) => {
    if (!active || stopped || nesting) return operation();
    const before = geometry();
    nesting++;
    try { return operation(); } finally { nesting--; receive(kind, {before, ...extra}); }
  };
  const relevantStyle = style => style === thread.style || style === content.style
    || style === thread.querySelector('[data-conversation-reserved-space]')?.style;
  try {
    const scrollTop = descriptor(thread, 'scrollTop');
    patch(thread, 'scrollTop', {get() { return scrollTop.get.call(this); },
      set(value) { around('write', () => scrollTop.set.call(this, value), {method: 'scrollTop', value}); }});
    for (const key of ['scroll', 'scrollTo', 'scrollBy']) {
      const method = thread[key];
      patch(thread, key, {value: function(...args) { return around('write', () => method.apply(this, args), {method: key, args}); }, writable: true});
    }
    // CSS setters and methods run synchronously around the actual mutation. A
    // MutationObserver runs too late to recover the previous anchor geometry.
    const prototype = CSSStyleDeclaration.prototype;
    const nativeGet = prototype.getPropertyValue, nativeSet = prototype.setProperty;
    const styles = [thread.style, content.style, thread.querySelector('[data-conversation-reserved-space]')?.style].filter(Boolean);
    for (const style of styles) {
      for (const key of ['paddingTop', 'paddingBottom', 'padding', 'height', 'marginTop']) {
        // Chromium exposes these named CSS properties as dynamic own data
        // descriptors. Deletion restores that descriptor at its CURRENT value.
        const cssKey = key.replace(/[A-Z]/g, letter => `-${letter.toLowerCase()}`);
        Object.defineProperty(style, key, {configurable: true, enumerable: true,
          get() { return nativeGet.call(this, cssKey); },
          set(value) { return around('style', () => nativeSet.call(this, cssKey, value)); }});
        restore.push(() => { delete style[key]; });
      }
      const d = descriptor(style, 'cssText');
      patch(style, 'cssText', {...d, set(value) { return around('style', () => d.set.call(this, value)); }});
    }
    for (const key of ['setProperty', 'removeProperty']) {
      const method = prototype[key];
      patch(prototype, key, {value: function(...args) {
        if (!relevantStyle(this)) return method.apply(this, args);
        return around('style', () => method.apply(this, args));
      }, writable: true});
    }
    listen(document, 'wheel', event => {
      if (!thread.contains(event.target)) { if (active) receive('input'); return; }
      if (active) { receive('input'); return; }
      const bounds = thread.getBoundingClientRect();
      anchor = [...content.querySelectorAll('[data-conversation-view-key], [data-conversation-source-key], [data-conversation-outgoing]')]
        .find(node => node.getBoundingClientRect().bottom > bounds.top + 24 && node.getBoundingClientRect().top < bounds.bottom);
      active = true;
      receive('wheel', {trusted: event.isTrusted, delta: event.deltaY, deltaX: event.deltaX, deltaMode: event.deltaMode,
        target: event.target.tagName, cancelable: event.cancelable});
    }, true);
    listen(document, 'wheel', event => {
      if (!active || !thread.contains(event.target)) return;
      // The fixture's Svelte handler is a direct thread listener. This document
      // bubble receipt runs after it and inside dispatch, before native default.
      receive('post-handler', {prevented: event.defaultPrevented, cancelable: event.cancelable,
        preDefault: event.eventPhase === Event.BUBBLING_PHASE && event.currentTarget === document,
        owner: thread.dataset.conversationPositionOwner});
    }, {passive: false});
    for (const kind of ['pointerdown', 'touchstart', 'touchmove', 'keydown']) listen(document, kind, event => {
      if (active && (kind !== 'keydown' || ['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].includes(event.key))) receive('input');
    }, true);
    for (const kind of ['scroll', 'scrollend']) listen(thread, kind, event => {
      if (active && event.target === thread) receive(kind, {trusted: event.isTrusted});
    });
  } catch (error) { cleanup(); throw error; }
  function cleanup() {
    if (stopped) return;
    stopped = true;
    listeners.reverse().forEach(undo => undo());
    restore.reverse().forEach(undo => undo());
  }
  const frame = () => { if (stopped) return; if (active) receive('frame'); requestAnimationFrame(frame); };
  requestAnimationFrame(frame);
  return {admission, cleanup, status() { if (active) receive('query'); return {failure: admission.state.failure,
    ready: !!admission.state.completion && admission.state.intervals >= 2, receipts: admission.state.receipts}; },
    consume(callback) {
      const fresh = {kind: 'consume', token, thread: token, ms: performance.now(), geometry: geometry(), same: thread.isConnected && thread === document.querySelector('[data-conversation-thread]') && anchor?.isConnected && content.contains(anchor)};
      let value;
      try {
        if (!admission.consume(fresh, () => { value = callback(); })) throw new Error(JSON.stringify(this.status()));
        return value;
      } finally { cleanup(); }
    }};
}
