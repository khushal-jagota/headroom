/** Resolve untrusted frame input before checking the fixed managed directory. */
export function resolveManagedHtmlNavigation(raw: string, currentHref: string, rootHref: string, origin: string): URL | null {
  if (raw.trim() !== raw || /[\\\u0000-\u001f\u007f]/.test(raw) || /%(?:2f|5c|25)/i.test(raw)) return null;
  try {
    const root = new URL(rootHref, origin);
    const candidate = new URL(raw, new URL(currentHref, origin));
    if (candidate.origin !== origin || candidate.username || candidate.password || candidate.search) return null;
    if (!/^https?:$/.test(candidate.protocol) || !/\.html?$/i.test(candidate.pathname)) return null;
    const rootSegments = decodeURIComponent(root.pathname).split('/').slice(0, -1);
    const candidateSegments = decodeURIComponent(candidate.pathname).split('/');
    if (candidateSegments.some(part => part === '.' || part === '..' || part.includes('\\'))) return null;
    if (rootSegments.some((part, index) => candidateSegments[index] !== part)) return null;
    if (candidateSegments.length <= rootSegments.length) return null;
    return candidate;
  } catch { return null; }
}

/** Runs before authored scripts. The parent treats every message field as untrusted. */
export function managedHtmlBridge(generation: string): string {
  return `(() => {
    const generation = ${JSON.stringify(generation)};
    const send = parent.postMessage.bind(parent);
    const intercept = event => {
      if (event.type === 'auxclick' && event.button !== 1) return;
      const anchor = event.composedPath().find(node => node instanceof Element && node.matches('a[href],area[href]'));
      if (!anchor) return;
      const href = anchor.getAttribute('href');
      if (href === '' || href === '#') return;
      event.preventDefault();
      event.stopImmediatePropagation();
      send({type:'panels-html-navigation', generation, href}, '*');
    };
    window.addEventListener('click', intercept, true);
    window.addEventListener('auxclick', intercept, true);
    window.addEventListener('message', event => {
      if (event.source !== parent || event.data?.type !== 'panels-html-fragment' || event.data.generation !== generation) return;
      let fragment;
      try { fragment = decodeURIComponent(event.data.hash.slice(1)); } catch { return; }
      if (!fragment) { window.scrollTo(0, 0); return; }
      const target = document.getElementById(fragment) || Array.from(document.getElementsByName(fragment))[0];
      target?.scrollIntoView();
    });
  })();`;
}
