export type FilePreviewTarget =
  | { kind: "ticket-file"; ticketId: string; path: string }
  | { kind: "sprint-item-file"; sprintItemId: string; path: string }
  | { kind: "external-link"; href: string; label?: string };

export type FilePreviewKind =
  | "markdown"
  | "image"
  | "video"
  | "audio"
  | "html"
  | "download"
  | "external";

export type ResolvedPreview = {
  kind: FilePreviewKind;
  target: FilePreviewTarget;
  href: string;
  label: string;
  previewHref?: string;
  displayHref?: string;
};

export const MANAGED_HTML_PREVIEW_SANDBOX = "allow-scripts";

const KIND_BY_EXTENSION = new Map<string, FilePreviewKind>([
  ["md", "markdown"],
  ["markdown", "markdown"],
  ["htm", "html"],
  ["html", "html"],
  ["avif", "image"],
  ["bmp", "image"],
  ["gif", "image"],
  ["jpeg", "image"],
  ["jpg", "image"],
  ["png", "image"],
  ["svg", "image"],
  ["webp", "image"],
  ["m4v", "video"],
  ["mov", "video"],
  ["mp4", "video"],
  ["ogv", "video"],
  ["webm", "video"],
  ["aac", "audio"],
  ["flac", "audio"],
  ["m4a", "audio"],
  ["mp3", "audio"],
  ["oga", "audio"],
  ["ogg", "audio"],
  ["opus", "audio"],
  ["wav", "audio"]
]);
const KINDS_RENDERED_FROM_URL = new Set<FilePreviewKind>(["image", "video", "audio"]);
const TICKET_ID_RE = /^t_[a-z0-9]+$/;
const TICKET_DEV_SERVER_PREFIX = "/dev/tickets/";
const PORT_RE = /^[0-9]{1,5}$/;
const RESIDUAL_UNSAFE_RE = /%(?:25|2e|2f|5c)/i;
const MAX_MARKDOWN_EMBED_DEPTH = 2;

export function resolvePreview(target: FilePreviewTarget): ResolvedPreview {
  if (target.kind === "external-link") {
    const label = target.label || target.href;
    const kind = KIND_BY_EXTENSION.get(extensionFor(rawPathnameFromHref(target.href)));
    if (kind && KINDS_RENDERED_FROM_URL.has(kind)) {
      return { kind, target, href: target.href, label };
    }
    return {
      kind: "external",
      target,
      href: target.href,
      label,
      displayHref: displayHrefForExternal(target.href)
    };
  }

  if (target.kind === "ticket-file" && !ticketFileTarget(target.ticketId, target.path)) {
    throw new Error("unsafe ticket file target");
  }
  if (
    target.kind === "sprint-item-file" &&
    !sprintItemFileTarget(target.sprintItemId, target.path)
  ) {
    throw new Error("unsafe Sprint Item file target");
  }
  return {
    kind: KIND_BY_EXTENSION.get(extensionFor(target.path)) || "download",
    target,
    href: managedFileHref(target),
    label: filenameLabel(target.path),
    previewHref: previewHashHref(target)
  };
}

export function prepareManagedHtmlPreviewDocument(html: string, managedHtmlHref: string): string {
  const baseHref = absoluteHrefForManagedHtml(managedHtmlHref);
  const document = parseManagedHtmlDocument(html);
  const baseElement = document.createElement("base");
  baseElement.setAttribute("href", baseHref);
  document.head.insertBefore(baseElement, document.head.firstChild);
  return serializeManagedHtmlDocument(document);
}

export function markdownExpansionFor(
  resolved: ResolvedPreview,
  depth: number,
  visited: string[]
): { expandable: boolean; nextDepth: number; nextVisited: string[] } {
  if (resolved.kind !== "markdown") {
    return { expandable: false, nextDepth: depth, nextVisited: visited };
  }
  if (depth >= MAX_MARKDOWN_EMBED_DEPTH || visited.includes(resolved.href)) {
    return { expandable: false, nextDepth: depth, nextVisited: visited };
  }
  return { expandable: true, nextDepth: depth + 1, nextVisited: [...visited, resolved.href] };
}

export function targetFromHref(href: string, label = ""): FilePreviewTarget {
  const ticketTarget = ticketFileTargetFromHref(href);
  if (ticketTarget) return ticketTarget;
  const sprintItemTarget = sprintItemFileTargetFromHref(href);
  if (sprintItemTarget) return sprintItemTarget;
  return { kind: "external-link", href, label: label || href };
}

export function ticketFileTarget(
  ticketId: string,
  path: string
): Extract<FilePreviewTarget, { kind: "ticket-file" }> | null {
  if (!TICKET_ID_RE.test(ticketId) || !safeManagedPath(path)) return null;
  return { kind: "ticket-file", ticketId, path };
}

export function sprintItemFileTarget(
  sprintItemId: string,
  path: string
): Extract<FilePreviewTarget, { kind: "sprint-item-file" }> | null {
  if (!/^si_[a-z0-9]+$/.test(sprintItemId) || !safeManagedPath(path)) return null;
  return { kind: "sprint-item-file", sprintItemId, path };
}

export function previewHashHref(
  target: Extract<FilePreviewTarget, { kind: "ticket-file" | "sprint-item-file" }>
): string {
  if (target.kind === "sprint-item-file") {
    return (
      "#/preview?source=sprint-item" +
      `&item=${encodeURIComponent(target.sprintItemId)}` +
      `&path=${encodeURIComponent(target.path)}`
    );
  }
  return (
    "#/preview?source=ticket" +
    `&ticket=${encodeURIComponent(target.ticketId)}` +
    `&path=${encodeURIComponent(target.path)}`
  );
}

function managedFileHref(
  target: Extract<FilePreviewTarget, { kind: "ticket-file" | "sprint-item-file" }>
): string {
  return target.kind === "ticket-file" ? ticketFileHref(target) : sprintItemFileHref(target);
}

export function ticketFileHref(target: Extract<FilePreviewTarget, { kind: "ticket-file" }>): string {
  const path = encodedManagedPath(target.path);
  return `/files/tickets/${encodeURIComponent(target.ticketId)}/${path}`;
}

export function sprintItemFileHref(
  target: Extract<FilePreviewTarget, { kind: "sprint-item-file" }>
): string {
  const path = encodedManagedPath(target.path);
  return `/files/sprint-items/${encodeURIComponent(target.sprintItemId)}/${path}`;
}

function ticketFileTargetFromHref(href: string): FilePreviewTarget | null {
  const parts = managedHrefParts(href, "/files/tickets/");
  if (!parts) return null;
  return ticketFileTarget(parts.entityId, parts.path);
}

function sprintItemFileTargetFromHref(href: string): FilePreviewTarget | null {
  const parts = managedHrefParts(href, "/files/sprint-items/");
  if (!parts) return null;
  return sprintItemFileTarget(parts.entityId, parts.path);
}

/**
 * Recognize the Ticket-scoped address Panels proxies to a live dev server, so a
 * recorded preview link is claimed like the managed-file links it sits beside. The
 * address is a Panels route, not a file, so it stays an ordinary external target.
 */
export function isTicketDevServerHref(href: string): boolean {
  if (!isSameOriginHref(href)) return false;
  const pathname = rawPathnameFromHref(href);
  if (!pathname.startsWith(TICKET_DEV_SERVER_PREFIX)) return false;
  const [ticketSegment, portSegment] = pathname
    .slice(TICKET_DEV_SERVER_PREFIX.length)
    .split("/", 2);
  if (portSegment === undefined || !PORT_RE.test(portSegment)) return false;
  const port = Number(portSegment);
  if (port < 1 || port > 65_535) return false;
  try {
    return TICKET_ID_RE.test(decodeURIComponent(ticketSegment));
  } catch {
    return false;
  }
}

function isSameOriginHref(href: string): boolean {
  const localOrigin = typeof window === "undefined" ? "http://planner.local" : window.location.origin;
  try {
    return new URL(href, localOrigin).origin === localOrigin;
  } catch {
    return false;
  }
}

function managedHrefParts(href: string, prefix: string): { entityId: string; path: string } | null {
  if (!isSameOriginHref(href)) return null;
  const pathname = rawPathnameFromHref(href);
  if (!pathname.startsWith(prefix)) return null;
  const rest = pathname.slice(prefix.length);
  const slash = rest.indexOf("/");
  if (slash <= 0 || slash === rest.length - 1) return null;
  try {
    return {
      entityId: decodeURIComponent(rest.slice(0, slash)),
      path: decodeURIComponent(rest.slice(slash + 1))
    };
  } catch {
    return null;
  }
}

function safeManagedPath(path: string): boolean {
  if (!path || path.startsWith("/") || path.includes("\\") || RESIDUAL_UNSAFE_RE.test(path)) {
    return false;
  }
  return !path.split("/").some((segment) => !segment || segment === "." || segment === "..");
}

function encodedManagedPath(path: string): string {
  return path
    .split("/")
    .map((segment) => encodeURIComponent(segment))
    .join("/");
}

function rawPathnameFromHref(href: string): string {
  const withoutFragment = href.split("#", 1)[0];
  const withoutQuery = withoutFragment.split("?", 1)[0];
  if (withoutQuery.startsWith("//")) {
    const pathStart = withoutQuery.indexOf("/", 2);
    return pathStart < 0 ? "/" : withoutQuery.slice(pathStart);
  }
  const scheme = /^[a-z][a-z0-9+.-]*:\/\//i.exec(withoutQuery);
  if (scheme) {
    const pathStart = withoutQuery.indexOf("/", scheme[0].length);
    return pathStart < 0 ? "/" : withoutQuery.slice(pathStart);
  }
  return withoutQuery;
}

function absoluteHrefForManagedHtml(href: string): string {
  if (typeof window === "undefined") return href;
  return new URL(href, window.location.href).href;
}

function parseManagedHtmlDocument(html: string): Document {
  if (typeof DOMParser !== "undefined") {
    return new DOMParser().parseFromString(html, "text/html");
  }
  if (typeof document !== "undefined" && document.implementation) {
    const parsed = document.implementation.createHTMLDocument("");
    parsed.open();
    parsed.write(html);
    parsed.close();
    return parsed;
  }
  throw new Error("managed HTML preview requires a browser document parser");
}

function serializeManagedHtmlDocument(document: Document): string {
  return `${serializeDoctype(document.doctype)}${document.documentElement.outerHTML}`;
}

function serializeDoctype(doctype: DocumentType | null): string {
  if (!doctype) return "";
  let serialized = `<!doctype ${doctype.name}`;
  if (doctype.publicId) {
    serialized += ` PUBLIC "${escapeDoctypeIdentifier(doctype.publicId)}"`;
  } else if (doctype.systemId) {
    serialized += " SYSTEM";
  }
  if (doctype.systemId) {
    serialized += ` "${escapeDoctypeIdentifier(doctype.systemId)}"`;
  }
  return `${serialized}>`;
}

function escapeDoctypeIdentifier(value: string): string {
  return value.replaceAll('"', "&quot;");
}

function extensionFor(path: string): string {
  const label = filenameLabel(path);
  const dot = label.lastIndexOf(".");
  return dot < 0 ? "" : label.slice(dot + 1).toLowerCase();
}

function filenameLabel(path: string): string {
  const parts = path.split("/").filter(Boolean);
  return parts[parts.length - 1] || path;
}

function displayHrefForExternal(href: string): string {
  try {
    const url = new URL(href);
    return url.hostname || href;
  } catch {
    return href;
  }
}
