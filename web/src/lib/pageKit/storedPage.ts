import type { PageDocument } from "./contracts";

export const PAGE_KIND = "headroom.page";
const PAGE_VERSION = 1;

export type StoredPage =
  | { intent: "prose" }
  | { intent: "page"; document: PageDocument }
  | { intent: "invalid"; message: string };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Read a stored proposal. The server already checked a page; this only tells prose from a page. */
export function readStoredPage(body: string): StoredPage {
  if (!body.trimStart().startsWith("{")) return { intent: "prose" };
  let value: unknown;
  try {
    value = JSON.parse(body);
  } catch {
    if (body.slice(0, 2000).includes(PAGE_KIND)) {
      return {
        intent: "invalid",
        message: "proposal page is outside the page kit: the text is not valid JSON"
      };
    }
    return { intent: "prose" };
  }
  if (!isRecord(value) || value.kind !== PAGE_KIND) return { intent: "prose" };
  if (
    value.version !== PAGE_VERSION
    || typeof value.title !== "string"
    || typeof value.summary !== "string"
    || !Array.isArray(value.blocks)
  ) {
    return {
      intent: "invalid",
      message: "proposal page is outside the page kit: $: expected a page document"
    };
  }
  return { intent: "page", document: value as unknown as PageDocument };
}

export function prettyPage(document: PageDocument): string {
  return JSON.stringify(document, null, 2);
}

export function pageDocumentDigest(body: string): string {
  let hash = 0x811c9dc5;
  for (let index = 0; index < body.length; index += 1) {
    hash ^= body.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

/** The text a holder edit should store: the page JSON, or prose unchanged. */
export function normalizeAuthoredBody(body: string): string {
  const read = readStoredPage(body);
  if (read.intent === "invalid") throw new Error(read.message);
  if (read.intent === "prose") {
    if (!body) throw new Error("proposal body must be non-empty");
    return body;
  }
  return JSON.stringify(read.document);
}

/** Type-checked authoring door. The parameter is the closed page document. */
export function definePage(document: PageDocument): PageDocument {
  return document;
}
