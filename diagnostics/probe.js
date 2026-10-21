// Conversation viewport probe. Runs before application code.
// Records, on one clock: structural mount and unmount, scroll position, the
// displacement of one chosen reading anchor, element height changes, page reads,
// live-stream open, close and events, and the composer's typed text.
(() => {
  const start = performance.now();
  const log = [];
  let nextId = 1;
  const ids = new WeakMap();
  const KINDS = [
    ["thread", "[data-conversation-thread]"],
    ["transcript", "[data-conversation-transcript]"],
    ["thread-content", "[data-conversation-content]"],
    ["composer-input", "[data-conversation-input]"],
    ["pane", "[data-conversation-pane]"],
    ["skeleton-shape", ".skeleton"]
  ];
  const SELECTOR = KINDS.map(pair => pair[1]).join(",");

  function kindOf(element) {
    for (const pair of KINDS) if (element.matches(pair[1])) return pair[0];
    return null;
  }
  function idOf(element) {
    let value = ids.get(element);
    if (value === undefined) { value = nextId++; ids.set(element, value); }
    return value;
  }
  function rec(type, data) {
    log.push(Object.assign({ t: Math.round((performance.now() - start) * 10) / 10, type }, data || {}));
  }
  function thread() { return document.querySelector("[data-conversation-thread]"); }
  function composer() { return document.querySelector("[data-conversation-input]"); }
  function transcript() { return document.querySelector("[data-conversation-transcript]"); }

  let anchor = null;
  let lastSignature = "";

  function sample(mark) {
    const element = thread();
    const input = composer();
    const view = transcript();
    const state = {
      scrollTop: element === null ? null : Math.round(element.scrollTop),
      scrollHeight: element === null ? null : Math.round(element.scrollHeight),
      clientHeight: element === null ? null : Math.round(element.clientHeight),
      threadNode: element === null ? null : idOf(element),
      transcriptNode: view === null ? null : idOf(view),
      composerNode: input === null ? null : idOf(input),
      typed: input === null ? null : input.value.length,
      jump: document.querySelector(".chat-jump") !== null,
      skeleton: document.querySelector(".skeleton") !== null,
      navError: document.querySelector("[data-chief-conversation-error]") !== null
    };
    if (anchor !== null && element !== null) {
      state.anchorHere = anchor.element.isConnected && element.contains(anchor.element);
      if (state.anchorHere) {
        const offset = anchor.element.getBoundingClientRect().top - element.getBoundingClientRect().top;
        state.anchorOffset = Math.round(offset);
        state.anchorDrift = Math.round(offset - anchor.offset);
      } else {
        state.anchorOffset = null;
        state.anchorDrift = null;
      }
    }
    const signature = JSON.stringify(state);
    if (signature !== lastSignature || mark !== undefined) {
      lastSignature = signature;
      rec("sample", mark === undefined ? state : Object.assign({ mark }, state));
    }
  }

  // What the chosen anchor is doing right now, for a caller that drives the gesture.
  window.__probeState = () => {
    const element = thread();
    if (anchor === null || element === null) return null;
    const here = anchor.element.isConnected && element.contains(anchor.element);
    return {
      here,
      key: anchor.key,
      offset: here ? anchor.element.getBoundingClientRect().top - element.getBoundingClientRect().top : null
    };
  };

  window.__probe = {
    mark: name => { rec("mark", { name }); sample(name); },
    dump: () => log,
    clear: () => { log.length = 0; },
    error: null
  };

  // One chosen reading anchor: the item the reader has at the top of the thread.
  window.__probeAnchor = () => {
    const element = thread();
    if (element === null) return null;
    const top = element.getBoundingClientRect().top;
    const found = [...element.querySelectorAll("[data-conversation-view-key]")]
      .find(node => node.getBoundingClientRect().bottom > top + 1);
    if (found === undefined) { anchor = null; return null; }
    anchor = {
      element: found,
      key: found.getAttribute("data-conversation-view-key"),
      offset: found.getBoundingClientRect().top - top
    };
    rec("anchor-chosen", {
      key: anchor.key,
      offset: Math.round(anchor.offset),
      scrollTop: Math.round(element.scrollTop)
    });
    return anchor.key;
  };

  // Page reads and other API traffic. No DOM needed, so patch immediately.
  const realFetch = window.fetch;
  window.fetch = function (input) {
    const url = String(typeof input === "string" ? input : (input && input.url) || "");
    const short = url.replace(/^https?:\/\/[^/]+/, "");
    const id = nextId++;
    const interesting = short.indexOf("/api/") === 0;
    if (interesting) rec("fetch-start", { req: id, url: short.slice(0, 220) });
    const result = realFetch.apply(this, arguments);
    if (interesting) result.then(
      response => rec("fetch-end", { req: id, status: response.status }),
      () => rec("fetch-abort", { req: id })
    );
    return result;
  };

  // The live stream: every open, every close, every event it delivers.
  const RealEventSource = window.EventSource;
  function Patched(url, config) {
    const source = new RealEventSource(url, config);
    const id = nextId++;
    rec("sse-open", { sse: id, url: String(url).replace(/^https?:\/\/[^/]+/, "").slice(0, 220) });
    const realAdd = source.addEventListener.bind(source);
    const seen = new Set();
    const watch = name => {
      if (seen.has(name)) return;
      seen.add(name);
      realAdd(name, event => rec("sse-event", {
        sse: id, name, bytes: event && typeof event.data === "string" ? event.data.length : 0
      }));
    };
    for (const name of ["snapshot", "changes", "reset", "error"]) watch(name);
    source.addEventListener = function (name, handler, options) {
      watch(name);
      return realAdd(name, handler, options);
    };
    const close = source.close.bind(source);
    source.close = function () { rec("sse-close", { sse: id }); return close(); };
    return source;
  }
  Patched.prototype = RealEventSource.prototype;
  Patched.CONNECTING = 0;
  Patched.OPEN = 1;
  Patched.CLOSED = 2;
  window.EventSource = Patched;

  function structural(root, event) {
    if (!(root instanceof Element)) return;
    const found = root.matches(SELECTOR) ? [root] : [];
    for (const element of root.querySelectorAll(SELECTOR)) found.push(element);
    for (const element of found) {
      const kind = kindOf(element);
      if (kind !== null) rec(event, { kind, node: idOf(element) });
    }
  }

  let sizes = null;
  let watchedThread = null;
  function watchSizes() {
    const element = thread();
    const content = document.querySelector("[data-conversation-content]");
    if (sizes !== null) { sizes.disconnect(); sizes = null; }
    if (element === null) return;
    sizes = new ResizeObserver(entries => {
      for (const entry of entries) rec("resize", {
        node: idOf(entry.target),
        kind: kindOf(entry.target),
        height: Math.round(entry.contentRect.height)
      });
    });
    sizes.observe(element);
    if (content !== null) sizes.observe(content);
  }

  let rows = null;
  let lastRowScan = 0;
  const watched = new WeakSet();
  const rowHeights = new WeakMap();
  function watchRows() {
    const element = thread();
    if (element === null) return;
    if (rows === null) {
      rows = new ResizeObserver(entries => {
        for (const entry of entries) {
          const node = entry.target;
          const height = Math.round(entry.contentRect.height);
          const before = rowHeights.get(node);
          if (before !== undefined && Math.abs(before - height) < 1) continue;
          rowHeights.set(node, height);
          rec("row", {
            key: (node.getAttribute("data-conversation-view-key") || "").slice(-24),
            node: idOf(node),
            height,
            was: before === undefined ? null : before,
            filled: node.children.length > 0
          });
        }
      });
    }
    for (const node of element.querySelectorAll("[data-conversation-view-key]")) {
      if (watched.has(node)) continue;
      watched.add(node);
      rows.observe(node);
    }
  }

  function install() {
    new MutationObserver(records => {
      for (const record of records) {
        for (const node of record.addedNodes) structural(node, "mount");
        for (const node of record.removedNodes) structural(node, "unmount");
      }
    }).observe(document.documentElement, { childList: true, subtree: true });

    document.addEventListener("visibilitychange",
      () => rec("visibility", { state: document.visibilityState }));

    const loop = () => {
      try {
        const element = thread();
        if (element !== watchedThread) { watchedThread = element; watchSizes(); }
        if (performance.now() - lastRowScan > 400) { lastRowScan = performance.now(); watchRows(); }
        sample();
      } catch (error) {
        window.__probe.error = String((error && error.stack) || error);
      }
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
  }

  function boot() {
    try { install(); } catch (error) { window.__probe.error = String((error && error.stack) || error); }
  }
  if (document.documentElement !== null && document.readyState !== "loading") boot();
  else document.addEventListener("DOMContentLoaded", boot, { once: true });
})();
