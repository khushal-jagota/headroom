export type FilePreviewTarget =
  | { kind: "ticket-file"; ticketId: string; path: string }
  | { kind: "chat-file"; entityId: string; path: string }
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
  actionLabel?: string;
};

const MARKDOWN_EXTENSIONS = new Set(["md", "markdown"]);
const HTML_EXTENSIONS = new Set(["html", "htm"]);
const IMAGE_EXTENSIONS = new Set(["avif", "bmp", "gif", "jpeg", "jpg", "png", "webp"]);
const VIDEO_EXTENSIONS = new Set(["m4v", "mov", "mp4", "ogg", "ogv", "webm"]);
const AUDIO_EXTENSIONS = new Set(["aac", "flac", "m4a", "mp3", "oga", "ogg", "opus", "wav", "webm"]);
const TICKET_ID_RE = /^t_[a-z0-9]+$/;
const ENTITY_ID_RE = /^[A-Za-z0-9_-]+$/;
const RESIDUAL_UNSAFE_RE = /%(?:25|2e|2f|5c)/i;
const MAX_MARKDOWN_EMBED_DEPTH = 2;

export function resolvePreview(target: FilePreviewTarget): ResolvedPreview {
  if (target.kind === "external-link") {
    return {
      kind: "external",
      target,
      href: target.href,
      label: target.label || target.href,
      displayHref: displayHrefForExternal(target.href),
      actionLabel: "Open external link"
    };
  }

  if (target.kind === "ticket-file" && !ticketFileTarget(target.ticketId, target.path)) {
    throw new Error("unsafe ticket file target");
  }
  if (target.kind === "chat-file" && !chatFileTarget(target.entityId, target.path)) {
    throw new Error("unsafe chat file target");
  }
  const href = target.kind === "ticket-file" ? ticketFileHref(target) : chatFileHref(target);
  const label = filenameLabel(target.path);
  const previewHref = previewHashHref(target);
  const extension = extensionFor(target.path);
  if (MARKDOWN_EXTENSIONS.has(extension)) return { kind: "markdown", target, href, label, previewHref };
  if (HTML_EXTENSIONS.has(extension)) {
    return { kind: "html", target, href, label, previewHref, actionLabel: "Open preview" };
  }
  if (IMAGE_EXTENSIONS.has(extension)) return { kind: "image", target, href, label, previewHref };
  if (VIDEO_EXTENSIONS.has(extension)) return { kind: "video", target, href, label, previewHref };
  if (AUDIO_EXTENSIONS.has(extension)) return { kind: "audio", target, href, label, previewHref };
  return { kind: "download", target, href, label, previewHref, actionLabel: "Download" };
}

export function markdownExpansionFor(
  resolved: ResolvedPreview,
  depth: number,
  visited: string[]
): { expandable: boolean; nextDepth: number; nextVisited: string[] } {
  if (resolved.kind !== "markdown" || resolved.target.kind !== "ticket-file") {
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
  const chatTarget = chatFileTargetFromHref(href);
  if (chatTarget) return chatTarget;
  return { kind: "external-link", href, label: label || href };
}

export function ticketFileTarget(
  ticketId: string,
  path: string
): Extract<FilePreviewTarget, { kind: "ticket-file" }> | null {
  if (!TICKET_ID_RE.test(ticketId) || !safeManagedPath(path)) return null;
  return { kind: "ticket-file", ticketId, path };
}

export function chatFileTarget(
  entityId: string,
  path: string
): Extract<FilePreviewTarget, { kind: "chat-file" }> | null {
  if (!ENTITY_ID_RE.test(entityId) || !safeManagedPath(path)) return null;
  return { kind: "chat-file", entityId, path };
}

export function previewHashHref(
  target: Extract<FilePreviewTarget, { kind: "ticket-file" | "chat-file" }>
): string {
  if (target.kind === "chat-file") {
    return (
      "#/preview?source=chat" +
      `&entity=${encodeURIComponent(target.entityId)}` +
      `&path=${encodeURIComponent(target.path)}`
    );
  }
  return (
    "#/preview?source=ticket" +
    `&ticket=${encodeURIComponent(target.ticketId)}` +
    `&path=${encodeURIComponent(target.path)}`
  );
}

export function ticketFileHref(target: Extract<FilePreviewTarget, { kind: "ticket-file" }>): string {
  const path = encodedManagedPath(target.path);
  return `/files/tickets/${encodeURIComponent(target.ticketId)}/${path}`;
}

export function chatFileHref(target: Extract<FilePreviewTarget, { kind: "chat-file" }>): string {
  const path = encodedManagedPath(target.path);
  return `/files/chats/${encodeURIComponent(target.entityId)}/${path}`;
}

function ticketFileTargetFromHref(href: string): FilePreviewTarget | null {
  const parts = managedHrefParts(href, "/files/tickets/");
  if (!parts) return null;
  return ticketFileTarget(parts.entityId, parts.path);
}

function chatFileTargetFromHref(href: string): FilePreviewTarget | null {
  const parts = managedHrefParts(href, "/files/chats/");
  if (!parts) return null;
  return chatFileTarget(parts.entityId, parts.path);
}

function managedHrefParts(href: string, prefix: string): { entityId: string; path: string } | null {
  const localOrigin = typeof window === "undefined" ? "http://planner.local" : window.location.origin;
  try {
    if (new URL(href, localOrigin).origin !== localOrigin) return null;
  } catch {
    return null;
  }
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
