import assert from "node:assert/strict";
import { mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";
import ts from "typescript";

const webRoot = new URL("..", import.meta.url);
const srcRoot = new URL("../src/", import.meta.url);
const libRoot = new URL("../src/lib/", import.meta.url);
const componentRoot = new URL("../src/components/", import.meta.url);
const ownerUrl = new URL("../src/lib/managedMarkdown.ts", import.meta.url);
const ticketDevServerLinkUrl = new URL(
  "../src/lib/ticketDevServerLink.ts",
  import.meta.url
);

async function productionFiles(directory, suffixes) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const url = new URL(`${entry.name}${entry.isDirectory() ? "/" : ""}`, directory);
    if (entry.isDirectory()) files.push(...(await productionFiles(url, suffixes)));
    else if (suffixes.some((suffix) => entry.name.endsWith(suffix))) files.push(url);
  }
  return files;
}

// Static ownership and deletion contract.
const ownerSource = await readFile(ownerUrl, "utf8");
const ticketDevServerLinkSource = await readFile(ticketDevServerLinkUrl, "utf8");
const pipelineSource = await readFile(new URL("../src/lib/markdownPipeline.ts", import.meta.url), "utf8");
for (const declaration of [
  "export type ReadOnlyManagedMarkdownInput",
  "export interface ReadOnlyManagedMarkdownSurface",
  "export interface EditableManagedMarkdownSurface",
  "export function createManagedMarkdownSurface"
]) {
  assert.ok(ownerSource.includes(declaration), declaration);
}
assert.match(ownerSource, /readonly mode: "read-only"/);
assert.match(ownerSource, /readonly mode: "editable"/);
assert.equal(
  (ownerSource.match(/export function createManagedMarkdownSurface\(/g) || []).length,
  3
);

const productionSources = await productionFiles(srcRoot, [".ts", ".svelte"]);
const sourceByName = new Map(
  await Promise.all(
    productionSources.map(async (url) => [basename(url.pathname), await readFile(url, "utf8")])
  )
);
for (const token of [
  "new MutationObserver",
  "markdownAtomicSlot"
]) {
  assert.deepEqual(
    [...sourceByName].filter(([, source]) => source.includes(token)).map(([name]) => name),
    ["managedMarkdown.ts"],
    token
  );
}
for (const token of ["renderMarkdownToElement", "serializeMarkdownDomToSource"]) {
  assert.deepEqual(
    [...sourceByName].filter(([, source]) => source.includes(token)).map(([name]) => name).sort(),
    ["managedMarkdown.ts", "markdownPipeline.ts"],
    token
  );
}
for (const token of ["data-markdown-source-token"]) {
  assert.deepEqual(
    [...sourceByName].filter(([, source]) => source.includes(token)).map(([name]) => name).sort(),
    ["managedMarkdown.ts", "markdownPipeline.ts"],
    token
  );
}
assert.ok(ownerSource.includes('from "./markdownPipeline"'));
assert.ok(ownerSource.includes('from "./ticketDevServerLink"'));
assert.ok(pipelineSource.includes("remarkGfm"));
assert.ok(pipelineSource.includes("rehypeSanitize"));
assert.ok(pipelineSource.includes("rehypeToRemark"));
for (const token of ['from "svelte"', 'FilePreview.svelte']) {
  assert.deepEqual(
    [...sourceByName]
      .filter(([, source]) => source.includes(token) && source.includes("targetFromHref"))
      .map(([name]) => name),
    ["managedMarkdown.ts"],
    token
  );
}
for (const wrapper of ["MarkdownBlock.svelte", "InlineEdit.svelte"]) {
  const source = sourceByName.get(wrapper);
  assert.ok(source);
  for (const forbidden of [
    "cleanupMarkdownPreviews",
    "cleanupPreviews",
    "Planner.markdown",
    "mountFilePreviews",
    "MutationObserver",
    "data-markdown-source-token",
    "serializeBlockContainer",
    "mount(",
    "unmount("
  ]) {
    assert.ok(!source.includes(forbidden), `${wrapper}: ${forbidden}`);
  }
}
for (const deleted of ["markdownEdit.ts", "filePreviewMount.ts"]) {
  assert.ok(!sourceByName.has(deleted), deleted);
}
for (const deletedExport of [
  "paintMarkdownEditable",
  "readMarkdownEditable",
  "refreshEditableEmptyState",
  "mountFilePreviews"
]) {
  assert.ok(![...sourceByName.values()].some((source) => source.includes(deletedExport)));
}
assert.ok(![...sourceByName.values()].some((source) => /\.innerHTML\s*=/.test(source)));
await assert.rejects(readFile(new URL("../../assets/markdown.js", import.meta.url), "utf8"), {
  code: "ENOENT"
});
assert.ok(!(await readFile(new URL("../index.html", import.meta.url), "utf8")).includes(
  "/assets/markdown.js"
));

for (const conversationComponent of [
  "LiveConversation.svelte",
  "ConversationPane.svelte",
  "ConversationViewport.svelte",
  "ConversationTranscript.svelte",
  "MessagePieces.svelte"
]) {
  assert.match(sourceByName.get(conversationComponent), /ticketId/, conversationComponent);
}
assert.match(sourceByName.get("TicketRoute.svelte"), /ticketId=\{detail\.id\}/);
assert.doesNotMatch(sourceByName.get("ChiefConversation.svelte"), /ticketId=/);
assert.doesNotMatch(sourceByName.get("DevConversationRoute.svelte"), /ticketId=/);
assert.ok(!(await readFile(new URL("../src/vite-env.d.ts", import.meta.url), "utf8")).includes(
  "Planner?:"
));
assert.ok((await readFile(new URL("../package.json", import.meta.url), "utf8")).includes(
  "tests/managed-markdown.test.mjs"
));

// Small fake DOM for isolated owner behavior.
class FakeNode {
  static TEXT_NODE = 3;
  static ELEMENT_NODE = 1;
  parentNode = null;

  get isConnected() {
    let current = this;
    while (current) {
      if (current === fakeDocument.documentElement) return true;
      current = current.parentNode;
    }
    return false;
  }
}

class FakeText extends FakeNode {
  nodeType = FakeNode.TEXT_NODE;
  constructor(text) {
    super();
    this.textContent = text;
  }
}

function dataAttributeName(property) {
  return `data-${property.replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`)}`;
}

class FakeElement extends FakeNode {
  nodeType = FakeNode.ELEMENT_NODE;
  attributes = new Map();
  childNodes = [];
  listeners = new Map();
  contentEditable = "inherit";

  constructor(tagName, children = [], attributes = {}) {
    super();
    this.tagName = tagName.toUpperCase();
    this.classList = {
      add: (...names) => {
        const current = new Set(this.className.split(/\s+/).filter(Boolean));
        for (const name of names) current.add(name);
        this.className = [...current].join(" ");
      },
      contains: (name) => this.className.split(/\s+/).includes(name)
    };
    this.dataset = new Proxy(
      {},
      {
        get: (_target, property) => this.getAttribute(dataAttributeName(String(property))),
        set: (_target, property, value) => {
          this.setAttribute(dataAttributeName(String(property)), String(value));
          return true;
        }
      }
    );
    for (const [name, value] of Object.entries(attributes)) this.setAttribute(name, value);
    for (const child of children) this.appendChild(child);
  }

  get children() {
    return this.childNodes.filter((child) => child instanceof FakeElement);
  }

  get className() {
    return this.getAttribute("class") || "";
  }

  set className(value) {
    this.setAttribute("class", value);
  }

  get textContent() {
    return this.childNodes.map((child) => child.textContent || "").join("");
  }

  set textContent(value) {
    this.replaceChildren(value ? new FakeText(String(value)) : undefined);
  }

  appendChild(child) {
    if (child.parentNode) child.parentNode.removeChild(child);
    child.parentNode = this;
    this.childNodes.push(child);
    return child;
  }

  append(...children) {
    for (const child of children) this.appendChild(child);
  }

  removeChild(child) {
    const index = this.childNodes.indexOf(child);
    if (index >= 0) this.childNodes.splice(index, 1);
    child.parentNode = null;
    return child;
  }

  replaceChildren(...children) {
    for (const child of this.childNodes) child.parentNode = null;
    this.childNodes = [];
    for (const child of children) if (child) this.appendChild(child);
  }

  replaceWith(...nodes) {
    const parent = this.parentNode;
    if (!parent) return;
    const index = parent.childNodes.indexOf(this);
    parent.childNodes.splice(index, 1, ...nodes);
    this.parentNode = null;
    for (const node of nodes) {
      if (node.parentNode) node.parentNode.removeChild(node);
      node.parentNode = parent;
    }
  }

  contains(candidate) {
    if (candidate === this) return true;
    return this.childNodes.some(
      (child) => child === candidate || (child instanceof FakeElement && child.contains(candidate))
    );
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  hasAttribute(name) {
    return this.attributes.has(name);
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  removeAttribute(name) {
    this.attributes.delete(name);
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  removeEventListener(type, listener) {
    this.listeners.set(
      type,
      (this.listeners.get(type) || []).filter((candidate) => candidate !== listener)
    );
  }

  dispatchEvent(event) {
    event.currentTarget = this;
    for (const listener of this.listeners.get(event.type) || []) listener.call(this, event);
    return true;
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }

  querySelectorAll(selector) {
    const matches = [];
    const match = (element) => {
      if (selector === "a[href]") return element.tagName === "A" && element.hasAttribute("href");
      if (selector === "img[src]") return element.tagName === "IMG" && element.hasAttribute("src");
      if (selector === "a[href], img[src]") {
        return (
          (element.tagName === "A" && element.hasAttribute("href")) ||
          (element.tagName === "IMG" && element.hasAttribute("src"))
        );
      }
      if (selector === "code") return element.tagName === "CODE";
      return false;
    };
    const visit = (element) => {
      for (const child of element.children) {
        if (match(child)) matches.push(child);
        visit(child);
      }
    };
    visit(this);
    return matches;
  }
}

class FakeInputEvent {
  constructor(type, options = {}) {
    this.type = type;
    this.bubbles = options.bubbles || false;
  }
}

const fakeDocument = {
  documentElement: new FakeElement("html"),
  createElement: (tagName) => new FakeElement(tagName),
  createTextNode: (text) => new FakeText(text)
};
const appRoot = new FakeElement("body");
fakeDocument.documentElement.appendChild(appRoot);

const observerInstances = [];
class FakeMutationObserver {
  constructor(callback) {
    this.callback = callback;
    this.observeCalls = [];
    this.disconnectCalls = 0;
    observerInstances.push(this);
  }
  observe(target, options) {
    this.observeCalls.push({ target, options });
  }
  disconnect() {
    this.disconnectCalls += 1;
  }
  emit(records) {
    this.callback(records);
  }
}

globalThis.Node = FakeNode;
globalThis.HTMLElement = FakeElement;
globalThis.document = fakeDocument;
globalThis.InputEvent = FakeInputEvent;
globalThis.MutationObserver = FakeMutationObserver;

const mountCalls = [];
const unmountCalls = [];
globalThis.__managedMarkdownSvelte = {
  mount: (_component, options) => {
    const handle = { id: mountCalls.length + 1, options };
    options.target.appendChild(
      new FakeElement("article", [new FakeText("generated preview text")], {
        "data-file-preview": ""
      })
    );
    mountCalls.push(handle);
    return handle;
  },
  unmount: (handle) => {
    unmountCalls.push(handle);
    return Promise.resolve();
  },
  FilePreview: {}
};
const TICKET_FILE_PREFIX = "/files/tickets/t_demo/";
globalThis.__managedMarkdownFilePreview = {
  targetFromHref: (href, label) => {
    if (href.startsWith(TICKET_FILE_PREFIX)) {
      return {
        kind: "ticket-file",
        ticketId: "t_demo",
        path: href.slice(TICKET_FILE_PREFIX.length)
      };
    }
    return { kind: "external-link", href, label };
  },
  resolvePreview: (target) => {
    const name = target.kind === "ticket-file" ? target.path : target.href;
    if (/\.(png|jpg|gif|webp|svg)$/i.test(name)) return { kind: "image" };
    if (/\.md$/i.test(name)) return { kind: "markdown" };
    return { kind: "external" };
  }
};

function text(value) {
  return new FakeText(value);
}

function element(tag, children = [], attributes = {}) {
  return new FakeElement(tag, children, attributes);
}

function renderedMarkdown(source) {
  const rendered = element("div");
  rendered.classList.add("markdown");
  if (source.startsWith("[![Attached image]")) {
    rendered.appendChild(
      element("p", [
        element("a", [element("img", [], {
          src: "/files/tickets/t_demo/images/shot.png",
          alt: "Attached image",
          "data-markdown-source-token":
            "![Attached image](/files/tickets/t_demo/images/shot.png)"
        })], {
          href: "https://example.com/image-link",
          "data-markdown-source-token":
            "[![Attached image](/files/tickets/t_demo/images/shot.png)](https://example.com/image-link)"
        })
      ])
    );
  } else if (source.includes("![Attached image]")) {
    rendered.appendChild(
      element("p", [element("img", [], {
        src: "/files/tickets/t_demo/images/shot.png",
        alt: "Attached image",
        "data-markdown-source-token":
          "![Attached image](/files/tickets/t_demo/images/shot.png)"
      })])
    );
  } else if (source.includes("![Remote image]")) {
    rendered.appendChild(
      element("p", [element("img", [], {
        src: "https://example.com/remote.png",
        alt: "Remote image",
        "data-markdown-source-token": "![Remote image](https://example.com/remote.png)"
      })])
    );
  } else if (source.includes("[Results]")) {
    rendered.appendChild(
      element("p", [
        text("Jump to "),
        element("a", [text("Results")], {
          href: "#results",
          "data-markdown-source-token": "[Results](#results)"
        }),
        text(" or "),
        element("a", [text("the Day")], {
          href: "#/day",
          "data-markdown-source-token": "[the Day](#/day)"
        }),
        text(" or "),
        element("a", [text("elsewhere")], {
          href: "https://example.com/page",
          "data-markdown-source-token": "[elsewhere](https://example.com/page)"
        })
      ])
    );
  } else if (source.includes("[Dev]")) {
    rendered.appendChild(
      element("p", [element("a", [text("Dev")], {
        href: "http://localhost:4173/nested/page?theme=dark#result",
        "data-markdown-source-token":
          "[Dev](http://localhost:4173/nested/page?theme=dark#result)"
      })])
    );
  } else if (source.includes("[Doc]")) {
    rendered.appendChild(
      element("p", [text("Before "), element("a", [text("Doc")], {
        href: "/files/tickets/t_demo/doc.md",
        "data-markdown-source-token": "[Doc](/files/tickets/t_demo/doc.md)"
      }), text(" after")])
    );
  } else {
    rendered.appendChild(element("p", [text(source)]));
  }
  return rendered;
}

function editableText(value) {
  return (value || "").replace(/\u200b/g, "").replace(/\u00a0/g, " ");
}

function serializeInlineChildren(parent) {
  return Array.from(parent.childNodes).map(serializeInlineNode).join("");
}

function serializeInlineNode(node) {
  if (node.nodeType === FakeNode.TEXT_NODE) return editableText(node.textContent);
  if (!(node instanceof FakeElement)) return "";
  if (node.hasAttribute("data-markdown-caret-guard")) return editableText(node.textContent);
  const atomicToken = node.getAttribute("data-markdown-source-token");
  if (node.getAttribute("data-markdown-atomic-slot") === "true" && atomicToken !== null) {
    return atomicToken;
  }
  if (node.tagName === "BR") return "\n";
  if (node.tagName === "STRONG" || node.tagName === "B") {
    return `**${serializeInlineChildren(node)}**`;
  }
  if (node.tagName === "EM" || node.tagName === "I") return `*${serializeInlineChildren(node)}*`;
  if (node.tagName === "CODE") return `\`${node.textContent || ""}\``;
  if (node.tagName === "A") {
    const href = node.getAttribute("href");
    const label = serializeInlineChildren(node);
    return href ? `[${label}](${href})` : label;
  }
  return serializeInlineChildren(node);
}

function serializeForTest(parent) {
  const blocks = [];
  let textValue = "";
  for (const child of Array.from(parent.childNodes)) {
    if (child.nodeType === FakeNode.TEXT_NODE) {
      textValue += editableText(child.textContent);
      continue;
    }
    if (!(child instanceof FakeElement)) continue;
    let block = null;
    if (child.tagName === "H1" || child.tagName === "H2" || child.tagName === "H3") {
      block = `${"#".repeat(Number(child.tagName.slice(1)))} ${serializeInlineChildren(child)}`;
    } else if (child.tagName === "P") {
      block = serializeInlineChildren(child);
    } else if (child.tagName === "UL" || child.tagName === "OL") {
      block = child.children
        .filter((item) => item.tagName === "LI")
        .map((item, index) => {
          const marker = child.tagName === "OL" ? `${index + 1}. ` : "- ";
          return `${marker}${serializeInlineChildren(item).replace(/\n+/g, " ")}`;
        })
        .join("\n");
    } else if (child.tagName === "PRE") {
      const code = child.querySelector("code");
      block = `\`\`\`\n${code ? code.textContent || "" : child.textContent || ""}\n\`\`\``;
    } else if (child.tagName === "DIV") {
      block = serializeForTest(child) || serializeInlineChildren(child);
    }
    if (block === null) {
      textValue += serializeInlineNode(child);
      continue;
    }
    if (textValue !== "") {
      blocks.push(textValue);
      textValue = "";
    }
    if (block !== "") blocks.push(block);
  }
  if (textValue !== "") blocks.push(textValue);
  return blocks.join("\n\n");
}

globalThis.__managedMarkdownPipeline = {
  renderMarkdownToElement: renderedMarkdown,
  serializeMarkdownDomToSource: serializeForTest
};

globalThis.window = {
  location: { origin: "https://panels.test" }
};

const ticketDevServerLinkCompiled = ts.transpileModule(ticketDevServerLinkSource, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
    verbatimModuleSyntax: true
  }
}).outputText;
const helperTempDir = await mkdtemp(join(tmpdir(), "planner-ticket-dev-server-link-"));
const helperModulePath = join(helperTempDir, "ticketDevServerLink.mjs");
await writeFile(helperModulePath, ticketDevServerLinkCompiled, "utf8");
const { ticketDevServerHref } = await import(helperModulePath);
await rm(helperTempDir, { recursive: true, force: true });
globalThis.__managedMarkdownTicketDevServerLink = { ticketDevServerHref };

assert.equal(
  ticketDevServerHref(
    "http://localhost:4173/nested/page?theme=dark#result",
    "t_demo"
  ),
  "/dev/tickets/t_demo/4173/nested/page?theme=dark#result"
);
assert.equal(
  ticketDevServerHref("http://127.0.0.1:80?ready=yes#top", "ticket / one"),
  "/dev/tickets/ticket%20%2F%20one/80/?ready=yes#top"
);
for (const unchanged of [
  "http://localhost/path",
  "https://localhost:4173/path",
  "http://localhost.example:4173/path",
  "http://[::1]:4173/path",
  "http://user@localhost:4173/path",
  "http://127.0.0.1:0/path",
  "http://127.0.0.1:65536/path",
  "/already/local"
]) {
  assert.equal(ticketDevServerHref(unchanged, "t_demo"), unchanged);
}

const executableSource = ownerSource
  .replace(
    'import { mount, unmount } from "svelte";',
    "const { mount, unmount } = globalThis.__managedMarkdownSvelte;"
  )
  .replace(
    'import FilePreview from "../components/FilePreview.svelte";',
    "const FilePreview = globalThis.__managedMarkdownSvelte.FilePreview;"
  )
  .replace(
    'import { resolvePreview, targetFromHref } from "./filePreview";',
    "const { resolvePreview, targetFromHref } = globalThis.__managedMarkdownFilePreview;"
  )
  .replace(
    'import { renderMarkdownToElement, serializeMarkdownDomToSource } from "./markdownPipeline";',
    "const { renderMarkdownToElement, serializeMarkdownDomToSource } = globalThis.__managedMarkdownPipeline;"
  )
  .replace(
    'import { ticketDevServerHref } from "./ticketDevServerLink";',
    "const { ticketDevServerHref } = globalThis.__managedMarkdownTicketDevServerLink;"
  );
const compiled = ts.transpileModule(executableSource, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
    verbatimModuleSyntax: true
  }
}).outputText;
const tempDir = await mkdtemp(join(tmpdir(), "planner-managed-markdown-"));
const modulePath = join(tempDir, "managedMarkdown.mjs");
await writeFile(modulePath, compiled, "utf8");
const { createManagedMarkdownSurface } = await import(modulePath);
await rm(tempDir, { recursive: true, force: true });

function host() {
  const node = element("div");
  appRoot.appendChild(node);
  return node;
}

// Read-only presentation and full-tuple identity.
const readOnlyHost = host();
const readOnly = createManagedMarkdownSurface(readOnlyHost, { mode: "read-only" });
readOnly.update({ source: null, emptyText: "(none)", depth: 0, visited: [] });
assert.equal(readOnly.mode, "read-only");
assert.equal(readOnlyHost.children[0].className, "quiet-line");
assert.equal(readOnlyHost.children[0].textContent, "(none)");

readOnly.update({ source: "safe <text>", emptyText: "(none)", depth: 0, visited: [] });
assert.ok(readOnlyHost.children[0].classList.contains("markdown-block"));
assert.equal(readOnlyHost.children[0].textContent, "safe <text>");

const ordinaryLinkHost = host();
const ordinaryLinks = createManagedMarkdownSurface(ordinaryLinkHost, { mode: "read-only" });
ordinaryLinks.update({
  source: "[Dev](http://localhost:4173/nested/page?theme=dark#result)",
  emptyText: "",
  depth: 0,
  visited: []
});
assert.equal(
  ordinaryLinkHost.querySelector("a[href]").getAttribute("href"),
  "http://localhost:4173/nested/page?theme=dark#result"
);

const ticketLinkHost = host();
const ticketLinks = createManagedMarkdownSurface(ticketLinkHost, { mode: "read-only" });
ticketLinks.update({
  source: "[Dev](http://localhost:4173/nested/page?theme=dark#result)",
  emptyText: "",
  depth: 0,
  visited: [],
  ticketId: "t_demo"
});
assert.equal(
  ticketLinkHost.querySelector("a[href]").getAttribute("href"),
  "/dev/tickets/t_demo/4173/nested/page?theme=dark#result"
);

readOnly.update({
  source: "[Doc](ignored)",
  emptyText: "(none)",
  depth: 1,
  visited: ["first"]
});
const stableRendered = readOnlyHost.children[0];
const stableSlot = stableRendered.querySelectorAll("a[href]").length
  ? null
  : stableRendered.children[0].children[0];
const mountCount = mountCalls.length;
const unmountCount = unmountCalls.length;
readOnly.update({
  source: "[Doc](ignored)",
  emptyText: "(none)",
  depth: 1,
  visited: ["first"]
});
assert.equal(readOnlyHost.children[0], stableRendered);
assert.ok(stableRendered.contains(stableSlot));
assert.equal(mountCalls.length, mountCount);
assert.equal(unmountCalls.length, unmountCount);
assert.deepEqual(mountCalls.at(-1).options.props, {
  target: { kind: "ticket-file", ticketId: "t_demo", path: "doc.md" },
  depth: 1,
  visited: ["first"]
});

readOnly.update({
  source: "[Doc](ignored)",
  emptyText: "changed tuple",
  depth: 1,
  visited: ["first"]
});
assert.notEqual(readOnlyHost.children[0], stableRendered);
assert.equal(unmountCalls.length, unmountCount + 1);
const postChangeChild = readOnlyHost.children[0];
readOnly.destroy();
readOnly.destroy();
readOnly.update({ source: "after destroy", emptyText: "x", depth: 0, visited: [] });
assert.equal(readOnlyHost.children[0], postChangeChild);
assert.equal(unmountCalls.length, unmountCount + 2);

const imageReadOnlyHost = host();
const imageReadOnly = createManagedMarkdownSurface(imageReadOnlyHost, { mode: "read-only" });
const imageMountCount = mountCalls.length;
imageReadOnly.update({
  source: "![Attached image](/files/tickets/t_demo/images/shot.png)",
  emptyText: "",
  depth: 0,
  visited: []
});
assert.equal(imageReadOnlyHost.querySelectorAll("img[src]").length, 0);
assert.equal(mountCalls.length, imageMountCount + 1);
assert.deepEqual(mountCalls.at(-1).options.props.target, {
  kind: "ticket-file",
  ticketId: "t_demo",
  path: "images/shot.png"
});
imageReadOnly.destroy();

// An image from another origin is previewed exactly like a managed one.
const remoteImageHost = host();
const remoteImageReadOnly = createManagedMarkdownSurface(remoteImageHost, { mode: "read-only" });
const remoteImageMountCount = mountCalls.length;
remoteImageReadOnly.update({
  source: "![Remote image](https://example.com/remote.png)",
  emptyText: "",
  depth: 0,
  visited: []
});
assert.equal(remoteImageHost.querySelectorAll("img[src]").length, 0);
assert.equal(mountCalls.length, remoteImageMountCount + 1);
assert.deepEqual(mountCalls.at(-1).options.props.target, {
  kind: "external-link",
  href: "https://example.com/remote.png",
  label: "Remote image"
});
remoteImageReadOnly.destroy();

// A link that does not name a managed file stays the anchor markdown rendered:
// a same-page anchor, an app route, and an off-site address are all left alone.
const linkHost = host();
const linkReadOnly = createManagedMarkdownSurface(linkHost, { mode: "read-only" });
const linkMountCount = mountCalls.length;
linkReadOnly.update({
  source: "Jump to [Results](#results) or [the Day](#/day) or [elsewhere](https://example.com/page)",
  emptyText: "",
  depth: 0,
  visited: []
});
assert.equal(mountCalls.length, linkMountCount);
assert.deepEqual(
  linkHost.querySelectorAll("a[href]").map((anchor) => anchor.getAttribute("href")),
  ["#results", "#/day", "https://example.com/page"]
);
linkReadOnly.destroy();

// An image inside a link the preview system left standing keeps both: the link is
// still a link and the image is still an image, rather than one card replacing both.
const linkedImageHost = host();
const linkedImageReadOnly = createManagedMarkdownSurface(linkedImageHost, { mode: "read-only" });
const linkedImageMountCount = mountCalls.length;
linkedImageReadOnly.update({
  source:
    "[![Attached image](/files/tickets/t_demo/images/shot.png)](https://example.com/image-link)",
  emptyText: "",
  depth: 0,
  visited: []
});
assert.equal(mountCalls.length, linkedImageMountCount);
assert.deepEqual(
  linkedImageHost.querySelectorAll("a[href]").map((anchor) => anchor.getAttribute("href")),
  ["https://example.com/image-link"]
);
assert.deepEqual(
  linkedImageHost.querySelectorAll("img[src]").map((image) => image.getAttribute("src")),
  ["/files/tickets/t_demo/images/shot.png"]
);
linkedImageReadOnly.destroy();

// Editable first paint, dirty same-source reset, observer reuse, and final-DOM reconciliation.
const editableHost = host();
editableHost.appendChild(element("span", [text("stale")]))
const editable = createManagedMarkdownSurface(editableHost, { mode: "editable" });
editable.update("");
assert.equal(editable.mode, "editable");
assert.equal(editableHost.textContent, "");
assert.equal(editableHost.getAttribute("data-empty"), "true");

editable.update("[Doc](ignored)");
const pristineNode = editableHost.children[0];
const editableSlot = pristineNode.children[0].children.find(
  (child) => child.getAttribute("data-markdown-atomic-slot") === "true"
);
const editableMount = mountCalls.at(-1);
assert.ok(editableSlot);
assert.equal(editableSlot.contentEditable, "false");
assert.equal(
  editableSlot.getAttribute("data-markdown-source-token"),
  "[Doc](/files/tickets/t_demo/doc.md)"
);
assert.equal(observerInstances.length, 1);
editable.update("[Doc](ignored)");
assert.equal(editableHost.children[0], pristineNode);

editableHost.dispatchEvent(new FakeInputEvent("input", { bubbles: true }));
assert.equal(editable.hasChanges(), true);
editable.update("[Doc](ignored)");
assert.equal(editable.hasChanges(), false);
assert.notEqual(editableHost.children[0], pristineNode);
assert.equal(observerInstances.length, 1);
assert.ok(observerInstances[0].disconnectCalls >= 1);
assert.ok(observerInstances[0].observeCalls.length >= 2);
assert.equal(unmountCalls.filter((handle) => handle === editableMount).length, 1);

const movedSlot = editableHost.children[0].children[0].children.find(
  (child) => child.getAttribute("data-markdown-atomic-slot") === "true"
);
const movedHandle = mountCalls.at(-1);
const movedParent = movedSlot.parentNode;
movedParent.removeChild(movedSlot);
movedParent.appendChild(movedSlot);
observerInstances[0].emit([{ removedNodes: [movedSlot] }]);
assert.equal(unmountCalls.filter((handle) => handle === movedHandle).length, 0);

movedParent.removeChild(movedSlot);
observerInstances[0].emit([{ removedNodes: [movedSlot] }]);
observerInstances[0].emit([{ removedNodes: [movedSlot] }]);
assert.equal(unmountCalls.filter((handle) => handle === movedHandle).length, 1);

// Full preserved serializer matrix, including generated-descendant exclusion.
const serializerHost = host();
const serializer = createManagedMarkdownSurface(serializerHost, { mode: "editable" });
serializer.update("seed");
const exactToken = "[Doc](/files/tickets/t_demo/space%20name.md?raw=1#part)";
const markdownBlock = element("div", [
  element("h1", [text("Heading")]),
  element("p", [
    text("Before\u00a0"),
    element("strong", [text("bold")]),
    text(" and "),
    element("em", [text("italic")]),
    text(" "),
    element("code", [text("inline")]),
    text(" "),
    element("a", [text("ordinary")], { href: "https://example.com" }),
    element("br"),
    element("span", [text("\u200btyped")], { "data-markdown-caret-guard": "after" })
  ]),
  element("ul", [element("li", [text("one")]), element("li", [text("two")])]),
  element("ol", [element("li", [text("first")]), element("li", [text("second")])]),
  element("pre", [element("code", [text("const x = 1;")])]),
  element("div", [text("browser block")]),
  element("p", [
    text("A "),
    element("span", [element("article", [text("generated preview")])], {
      "data-markdown-atomic-slot": "true",
      "data-markdown-source-token": exactToken
    }),
    element("span", [text("\u200b")], { "data-markdown-caret-guard": "after" }),
    element("span", [element("iframe", [text("generated html")])], {
      "data-markdown-atomic-slot": "true",
      "data-markdown-source-token": "[Image](/files/tickets/t_demo/image.png)"
    }),
    text(" C")
  ])
]);
markdownBlock.classList.add("markdown-block");
serializerHost.replaceChildren(markdownBlock);
serializerHost.dispatchEvent(new FakeInputEvent("input", { bubbles: true }));
const serialized = serializer.read();
assert.equal(
  serialized,
  "# Heading\n\nBefore **bold** and *italic* `inline` [ordinary](https://example.com)\n" +
    "typed\n\n- one\n- two\n\n1. first\n2. second\n\n```\nconst x = 1;\n```\n\n" +
    "browser block\n\n" +
    `A ${exactToken}[Image](/files/tickets/t_demo/image.png) C`
);
assert.equal(serializer.hasChanges(), true);
serializer.refreshEmptyState();
assert.equal(serializer.hasChanges(), true);
assert.equal(serializerHost.getAttribute("data-empty"), null);

serializerHost.replaceChildren(
  element("div", [
    text("A "),
    element("span", [text("generated")], {
      "data-markdown-atomic-slot": "true",
      "data-markdown-source-token": "[Image](/files/tickets/t_demo/image.png)"
    }),
    text(" C")
  ])
);
assert.equal(serializer.read(), "A [Image](/files/tickets/t_demo/image.png) C");

const destroyedChild = editableHost.children[0];
const editableUnmountCount = unmountCalls.length;
editable.destroy();
editable.destroy();
editable.update("remount forbidden");
editableHost.dispatchEvent(new FakeInputEvent("input", { bubbles: true }));
assert.equal(editableHost.children[0], destroyedChild);
assert.equal(editable.hasChanges(), false);
assert.equal(unmountCalls.length, editableUnmountCount);
