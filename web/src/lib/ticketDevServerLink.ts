const LOOPBACK_HTTP_URL =
  /^http:\/\/(?:localhost|127\.0\.0\.1):([0-9]+)([/?#].*)?$/i;

/** Give a loopback dev-server URL the Ticket-scoped ingress address it can be opened at. */
export function ticketDevServerHref(href: string, ticketId: string): string {
  const match = LOOPBACK_HTTP_URL.exec(href);
  if (match === null || ticketId.length === 0) return href;

  const port = Number(match[1]);
  if (!Number.isInteger(port) || port < 1 || port > 65_535) return href;

  const matchedSuffix = match[2];
  const suffix = matchedSuffix === undefined
    ? "/"
    : matchedSuffix.startsWith("/")
      ? matchedSuffix
      : `/${matchedSuffix}`;
  return `/dev/tickets/${encodeURIComponent(ticketId)}/${port}${suffix}`;
}
