/** Build a managed file address only on the server's separate artifact origin. */
export function artifactPageUrl(origin: string | null, href: string, panelsOrigin: string): string {
  if (!origin) throw new Error("HTML pages are unavailable because the artifact origin is not configured.");
  const artifactOrigin = new URL(origin);
  if (!['http:', 'https:'].includes(artifactOrigin.protocol) || artifactOrigin.origin === panelsOrigin ||
      artifactOrigin.username || artifactOrigin.password || artifactOrigin.pathname !== '/' ||
      artifactOrigin.search || artifactOrigin.hash) {
    throw new Error("The artifact origin is invalid.");
  }
  const file = new URL(href, panelsOrigin);
  if (file.origin !== panelsOrigin || !file.pathname.startsWith('/files/')) {
    throw new Error("The artifact file address is invalid.");
  }
  return `${artifactOrigin.origin}${file.pathname}${file.search}${file.hash}`;
}
