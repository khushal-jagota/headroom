import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import ts from "typescript";

const source = await readFile(new URL("../src/lib/filePreview.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
    verbatimModuleSyntax: true
  }
}).outputText;
const dir = await mkdtemp(join(tmpdir(), "planner-file-preview-"));
const modulePath = join(dir, "filePreview.mjs");
await writeFile(modulePath, compiled, "utf8");
const {
  markdownExpansionFor,
  previewHashHref,
  resolvePreview,
  targetFromHref,
  ticketFileTarget
} = await import(modulePath);
await rm(dir, { recursive: true, force: true });

const ticketMarkdown = {
  kind: "ticket-file",
  ticketId: "t_file123",
  path: "notes/space name.md"
};
assert.deepEqual(resolvePreview(ticketMarkdown), {
  kind: "markdown",
  target: ticketMarkdown,
  href: "/files/tickets/t_file123/notes/space%20name.md",
  label: "space name.md",
  previewHref: "#/preview?source=ticket&ticket=t_file123&path=notes%2Fspace%20name.md"
});

const ticketHtml = { kind: "ticket-file", ticketId: "t_file123", path: "page.HTML" };
assert.equal(resolvePreview(ticketHtml).kind, "html");

const ticketImage = { kind: "ticket-file", ticketId: "t_file123", path: "images/pic.webp" };
assert.equal(resolvePreview(ticketImage).kind, "image");

const ticketVideo = { kind: "ticket-file", ticketId: "t_file123", path: "clips/demo.mp4" };
assert.equal(resolvePreview(ticketVideo).kind, "video");

const ticketAudio = { kind: "ticket-file", ticketId: "t_file123", path: "audio/demo.mp3" };
assert.equal(resolvePreview(ticketAudio).kind, "audio");

// SVG is an image like any other.
const ticketSvg = { kind: "ticket-file", ticketId: "t_file123", path: "icon.svg" };
assert.equal(resolvePreview(ticketSvg).kind, "image");

// One extension names one kind: no extension is both video and audio.
for (const [path, kind] of [
  ["clips/talk.webm", "video"],
  ["clips/talk.ogv", "video"],
  ["audio/talk.ogg", "audio"],
  ["audio/talk.oga", "audio"],
  ["code/tool.py", "download"],
  ["archive/bundle.zip", "download"]
]) {
  assert.equal(resolvePreview({ kind: "ticket-file", ticketId: "t_file123", path }).kind, kind, path);
}

assert.deepEqual(resolvePreview({ kind: "external-link", href: "https://example.com/x", label: "Example" }), {
  kind: "external",
  target: { kind: "external-link", href: "https://example.com/x", label: "Example" },
  href: "https://example.com/x",
  label: "Example",
  displayHref: "example.com"
});

// A URL the browser renders on its own is that kind, wherever it is hosted.
const externalImage = {
  kind: "external-link",
  href: "https://example.com/pictures/photo.png?v=2#top",
  label: "Photo"
};
assert.deepEqual(resolvePreview(externalImage), {
  kind: "image",
  target: externalImage,
  href: "https://example.com/pictures/photo.png?v=2#top",
  label: "Photo"
});
assert.equal(
  resolvePreview({ kind: "external-link", href: "https://example.com/clip.mp4" }).kind,
  "video"
);
assert.equal(
  resolvePreview({ kind: "external-link", href: "https://example.com/tone.wav" }).kind,
  "audio"
);
// Markdown and HTML are rendered from bytes we fetch, so a URL stays a link.
for (const href of ["https://example.com/notes.md", "https://example.com/page.html", "#results", "#/day"]) {
  assert.equal(resolvePreview({ kind: "external-link", href }).kind, "external", href);
}

for (const resolved of [resolvePreview(ticketHtml), resolvePreview(ticketSvg), resolvePreview(externalImage)]) {
  assert.equal("actionLabel" in resolved, false);
}
assert.deepEqual(markdownExpansionFor(resolvePreview(ticketMarkdown), 0, []), {
  expandable: true,
  nextDepth: 1,
  nextVisited: ["/files/tickets/t_file123/notes/space%20name.md"]
});
assert.deepEqual(
  markdownExpansionFor(resolvePreview(ticketMarkdown), 1, [
    "/files/tickets/t_file123/notes/space%20name.md"
  ]),
  {
    expandable: false,
    nextDepth: 1,
    nextVisited: ["/files/tickets/t_file123/notes/space%20name.md"]
  }
);
assert.deepEqual(markdownExpansionFor(resolvePreview(ticketMarkdown), 2, []), {
  expandable: false,
  nextDepth: 2,
  nextVisited: []
});

assert.deepEqual(targetFromHref("/files/tickets/t_file123/notes/space%20name.md"), ticketMarkdown);
globalThis.window = { location: { origin: "https://panels.test" } };
assert.deepEqual(
  targetFromHref("https://panels.test/files/tickets/t_file123/notes/space%20name.md"),
  ticketMarkdown
);
assert.deepEqual(
  targetFromHref("https://example.com/files/tickets/t_file123/notes/space%20name.md", "Remote"),
  {
    kind: "external-link",
    href: "https://example.com/files/tickets/t_file123/notes/space%20name.md",
    label: "Remote"
  }
);
assert.equal(ticketFileTarget("t_file123", "../t_other/notes.md"), null);
assert.equal(ticketFileTarget("t_file123", "notes/./file.md"), null);
assert.equal(ticketFileTarget("t_file123", "%2e%2e/notes.md"), null);
for (const unsafeHref of [
  "/files/tickets/t_file123/../t_other/notes.md",
  "/files/tickets/t_file123/notes/%2e%2e/secret.md"
]) {
  assert.deepEqual(targetFromHref(unsafeHref, "Unsafe"), {
    kind: "external-link",
    href: unsafeHref,
    label: "Unsafe"
  });
}
assert.throws(
  () => resolvePreview({ kind: "ticket-file", ticketId: "t_file123", path: "../t_other/notes.md" }),
  /unsafe ticket file target/
);
assert.deepEqual(targetFromHref("https://example.com/x", "Example"), {
  kind: "external-link",
  href: "https://example.com/x",
  label: "Example"
});
assert.equal(previewHashHref(ticketMarkdown), "#/preview?source=ticket&ticket=t_file123&path=notes%2Fspace%20name.md");
