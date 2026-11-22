import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkGfm from "remark-gfm";
import remarkRehype from "remark-rehype";
import rehypeSanitize, { type Options as SanitizeSchema } from "rehype-sanitize";
import rehypeToRemark from "rehype-remark";
import remarkStringify from "remark-stringify";
import { visit } from "unist-util-visit";
import { parseChatChart } from "./markdown/charts/validation";
import type { MarkdownChart } from "./markdown/charts/contracts";

type Position = {
  start?: { offset?: number };
  end?: { offset?: number };
};

type MarkdownNode = {
  type: string;
  value?: string;
  lang?: string | null;
  meta?: string | null;
  identifier?: string;
  label?: string;
  url?: string;
  title?: string | null;
  children?: MarkdownNode[];
  position?: Position;
  data?: { hProperties?: Record<string, unknown> };
};

import type { MarkdownTreeNode as HastNode, MarkdownReferenceDefinition as ReferenceDefinition, ParsedMarkdown } from "./markdown/contracts";

type AtomicPlaceholder = {
  placeholder: string;
  sourceToken: string;
};

const chartsByElement = new WeakMap<HTMLElement, MarkdownChart>();
export function markdownChartForElement(element: HTMLElement): MarkdownChart | undefined {
  return chartsByElement.get(element);
}

const referenceDefinitionsByRoot = new WeakMap<Node, ReferenceDefinition[]>();

const sanitizeSchema: SanitizeSchema = {
  tagNames: [
    "a",
    "blockquote",
    "br",
    "code",
    "del",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "img",
    "input",
    "li",
    "ol",
    "p",
    "pre",
    "strong",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "ul"
  ],
  attributes: {
    a: ["href", "title", "dataMarkdownSourceToken", "dataMarkdownReferenceIdentifier"],
    code: [["className", /^language-[\w-]+$/]],
    img: ["src", "alt", "title", "dataMarkdownSourceToken", "dataMarkdownReferenceIdentifier"],
    input: [["type", "checkbox"], "checked", "disabled"],
    td: ["align"],
    th: ["align"]
  },
  protocols: {
    href: ["http", "https", "mailto"],
    src: ["http", "https"]
  },
  clobberPrefix: "user-content-",
  clobber: ["ariaDescribedBy", "ariaLabelledBy", "id", "name"]
};

function literalHtmlPlugin() {
  return (tree: MarkdownNode): void => {
    replaceLiteralHtml(tree);
  };
}

function replaceLiteralHtml(node: MarkdownNode): void {
  if (!node.children) return;
  node.children = node.children.map((child) => {
    if (child.type !== "html") {
      replaceLiteralHtml(child);
      return child;
    }
    const textNode: MarkdownNode = { type: "text", value: child.value || "" };
    if (node.type === "paragraph" || node.type === "heading") return textNode;
    return { type: "paragraph", children: [textNode] };
  });
}

function sourceTokenPlugin(source: string) {
  return (tree: MarkdownNode): void => {
    visit(tree as never, (node: MarkdownNode) => {
      if (
        node.type !== "link" &&
        node.type !== "image" &&
        node.type !== "linkReference" &&
        node.type !== "imageReference"
      ) {
        return;
      }
      const start = node.position?.start?.offset;
      const end = node.position?.end?.offset;
      if (typeof start !== "number" || typeof end !== "number" || end <= start) return;
      node.data = node.data || {};
      const hProperties: Record<string, unknown> = {
        ...(node.data.hProperties || {}),
        dataMarkdownSourceToken: source.slice(start, end)
      };
      if (
        (node.type === "linkReference" || node.type === "imageReference") &&
        node.identifier
      ) {
        hProperties.dataMarkdownReferenceIdentifier = node.identifier;
      }
      node.data.hProperties = hProperties;
    });
  };
}

const forwardProcessor = (source: string) =>
  unified()
    .use(remarkParse)
    .use(remarkGfm)
    .use(literalHtmlPlugin)
    .use(sourceTokenPlugin, source)
    .use(remarkRehype)
    .use(rehypeSanitize, sanitizeSchema);

const reverseProcessor = unified()
  .use(rehypeToRemark)
  .use(remarkGfm)
  .use(remarkStringify, {
    bullet: "-",
    fences: true,
    listItemIndent: "one",
    rule: "-",
    ruleSpaces: false
  });

/** DOM-free exact parse, shared by the main-thread editor and parser worker. */
export function parseMarkdown(source: string): ParsedMarkdown {
  const processor = forwardProcessor(source);
  const parsed = processor.parse(source);
  const definitions = collectReferenceDefinitions(parsed as unknown as MarkdownNode);
  // Code blocks retain traversal order through remark-rehype and sanitization.
  // Keep chart metadata outside HTML properties, after the sanitize boundary.
  const candidates: Array<MarkdownChart | undefined> = [];
  visit(parsed as never, (node: MarkdownNode) => {
    if (node.type !== "code") return;
    let chart: MarkdownChart | undefined;
    const start = node.position?.start?.offset, end = node.position?.end?.offset;
    if (node.lang === "panels-chart" && !node.meta && start !== undefined && end !== undefined) {
      const token = source.slice(start, end);
      const opening = /^(`{3,}|~{3,})panels-chart[ \t]*\r?\n/.exec(token);
      const closing = /^[ \t]*(?:>[ \t]*)*(`+|~+)[ \t]*\r?$/.exec(token.split("\n").at(-1) || "");
      if (opening && closing && closing[1][0] === opening[1][0] && closing[1].length >= opening[1].length) {
        const block = parseChatChart(node.value || "");
        if (block) {
          // Nested fences include quote/list prefixes after the opening line.
          // Copy a standalone fence instead of a fragment with mixed prefixes.
          const lastLine = token.split("\n").at(-1)?.replace(/\r$/, "");
          const copiedSource = lastLine === closing[1] ? token :
            `${opening[1]}panels-chart\n${node.value || ""}\n${closing[1]}`;
          chart = { block, source: copiedSource };
        }
      }
    }
    candidates.push(chart);
  });
  const tree = processor.runSync(parsed) as HastNode;
  let codeIndex = 0;
  visit(tree as never, (node: HastNode) => {
    if (node.type === "element" && node.tagName === "pre") {
      const chart = candidates[codeIndex++];
      if (chart) node.chart = chart;
    }
  });
  return { children: tree.children || [], definitions };
}

export type MarkdownTextMapper = (node: Text, blockIndex: number, path: readonly number[]) => void;

/** A file reference in plain words ("batcher.go:97", "web/src/app.ts") is code, as it would
 *  be had the writer put it in backticks: a path with a source-file extension that also
 *  names a line or a directory. A bare "Node.js" or "README.md" stays a word. */
const FILE_REFERENCE = /(?<![\w./@-])(?:[\w.-]+\/)*[\w-]+\.(?:go|ts|tsx|js|jsx|mjs|cjs|svelte|vue|py|rs|swift|kt|java|rb|css|scss|html|json|ya?ml|toml|sql|sh|c|h|cc|cpp|hpp|m|mm|php|cs|lua|zig|ex|exs|txt|log|csv|md)(?::\d+(?::\d+)?)?(?![\w/])/g;

/** The pieces of a text, with each file reference marked (only those naming a line or a
 *  directory), for a read-only conversation surface. */
export function fileReferencePieces(text: string): { text: string; reference: boolean }[] {
  const pieces: { text: string; reference: boolean }[] = [];
  let at = 0;
  for (const match of text.matchAll(FILE_REFERENCE)) {
    const found = match[0];
    if (!/:\d+$/.test(found) && !found.includes("/")) continue;
    const start = match.index ?? 0;
    if (start > at) pieces.push({ text: text.slice(at, start), reference: false });
    pieces.push({ text: found, reference: true });
    at = start + found.length;
  }
  if (at < text.length) pieces.push({ text: text.slice(at), reference: false });
  return pieces;
}

export type MarkdownRenderOptions = { fileReferences?: boolean };

export function renderParsedMarkdown(document: ParsedMarkdown, mapText?: MarkdownTextMapper, blockOffset = 0, options: MarkdownRenderOptions = {}): HTMLElement {
  const root = globalThis.document.createElement("div");
  root.className = "markdown";
  document.children.forEach((child, index) => appendHastNode(root, child, mapText, blockOffset + index, [], options.fileReferences === true));
  referenceDefinitionsByRoot.set(root, document.definitions);
  return root;
}

export function renderMarkdownToElement(source: string, mapText?: MarkdownTextMapper, options: MarkdownRenderOptions = {}): HTMLElement {
  return renderParsedMarkdown(parseMarkdown(source), mapText, 0, options);
}

/** The link and image destinations that rendered Markdown exposes to a reader. */
export function markdownLinkHrefs(source: string): string[] {
  const parsed = unified().use(remarkParse).use(remarkGfm).parse(source) as unknown as MarkdownNode;
  const definitions = new Map(
    collectReferenceDefinitions(parsed).map((definition) => [
      definition.identifier.toLocaleLowerCase(),
      definition.url
    ])
  );
  const hrefs: string[] = [];
  visit(parsed as never, (node: MarkdownNode) => {
    if ((node.type === "link" || node.type === "image") && node.url) {
      hrefs.push(node.url);
      return;
    }
    if ((node.type === "linkReference" || node.type === "imageReference") && node.identifier) {
      const href = definitions.get(node.identifier.toLocaleLowerCase());
      if (href) hrefs.push(href);
    }
  });
  return hrefs;
}

export function serializeMarkdownDomToSource(root: Node): string {
  const placeholders: AtomicPlaceholder[] = [];
  const placeholderBase = unusedPlaceholderBase(root);
  const usedReferenceIdentifiers = collectReferenceIdentifiers(root);
  const hast: HastNode = {
    type: "root",
    children: Array.from(root.childNodes).map((child) =>
      domNodeToHast(child, placeholders, placeholderBase)
    )
  };
  const mdast = reverseProcessor.runSync(hast as never) as MarkdownNode;
  const referenceDefinitions = (referenceDefinitionsByRoot.get(root) || []).filter((definition) =>
    usedReferenceIdentifiers.has(definition.identifier)
  );
  if (referenceDefinitions.length > 0) {
    mdast.children = [...(mdast.children || []), ...referenceDefinitions];
  }
  let markdown = String(reverseProcessor.stringify(mdast as never)).trimEnd();
  for (const record of placeholders) {
    markdown = markdown.split(record.placeholder).join(record.sourceToken);
  }
  return markdown;
}

const NO_FILE_REFERENCES_INSIDE = new Set(["CODE", "PRE", "A"]);

function appendHastNode(parent: HTMLElement, node: HastNode, mapText?: MarkdownTextMapper, blockIndex = 0, path: readonly number[] = [], fileReferences = false): void {
  if (node.type === "text") {
    const value = node.value || "";
    const pieces = fileReferences && !NO_FILE_REFERENCES_INSIDE.has(parent.tagName) && parent.closest("code, pre, a") === null
      ? fileReferencePieces(value) : [];
    if (pieces.some((piece) => piece.reference)) {
      // Each piece is its own text leaf, at its own path under this one, so a text location
      // taken from this render is found again in the next.
      pieces.forEach((piece, index) => {
        const text = document.createTextNode(piece.text);
        if (piece.reference) {
          const code = document.createElement("code");
          code.className = "cv-file-ref";
          code.appendChild(text);
          parent.appendChild(code);
        } else parent.appendChild(text);
        mapText?.(text, blockIndex, [...path, index]);
      });
      return;
    }
    const text = document.createTextNode(value);
    parent.appendChild(text);
    mapText?.(text, blockIndex, path);
    return;
  }
  if (node.type !== "element" || !node.tagName) return;
  const element = document.createElement(node.tagName);
  if (node.chart) chartsByElement.set(element, node.chart);
  if (element instanceof HTMLImageElement) {
    element.loading = "lazy";
    element.decoding = "async";
  }
  applySanitizedProperties(element, node.properties || {});
  // Attached before its children, so a text inside code, pre or a link can see it is there.
  parent.appendChild(element);
  (node.children || []).forEach((child, index) => appendHastNode(element, child, mapText, blockIndex, [...path, index], fileReferences));
}

function collectReferenceDefinitions(tree: MarkdownNode): ReferenceDefinition[] {
  const definitions: ReferenceDefinition[] = [];
  visit(tree as never, (node: MarkdownNode) => {
    if (node.type !== "definition" || !node.identifier || node.url === undefined) return;
    definitions.push({
      type: "definition",
      identifier: node.identifier,
      ...(node.label === undefined ? {} : { label: node.label }),
      url: node.url,
      ...(node.title === undefined ? {} : { title: node.title })
    });
  });
  return definitions;
}

function applySanitizedProperties(element: HTMLElement, properties: Record<string, unknown>): void {
  for (const [name, value] of Object.entries(properties)) {
    if (value === null || value === undefined || value === false) continue;
    if (name === "dataMarkdownSourceToken") {
      element.setAttribute("data-markdown-source-token", String(value));
      continue;
    }
    if (name === "className") {
      const className = Array.isArray(value) ? value.join(" ") : String(value);
      if (className) element.setAttribute("class", className);
      continue;
    }
    if (value === true) {
      element.setAttribute(attributeName(name), "");
      continue;
    }
    element.setAttribute(attributeName(name), Array.isArray(value) ? value.join(" ") : String(value));
  }
}

function attributeName(propertyName: string): string {
  return propertyName.replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`);
}

const MARKDOWN_SERIALIZED_ATTRIBUTE_NAMES = [
  "href",
  "src",
  "alt",
  "title",
  "align",
  "type",
  "data-markdown-source-token",
  "data-markdown-reference-identifier"
] as const;

function unusedPlaceholderBase(root: Node): string {
  const serializedInputs = [editableText(root.textContent), ...collectSerializedAttributeValues(root)];
  let nonce = 0;
  while (true) {
    const base = `PANELSMMATOMICPLACEHOLDER${nonce}BOUNDARY`;
    if (!serializedInputs.some((value) => value.includes(base))) return base;
    nonce += 1;
  }
}

function collectSerializedAttributeValues(root: Node): string[] {
  const values: string[] = [];
  collectSerializedAttributeValuesFromNode(root, values);
  return values;
}

function collectSerializedAttributeValuesFromNode(node: Node, values: string[]): void {
  if (node.nodeType !== Node.ELEMENT_NODE) return;
  const element = node as HTMLElement;
  for (const name of MARKDOWN_SERIALIZED_ATTRIBUTE_NAMES) {
    const value = element.getAttribute(name);
    if (value !== null) values.push(value);
  }
  for (const child of Array.from(element.childNodes)) {
    collectSerializedAttributeValuesFromNode(child, values);
  }
}

function collectReferenceIdentifiers(root: Node): Set<string> {
  const identifiers = new Set<string>();
  collectReferenceIdentifiersFromNode(root, identifiers);
  return identifiers;
}

function collectReferenceIdentifiersFromNode(node: Node, identifiers: Set<string>): void {
  if (node.nodeType !== Node.ELEMENT_NODE) return;
  const element = node as HTMLElement;
  const identifier = element.getAttribute("data-markdown-reference-identifier");
  if (identifier !== null) identifiers.add(identifier);
  for (const child of Array.from(element.childNodes)) {
    collectReferenceIdentifiersFromNode(child, identifiers);
  }
}

function domNodeToHast(
  node: Node,
  placeholders: AtomicPlaceholder[],
  placeholderBase: string
): HastNode {
  if (node.nodeType === Node.TEXT_NODE) return { type: "text", value: editableText(node.textContent) };
  if (node.nodeType !== Node.ELEMENT_NODE) return { type: "text", value: "" };
  const element = node as HTMLElement;
  if (element.hasAttribute("data-markdown-caret-guard")) {
    return { type: "text", value: editableText(element.textContent) };
  }
  // A file reference drawn as code was plain words in the source, and copies as them.
  if (element.tagName === "CODE" && element.classList.contains("cv-file-ref")) {
    return { type: "text", value: editableText(element.textContent) };
  }
  const sourceToken = element.getAttribute("data-markdown-source-token");
  const preservesSourceToken =
    element.getAttribute("data-markdown-atomic-slot") === "true" ||
    element.tagName === "A" ||
    element.tagName === "IMG";
  if (preservesSourceToken && sourceToken !== null) {
    const placeholder = `${placeholderBase}${placeholders.length}END`;
    placeholders.push({ placeholder, sourceToken });
    return { type: "text", value: placeholder };
  }
  const tagName = element.tagName.toLowerCase();
  return {
    type: "element",
    tagName,
    properties: propertiesFromElement(element),
    children: Array.from(element.childNodes).map((child) =>
      domNodeToHast(child, placeholders, placeholderBase)
    )
  };
}

function propertiesFromElement(element: HTMLElement): Record<string, unknown> {
  const properties: Record<string, unknown> = {};
  if (element.tagName === "CODE") {
    const language = [...element.classList].find(name => /^language-[\w-]+$/.test(name));
    if (language) properties.className = [language];
  }
  for (const name of ["href", "src", "alt", "title", "align", "type"]) {
    const value = element.getAttribute(name);
    if (value !== null) properties[name] = value;
  }
  for (const name of ["checked", "disabled"]) {
    if (element.hasAttribute(name)) properties[name] = true;
  }
  return properties;
}

function editableText(value: string | null): string {
  return (value || "").replace(/\u200b/g, "").replace(/\u00a0/g, " ");
}
