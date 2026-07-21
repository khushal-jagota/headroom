import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { basename, join } from "node:path";
import ts from "typescript";

const webRoot = new URL("..", import.meta.url);
const moduleUrl = new URL("../src/lib/markdownPipeline.ts", import.meta.url);

class FakeText {
  nodeType = 3;
  parentNode = null;

  constructor(text) {
    this.textContent = text;
  }
}

class FakeElement {
  nodeType = 1;
  parentNode = null;
  attributes = new Map();
  childNodes = [];

  constructor(tagName) {
    this.tagName = tagName.toUpperCase();
    this.classList = {
      add: (...names) => {
        const current = new Set((this.getAttribute("class") || "").split(/\s+/).filter(Boolean));
        for (const name of names) current.add(name);
        this.setAttribute("class", [...current].join(" "));
      }
    };
  }

  get children() {
    return this.childNodes.filter((child) => child instanceof FakeElement);
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

  replaceChildren(...children) {
    for (const child of this.childNodes) child.parentNode = null;
    this.childNodes = [];
    for (const child of children) if (child) this.appendChild(child);
  }

  removeChild(child) {
    const index = this.childNodes.indexOf(child);
    if (index >= 0) this.childNodes.splice(index, 1);
    child.parentNode = null;
    return child;
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

  querySelectorAll(selector) {
    const matches = [];
    const match = (element) => {
      if (selector === "script") return element.tagName === "SCRIPT";
      if (selector === "style") return element.tagName === "STYLE";
      if (selector === "[id]") return element.hasAttribute("id");
      if (selector === "[style]") return element.hasAttribute("style");
      if (selector === "[onclick]") return element.hasAttribute("onclick");
      if (selector === "a[href]") return element.tagName === "A" && element.hasAttribute("href");
      if (selector === "img[src]") return element.tagName === "IMG" && element.hasAttribute("src");
      if (selector === "table") return element.tagName === "TABLE";
      if (selector === "input[type='checkbox']") {
        return element.tagName === "INPUT" && element.getAttribute("type") === "checkbox";
      }
      return false;
    };
    const visit = (node) => {
      if (!(node instanceof FakeElement)) return;
      if (match(node)) matches.push(node);
      for (const child of node.childNodes) visit(child);
    };
    visit(this);
    return matches;
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }
}

globalThis.document = {
  createElement: (tagName) => new FakeElement(tagName),
  createTextNode: (text) => new FakeText(text)
};
globalThis.HTMLElement = FakeElement;
globalThis.Node = { TEXT_NODE: 3, ELEMENT_NODE: 1 };

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function html(node) {
  if (node instanceof FakeText) return escapeHtml(node.textContent || "");
  const attrs = [...node.attributes]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([name, value]) => ` ${name}="${escapeHtml(value)}"`)
    .join("");
  return `<${node.tagName.toLowerCase()}${attrs}>${node.childNodes.map(html).join("")}</${node.tagName.toLowerCase()}>`;
}

const source = await readFile(moduleUrl, "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
    verbatimModuleSyntax: true
  }
}).outputText;
const tempDir = await mkdtemp(join(new URL(".", webRoot).pathname, ".tmp-markdown-pipeline-"));
const modulePath = join(tempDir, `${basename(moduleUrl.pathname, ".ts")}.mjs`);
await writeFile(modulePath, compiled, "utf8");
const { renderMarkdownToElement, serializeMarkdownDomToSource } = await import(modulePath);
await rm(tempDir, { recursive: true, force: true });

const gfm = [
  "# Heading",
  "",
  "> quote",
  "",
  "1. ordered",
  "   - mixed bullet",
  "     continuation text",
  "   - second bullet",
  "2. next ordered",
  "",
  "```ts",
  "const value = 1;",
  "```",
  "",
  "---",
  "",
  "[reference][ref] and [escaped \\] link](/files/tickets/t_demo/a%20b.md?raw=1#frag)",
  "",
  "![alt text](https://example.com/image.png)",
  "",
  "| A | B |",
  "| - | - |",
  "| one | two |",
  "",
  "- [x] done",
  "- [ ] waiting",
  "",
  "~~struck~~ and https://example.com/autolink",
  "",
  "[ref]: https://example.com/ref"
].join("\n");

const renderedGfm = renderMarkdownToElement(gfm);
assert.match(html(renderedGfm), /<h1>Heading<\/h1>/);
assert.match(html(renderedGfm), /<blockquote>\s*<p>quote<\/p>\s*<\/blockquote>/);
assert.match(
  html(renderedGfm),
  /<ol>\s*<li>ordered\s*<ul>\s*<li>mixed bullet\s*continuation text<\/li>/
);
assert.match(html(renderedGfm), /<pre><code class="language-ts">const value = 1;\n<\/code><\/pre>/);
assert.match(html(renderedGfm), /<hr><\/hr>/);
assert.match(
  html(renderedGfm),
  /<table>\s*<thead>\s*<tr>\s*<th>A<\/th>\s*<th>B<\/th>\s*<\/tr>\s*<\/thead>/
);
assert.equal(renderedGfm.querySelectorAll("input[type='checkbox']").length, 2);
assert.match(html(renderedGfm), /<del>struck<\/del>/);
assert.equal(renderedGfm.querySelectorAll("img[src]").length, 1);
assert.ok(
  renderedGfm.textContent.includes("https://example.com/autolink"),
  "autolink text is rendered"
);

const exactLinkSource =
  "[escaped \\] link](/files/tickets/t_demo/a%20b.md?raw=1#frag)";
const exactAnchor = renderedGfm
  .querySelectorAll("a[href]")
  .find((anchor) => anchor.textContent === "escaped ] link");
assert.equal(exactAnchor?.getAttribute("data-markdown-source-token"), exactLinkSource);
const referenceAnchor = renderedGfm
  .querySelectorAll("a[href]")
  .find((anchor) => anchor.textContent === "reference");
assert.equal(referenceAnchor?.getAttribute("data-markdown-source-token"), "[reference][ref]");

const managedReferenceSource = [
  "Before [Managed][asset] after.",
  "",
  '[asset]: /files/tickets/t_demo/reference.md "Reference title"'
].join("\n");
const managedReferenceDom = renderMarkdownToElement(managedReferenceSource);
const managedReferenceAnchor = managedReferenceDom.querySelector("a[href]");
const managedReferenceSlot = document.createElement("span");
managedReferenceSlot.setAttribute("data-markdown-atomic-slot", "true");
managedReferenceSlot.setAttribute(
  "data-markdown-source-token",
  managedReferenceAnchor.getAttribute("data-markdown-source-token")
);
managedReferenceSlot.setAttribute(
  "data-markdown-reference-identifier",
  managedReferenceAnchor.getAttribute("data-markdown-reference-identifier")
);
managedReferenceAnchor.parentNode.childNodes.splice(
  managedReferenceAnchor.parentNode.childNodes.indexOf(managedReferenceAnchor),
  1,
  managedReferenceSlot
);
managedReferenceSlot.parentNode = managedReferenceAnchor.parentNode;
const serializedManagedReference = serializeMarkdownDomToSource(managedReferenceDom);
assert.match(serializedManagedReference, /\[Managed\]\[asset\]/);
assert.match(
  serializedManagedReference,
  /\[asset\]: \/files\/tickets\/t_demo\/reference\.md "Reference title"/
);
assert.equal(
  renderMarkdownToElement(serializedManagedReference).querySelector("a[href]")?.getAttribute("href"),
  "/files/tickets/t_demo/reference.md"
);
managedReferenceSlot.parentNode.removeChild(managedReferenceSlot);
assert.doesNotMatch(serializeMarkdownDomToSource(managedReferenceDom), /\[asset\]:/);

const referenceImageSource = [
  "![Screenshot][shot]",
  "",
  "[shot]: /files/tickets/t_demo/screenshot.png"
].join("\n");
const serializedReferenceImage = serializeMarkdownDomToSource(
  renderMarkdownToElement(referenceImageSource)
);
assert.match(serializedReferenceImage, /!\[Screenshot\]\[shot\]/);
assert.match(serializedReferenceImage, /\[shot\]: \/files\/tickets\/t_demo\/screenshot\.png/);

const hostile = [
  '<script>globalThis.__ran = true</script>',
  '<img src=x onerror="globalThis.__ran = true">',
  '<a href="javascript:globalThis.__ran = true" onclick="globalThis.__ran = true" id="location" style="color:red">bad</a>',
  '<form name="constructor"></form>',
  '[unsafe](javascript:alert(1))',
  '![unsafe](vbscript:alert(1))'
].join("\n\n");
const renderedHostile = renderMarkdownToElement(hostile);
const hostileHtml = html(renderedHostile);
assert.equal(renderedHostile.querySelectorAll("script").length, 0);
assert.equal(renderedHostile.querySelectorAll("[onclick]").length, 0);
assert.equal(renderedHostile.querySelectorAll("[id]").length, 0);
assert.equal(renderedHostile.querySelectorAll("[style]").length, 0);
assert.ok(renderedHostile.textContent.includes("<script>globalThis.__ran = true</script>"));
assert.ok(renderedHostile.textContent.includes('<img src=x onerror="globalThis.__ran = true">'));
assert.doesNotMatch(hostileHtml, /href="javascript:|src="vbscript:/);

const editableDom = renderMarkdownToElement([
  "1. Parent",
  "   - Child",
  "     continued",
  "",
  "| Item | Done |",
  "| - | - |",
  "| Task | yes |",
  "",
  "- [x] completed",
  "- [ ] pending"
].join("\n"));
assert.equal(
  serializeMarkdownDomToSource(editableDom),
  [
    "1. Parent",
    "   - Child continued",
    "",
    "| Item | Done |",
    "| ---- | ---- |",
    "| Task | yes  |",
    "",
    "- [x] completed",
    "- [ ] pending"
  ].join("\n")
);

const previewDom = renderMarkdownToElement("A [Doc](/files/tickets/t_demo/doc.md) C");
const anchor = previewDom.querySelector("a[href]");
const slot = document.createElement("span");
slot.setAttribute("data-markdown-atomic-slot", "true");
slot.setAttribute("data-markdown-source-token", anchor.getAttribute("data-markdown-source-token"));
slot.appendChild(document.createElement("article")).textContent = "generated preview descendant";
anchor.parentNode.childNodes.splice(anchor.parentNode.childNodes.indexOf(anchor), 1, slot);
slot.parentNode = anchor.parentNode;
assert.equal(
  serializeMarkdownDomToSource(previewDom),
  "A [Doc](/files/tickets/t_demo/doc.md) C"
);

const collisionSource =
  "PANELSMMATOMICPLACEHOLDER10END A " +
  "[PANELSMMATOMICPLACEHOLDER0END](/files/tickets/t_demo/doc.md) C";
const collisionDom = renderMarkdownToElement(collisionSource);
const collisionAnchor = collisionDom.querySelector("a[href]");
const collisionSlot = document.createElement("span");
const collisionToken = collisionAnchor.getAttribute("data-markdown-source-token");
collisionSlot.setAttribute("data-markdown-atomic-slot", "true");
collisionSlot.setAttribute("data-markdown-source-token", collisionToken);
collisionAnchor.parentNode.childNodes.splice(
  collisionAnchor.parentNode.childNodes.indexOf(collisionAnchor),
  1,
  collisionSlot
);
collisionSlot.parentNode = collisionAnchor.parentNode;
assert.equal(serializeMarkdownDomToSource(collisionDom), collisionSource);

const attributeCollisionDom = document.createElement("div");
const attributeCollisionParagraph = document.createElement("p");
const plainAnchor = document.createElement("a");
const attributePlaceholder = "PANELSMMATOMICPLACEHOLDER0BOUNDARY0END";
plainAnchor.setAttribute("href", `https://example.com/${attributePlaceholder}`);
plainAnchor.textContent = "Plain";
const attributeCollisionSlot = document.createElement("span");
const attributeCollisionToken = "[Doc](/files/tickets/t_demo/doc.md)";
attributeCollisionSlot.setAttribute("data-markdown-atomic-slot", "true");
attributeCollisionSlot.setAttribute("data-markdown-source-token", attributeCollisionToken);
attributeCollisionParagraph.appendChild(plainAnchor);
attributeCollisionParagraph.appendChild(document.createTextNode(" "));
attributeCollisionParagraph.appendChild(attributeCollisionSlot);
attributeCollisionDom.appendChild(attributeCollisionParagraph);
const serializedAttributeCollision = serializeMarkdownDomToSource(attributeCollisionDom);
assert.match(
  serializedAttributeCollision,
  /\[Plain\]\(https:\/\/example\.com\/PANELSMMATOMICPLACEHOLDER0BOUNDARY0END\)/
);
assert.match(serializedAttributeCollision, /\[Doc\]\(\/files\/tickets\/t_demo\/doc\.md\)/);
