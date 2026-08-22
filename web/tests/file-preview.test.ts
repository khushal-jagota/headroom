import { afterEach, describe, expect, it, vi } from "vitest";

import {
  conversationFileTarget,
  boundedConversationTextPreview,
  isTicketDevServerHref,
  markdownExpansionFor,
  prepareManagedHtmlPreviewDocument,
  previewHashHref,
  targetFromPreviewHref,
  resolvePreview,
  targetFromHref,
  ticketFileTarget,
  type FilePreviewKind,
  type FilePreviewTarget
} from "../src/lib/filePreview";

const ticketMarkdown = {
  kind: "ticket-file",
  ticketId: "t_file123",
  path: "notes/space name.md"
} satisfies FilePreviewTarget;

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("managed-file preview resolution", () => {
  it("resolves safe durable and optimistic Conversation file targets by original name", () => {
    const durable = conversationFileTarget(
      "/api/conversation/conversations/c%2F1/files/file%201",
      "notes.md"
    );
    const outgoing = conversationFileTarget(
      "data:application/json;base64,e30=",
      "facts.json"
    );

    expect(durable).not.toBeNull();
    expect(resolvePreview(durable!)).toMatchObject({
      kind: "markdown",
      href: "/api/conversation/conversations/c%2F1/files/file%201",
      label: "notes.md"
    });
    expect(resolvePreview(outgoing!)).toMatchObject({
      kind: "text",
      href: "data:application/json;base64,e30=",
      label: "facts.json"
    });
  });

  it.each([
    ["https://example.com/api/conversation/conversations/c1/files/f1", "notes.md"],
    ["/api/conversation/conversations/c1/not-files/f1", "notes.md"],
    ["data:text/html;base64,PGgxPm5vPC9oMT4=", "page.html"],
    ["data:application/octet-stream;base64,e30=", "facts.json"],
    ["data:text/plain;base64,e30=", "facts.json"],
    ["data:application/json;base64,e30=", "bad\u0000name.json"],
    ["data:application/json;base64,e30=", " facts.json"],
    ["data:application/json;base64,e30=", "folder/facts.json"]
  ])("rejects unsafe Conversation file target %s", (href, fileName) => {
    expect(conversationFileTarget(href, fileName)).toBeNull();
  });
  it("resolves Ticket Markdown with its encoded URL, label, and preview address", () => {
    expect(resolvePreview(ticketMarkdown)).toEqual({
      kind: "markdown",
      target: ticketMarkdown,
      href: "/files/tickets/t_file123/notes/space%20name.md",
      label: "space name.md",
      previewHref: "#/preview?source=ticket&ticket=t_file123&path=notes%2Fspace%20name.md"
    });
  });

  it.each(
    [
      ["page.HTML", "html"],
      ["images/pic.webp", "image"],
      ["icon.svg", "image"],
      ["clips/demo.mp4", "video"],
      ["clips/talk.webm", "video"],
      ["clips/talk.ogv", "video"],
      ["audio/demo.mp3", "audio"],
      ["audio/talk.ogg", "audio"],
      ["audio/talk.oga", "audio"],
      ["code/tool.py", "download"],
      ["archive/bundle.zip", "download"]
    ] satisfies Array<[string, FilePreviewKind]>
  )("selects %s as %s", (path, expectedKind) => {
    expect(resolvePreview({ kind: "ticket-file", ticketId: "t_file123", path }).kind).toBe(
      expectedKind
    );
  });

  it("keeps text and data preview kinds scoped to Conversation files", () => {
    expect(resolvePreview({ kind: "ticket-file", ticketId: "t_file123", path: "notes.txt" }).kind)
      .toBe("download");
    for (const [fileName, kind] of [
      ["report.pdf", "pdf"],
      ["notes.txt", "text"],
      ["rows.csv", "text"],
      ["rows.tsv", "text"],
      ["facts.json", "text"],
      ["events.jsonl", "text"]
    ] as const) {
      const target = conversationFileTarget(
        "/api/conversation/conversations/c1/files/f1",
        fileName
      );
      expect(resolvePreview(target!).kind).toBe(kind);
    }
  });

  it("bounds Conversation text preview bytes and states when it truncates", () => {
    expect(boundedConversationTextPreview("small")).toBe("small");
    const preview = boundedConversationTextPreview("é".repeat(200_000));
    expect(new TextEncoder().encode(preview.split("\n\n[", 1)[0]).byteLength)
      .toBeLessThanOrEqual(256 * 1024);
    expect(preview).toMatch(/Preview truncated after 256 KiB/);
  });

  it("resolves an ordinary external target as an external link", () => {
    const target = {
      kind: "external-link",
      href: "https://example.com/x",
      label: "Example"
    } satisfies FilePreviewTarget;

    expect(resolvePreview(target)).toEqual({
      kind: "external",
      target,
      href: "https://example.com/x",
      label: "Example",
      displayHref: "example.com"
    });
  });

  it.each(
    [
      ["https://example.com/pictures/photo.png?v=2#top", "image"],
      ["https://example.com/clip.mp4", "video"],
      ["https://example.com/tone.wav", "audio"]
    ] satisfies Array<[string, FilePreviewKind]>
  )("lets the browser render external URL %s as %s", (href, expectedKind) => {
    expect(resolvePreview({ kind: "external-link", href }).kind).toBe(expectedKind);
  });

  it("preserves the labelled external image target in the resolved interface value", () => {
    const target = {
      kind: "external-link",
      href: "https://example.com/pictures/photo.png?v=2#top",
      label: "Photo"
    } satisfies FilePreviewTarget;

    expect(resolvePreview(target)).toEqual({
      kind: "image",
      target,
      href: "https://example.com/pictures/photo.png?v=2#top",
      label: "Photo"
    });
  });

  it.each([
    "https://example.com/notes.md",
    "https://example.com/page.html",
    "#results",
    "#/day"
  ])("keeps fetched-document or link target %s external", (href) => {
    expect(resolvePreview({ kind: "external-link", href }).kind).toBe("external");
  });

  it("allows one new Markdown expansion and records its visited URL", () => {
    expect(markdownExpansionFor(resolvePreview(ticketMarkdown), 0, [])).toEqual({
      expandable: true,
      nextDepth: 1,
      nextVisited: ["/files/tickets/t_file123/notes/space%20name.md"]
    });
  });

  it("stops Markdown recursion at a visited URL or the depth bound", () => {
    const resolved = resolvePreview(ticketMarkdown);
    const visited = [resolved.href];

    expect(markdownExpansionFor(resolved, 1, visited)).toEqual({
      expandable: false,
      nextDepth: 1,
      nextVisited: visited
    });
    expect(markdownExpansionFor(resolved, 2, [])).toEqual({
      expandable: false,
      nextDepth: 2,
      nextVisited: []
    });
  });
});

describe("managed-file targets and preview addresses", () => {
  it("resolves local and same-origin absolute managed-file URLs", () => {
    const expectedTarget = {
      kind: "ticket-file",
      ticketId: "t_file123",
      path: "notes/space name.md"
    };
    vi.stubGlobal("window", {
      location: {
        origin: "https://panels.test",
        href: "https://panels.test/workspace"
      }
    });

    expect(targetFromHref("/files/tickets/t_file123/notes/space%20name.md")).toEqual(
      expectedTarget
    );
    expect(
      targetFromHref("https://panels.test/files/tickets/t_file123/notes/space%20name.md")
    ).toEqual(expectedTarget);
  });

  it("falls back to an external link for a cross-origin managed-file URL", () => {
    vi.stubGlobal("window", {
      location: {
        origin: "https://panels.test",
        href: "https://panels.test/workspace"
      }
    });
    const href = "https://example.com/files/tickets/t_file123/notes/space%20name.md";

    expect(targetFromHref(href, "Remote")).toEqual({
      kind: "external-link",
      href,
      label: "Remote"
    });
  });

  it.each(["../t_other/notes.md", "notes/./file.md", "%2e%2e/notes.md"])(
    "rejects unsafe Ticket path %s",
    (path) => {
      expect(ticketFileTarget("t_file123", path)).toBeNull();
    }
  );

  it.each([
    "/files/tickets/t_file123/../t_other/notes.md",
    "/files/tickets/t_file123/notes/%2e%2e/secret.md"
  ])("falls back to an external link for unsafe managed URL %s", (href) => {
    expect(targetFromHref(href, "Unsafe")).toEqual({
      kind: "external-link",
      href,
      label: "Unsafe"
    });
  });

  it("rejects an unsafe target passed directly to preview resolution", () => {
    expect(() =>
      resolvePreview({
        kind: "ticket-file",
        ticketId: "t_file123",
        path: "../t_other/notes.md"
      })
    ).toThrow(/unsafe ticket file target/);
  });

  it("generates the explicit preview hash", () => {
    expect(previewHashHref(ticketMarkdown)).toBe(
      "#/preview?source=ticket&ticket=t_file123&path=notes%2Fspace%20name.md"
    );
  });

  // A screen that would rather show the file than go to it reads the file back out of
  // the link the shared preview wrote.
  it("reads its own preview address back as the file it names", () => {
    expect(targetFromPreviewHref(previewHashHref(ticketMarkdown))).toEqual(ticketMarkdown);
    expect(
      targetFromPreviewHref("#/preview?source=sprint-item&item=si_one&path=notes%2Fplan.md")
    ).toEqual({ kind: "sprint-item-file", sprintItemId: "si_one", path: "notes/plan.md" });
    // The form the server builds, with a leading slash.
    expect(targetFromPreviewHref("/#/preview?source=ticket&ticket=t_one&path=a.md")).toEqual({
      kind: "ticket-file",
      ticketId: "t_one",
      path: "a.md"
    });
  });

  it("claims no address but a preview address naming a safe file", () => {
    expect(targetFromPreviewHref("#/workspace/t_one")).toBeNull();
    expect(targetFromPreviewHref("/files/tickets/t_one/artifacts/plan.html")).toBeNull();
    expect(targetFromPreviewHref("#/preview")).toBeNull();
    expect(targetFromPreviewHref("#/preview?source=ticket&ticket=t_one")).toBeNull();
    expect(targetFromPreviewHref("#/preview?source=ticket&ticket=t_one&path=../x.md")).toBeNull();
    expect(targetFromPreviewHref("#/previews?source=ticket&ticket=t_one&path=a.md")).toBeNull();
    expect(targetFromPreviewHref("https://example.com/thing")).toBeNull();
    expect(targetFromPreviewHref(null)).toBeNull();
  });
});

describe("Ticket dev-server addresses", () => {
  const stubPanelsOrigin = () => {
    vi.stubGlobal("window", {
      location: { origin: "https://panels.test", href: "https://panels.test/workspace" }
    });
  };

  it.each([
    "/dev/tickets/t_file123/8791/",
    "/dev/tickets/t_file123/8791",
    "/dev/tickets/t_file123/4173/nested/page?theme=dark#result",
    "/dev/tickets/t_file123/1/",
    "/dev/tickets/t_file123/65535/",
    "https://panels.test/dev/tickets/t_file123/8791/"
  ])("recognizes %s", (href) => {
    stubPanelsOrigin();
    expect(isTicketDevServerHref(href)).toBe(true);
  });

  it.each([
    "/dev/tickets/t_file123/",
    "/dev/tickets/t_file123/0/",
    "/dev/tickets/t_file123/65536/",
    "/dev/tickets/t_file123/80x/",
    "/dev/tickets/not-a-ticket/8791/",
    "/dev/tickets/t_file123/8791x/page",
    "/dev/sprint-items/t_file123/8791/",
    "/files/tickets/t_file123/8791/",
    "https://example.com/dev/tickets/t_file123/8791/"
  ])("rejects %s", (href) => {
    stubPanelsOrigin();
    expect(isTicketDevServerHref(href)).toBe(false);
  });

  it("stays an external-link target, so the preview component is unchanged", () => {
    stubPanelsOrigin();
    const href = "/dev/tickets/t_file123/8791/";

    expect(targetFromHref(href, "take one")).toEqual({
      kind: "external-link",
      href,
      label: "take one"
    });
    expect(resolvePreview(targetFromHref(href, "take one"))).toMatchObject({
      kind: "external",
      href,
      label: "take one"
    });
  });
});

describe("managed HTML preparation", () => {
  it("inserts an absolute base first and preserves doctype serialization", () => {
    const firstHeadChild = {} as ChildNode;
    const attributes = new Map<string, string>();
    const baseElement = {
      setAttribute(name: string, value: string) {
        attributes.set(name, value);
      }
    } as Element;
    let parsedSource = "";
    let parsedType: DOMParserSupportedType | null = null;
    let createdTag = "";
    let insertedNode: Node | null = null;
    let insertionReference: Node | null = null;

    const fakeDocument = {
      createElement(tagName: string) {
        createdTag = tagName;
        return baseElement;
      },
      head: {
        firstChild: firstHeadChild,
        insertBefore(newNode: Node, referenceNode: Node | null) {
          insertedNode = newNode;
          insertionReference = referenceNode;
          return newNode;
        }
      },
      doctype: {
        name: "html",
        publicId: "-//W3C//DTD XHTML 1.0 Transitional//EN",
        systemId: "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd"
      },
      documentElement: {
        get outerHTML() {
          return `<html><head><base href="${attributes.get("href")}"><title>Preview</title></head><body>Body</body></html>`;
        }
      }
    } as unknown as Document;

    class RecordingDOMParser {
      parseFromString(source: string, type: DOMParserSupportedType): Document {
        parsedSource = source;
        parsedType = type;
        return fakeDocument;
      }
    }

    vi.stubGlobal("window", {
      location: {
        origin: "https://panels.test",
        href: "https://panels.test/workspace#/ticket/t_file123"
      }
    });
    vi.stubGlobal("DOMParser", RecordingDOMParser);

    const prepared = prepareManagedHtmlPreviewDocument(
      "<!doctype html><html><head><title>Preview</title></head><body>Body</body></html>",
      "/files/tickets/t_file123/site/page.html"
    );

    expect(parsedSource).toBe(
      "<!doctype html><html><head><title>Preview</title></head><body>Body</body></html>"
    );
    expect(parsedType).toBe("text/html");
    expect(createdTag).toBe("base");
    expect(attributes.get("href")).toBe(
      "https://panels.test/files/tickets/t_file123/site/page.html"
    );
    expect(insertedNode).toBe(baseElement);
    expect(insertionReference).toBe(firstHeadChild);
    expect(prepared).toBe(
      '<!doctype html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd"><html><head><base href="https://panels.test/files/tickets/t_file123/site/page.html"><title>Preview</title></head><body>Body</body></html>'
    );
  });
});
