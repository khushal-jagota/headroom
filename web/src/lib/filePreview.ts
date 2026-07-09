export type FilePreviewTarget =
  | { kind: "ticket-file"; ticketId: string; path: string }
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
};

const MARKDOWN_EXTENSIONS = new Set(["md", "markdown"]);
const HTML_EXTENSIONS = new Set(["html", "htm"]);
const IMAGE_EXTENSIONS = new Set(["avif", "bmp", "gif", "jpeg", "jpg", "png", "webp"]);
const VIDEO_EXTENSIONS = new Set(["m4v", "mov", "mp4", "ogg", "ogv", "webm"]);
const AUDIO_EXTENSIONS = new Set(["aac", "flac", "m4a", "mp3", "oga", "ogg", "opus", "wav", "webm"]);
const TICKET_ID_RE = /^t_[a-z0-9]+$/;
const RESIDUAL_UNSAFE_RE = /%(?:25|2e|2f|5c)/i;

export function resolvePreview(target: FilePreviewTarget): ResolvedPreview {
  if (target.kind === "external-link") {
    return {
      kind: "external",
      target,
      href: target.href,
      label: target.label || target.href,
    };
  }

  if (!ticketFileTarget(target.ticketId, target.path)) {
    throw new Error("unsafe ticket file target");
  }
  const href = ticketFileHref(target);
  const label = filenameLabel(target.path);
  const previewHref = previewHashHref(target);
  const extension = extensionFor(target.path);
  if (MARKDOWN_EXTENSIONS.has(extension)) return { kind: "markdown", target, href, label, previewHref };
  if (HTML_EXTENSIONS.has(extension)) return { kind: "html", target, href, label, previewHref };
  if (IMAGE_EXTENSIONS.has(extension)) return { kind: "image", target, href, label, previewHref };
  if (VIDEO_EXTENSIONS.has(extension)) return { kind: "video", target, href, label, previewHref };
  if (AUDIO_EXTENSIONS.has(extension)) return { kind: "audio", target, href, label, previewHref };
  return { kind: "download", target, href, label, previewHref };
}

export function targetFromHref(href: string, label = ""): FilePreviewTarget {
  const ticketTarget = ticketFileTargetFromHref(href);
  if (ticketTarget) return ticketTarget;
  return { kind: "external-link", href, label: label || href };
}

export function ticketFileTarget(
  ticketId: string,
  path: string
): Extract<FilePreviewTarget, { kind: "ticket-file" }> | null {
  if (!TICKET_ID_RE.test(ticketId) || !path || path.startsWith("/") || path.includes("\\")) {
    return null;
  }
  if (RESIDUAL_UNSAFE_RE.test(path)) return null;
  const segments = path.split("/");
  if (segments.some((segment) => !segment || segment === "." || segment === "..")) return null;
  return { kind: "ticket-file", ticketId, path };
}

export function previewHashHref(target: Extract<FilePreviewTarget, { kind: "ticket-file" }>): string {
  return (
    "#/preview?source=ticket" +
    `&ticket=${encodeURIComponent(target.ticketId)}` +
    `&path=${encodeURIComponent(target.path)}`
  );
}

export function ticketFileHref(target: Extract<FilePreviewTarget, { kind: "ticket-file" }>): string {
  const path = target.path
    .split("/")
    .map((segment) => encodeURIComponent(segment))
    .join("/");
  return `/files/tickets/${encodeURIComponent(target.ticketId)}/${path}`;
}

function ticketFileTargetFromHref(href: string): FilePreviewTarget | null {
  const localOrigin = typeof window === "undefined" ? "http://planner.local" : window.location.origin;
  try {
    if (new URL(href, localOrigin).origin !== localOrigin) return null;
  } catch {
    return null;
  }
  const pathname = rawPathnameFromHref(href);
  const prefix = "/files/tickets/";
  if (!pathname.startsWith(prefix)) return null;
  const rest = pathname.slice(prefix.length);
  const slash = rest.indexOf("/");
  if (slash <= 0 || slash === rest.length - 1) return null;
  try {
    return ticketFileTarget(
      decodeURIComponent(rest.slice(0, slash)),
      decodeURIComponent(rest.slice(slash + 1))
    );
  } catch {
    return null;
  }
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
