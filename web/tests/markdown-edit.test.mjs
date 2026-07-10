import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import ts from "typescript";

const source = (await readFile(new URL("../src/lib/markdownEdit.ts", import.meta.url), "utf8"))
  .replace('import { mountFilePreviews } from "./filePreviewMount";', "");
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
    verbatimModuleSyntax: true
  }
}).outputText;
const dir = await mkdtemp(join(tmpdir(), "planner-markdown-edit-"));
const modulePath = join(dir, "markdownEdit.mjs");
await writeFile(modulePath, compiled, "utf8");

class FakeNode {
  static TEXT_NODE = 3;
  static ELEMENT_NODE = 1;
}

class FakeText {
  nodeType = FakeNode.TEXT_NODE;
  constructor(text) {
    this.textContent = text;
  }
}

class FakeElement {
  nodeType = FakeNode.ELEMENT_NODE;
  attributes = new Map();
  childNodes = [];
  children = [];
  classList = { contains: () => false };

  constructor(tagName, children = [], attributes = {}) {
    this.tagName = tagName;
    for (const [name, value] of Object.entries(attributes)) this.attributes.set(name, value);
    for (const child of children) this.append(child);
  }

  append(child) {
    this.childNodes.push(child);
    if (child instanceof FakeElement) this.children.push(child);
  }

  get textContent() {
    return this.childNodes.map((child) => child.textContent || "").join("");
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  hasAttribute(name) {
    return this.attributes.has(name);
  }

  setAttribute(name, value) {
    this.attributes.set(name, value);
  }

  removeAttribute(name) {
    this.attributes.delete(name);
  }

  querySelector(selector) {
    if (selector !== "code") return null;
    return this.children.find((child) => child.tagName === "CODE") || null;
  }
}

globalThis.Node = FakeNode;
globalThis.HTMLElement = FakeElement;

const { readMarkdownEditable } = await import(modulePath);
await rm(dir, { recursive: true, force: true });

const token = "[Doc](/files/tickets/t_tok123/notes/space%20name.md?raw=1#part)";
const root = new FakeElement("DIV", [
  new FakeText("Before "),
  new FakeElement(
    "SPAN",
    [
      new FakeElement("ARTICLE", [
        new FakeElement("IMG", [], { src: "/generated-preview.png" }),
        new FakeText("generated preview text")
      ])
    ],
    { "data-markdown-atomic-slot": "true", "data-markdown-source-token": token }
  ),
  new FakeText(" after")
]);

assert.equal(readMarkdownEditable(root), `Before ${token} after`);

const secondToken = "[Image](/files/tickets/t_tok123/images/pic.png)";
const adjacent = new FakeElement("DIV", [
  new FakeText("A "),
  new FakeElement("SPAN", [new FakeElement("IMG")], {
    "data-markdown-atomic-slot": "true",
    "data-markdown-source-token": token
  }),
  new FakeText(" B "),
  new FakeElement("SPAN", [new FakeElement("IFRAME"), new FakeText("generated html")], {
    "data-markdown-atomic-slot": "true",
    "data-markdown-source-token": secondToken
  }),
  new FakeText(" C")
]);

assert.equal(readMarkdownEditable(adjacent), `A ${token} B ${secondToken} C`);

const afterDeletion = new FakeElement("DIV", [
  new FakeText("A "),
  new FakeElement("SPAN", [new FakeElement("IFRAME"), new FakeText("generated html")], {
    "data-markdown-atomic-slot": "true",
    "data-markdown-source-token": secondToken
  }),
  new FakeText(" C")
]);

assert.equal(readMarkdownEditable(afterDeletion), `A ${secondToken} C`);

const caretGuard = new FakeElement("SPAN", [new FakeText("\u200btyped after")], {
  "data-markdown-caret-guard": "after"
});
assert.equal(readMarkdownEditable(new FakeElement("DIV", [caretGuard])), "typed after");
assert.equal(
  readMarkdownEditable(new FakeElement("DIV", [new FakeText("Before\u00a0preview")])),
  "Before preview"
);
