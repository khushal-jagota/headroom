import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkGfm from "remark-gfm";
import remarkRehype from "remark-rehype";
import rehypeSanitize, { type Options as SanitizeSchema } from "rehype-sanitize";
import rehypeToRemark from "rehype-remark";
import remarkStringify from "remark-stringify";
import { visit } from "unist-util-visit";

type Position = {
  start?: { offset?: number };
  end?: { offset?: number };
};

type MarkdownNode = {
  type: string;
  value?: string;
  identifier?: string;
  label?: string;
  url?: string;
  title?: string | null;
  children?: MarkdownNode[];
  position?: Position;
  data?: { hProperties?: Record<string, unknown> };
};

type HastNode = {
  type: string;
  tagName?: string;
  value?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
};

type AtomicPlaceholder = {
  placeholder: string;
  sourceToken: string;
};

type ReferenceDefinition = {
  type: "definition";
  identifier: string;
  label?: string;
  url: string;
  title?: string | null;
};

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

export function renderMarkdownToElement(source: string): HTMLElement {
  const processor = forwardProcessor(source);
  const parsed = processor.parse(source);
  const referenceDefinitions = collectReferenceDefinitions(parsed as unknown as MarkdownNode);
  const tree = processor.runSync(parsed) as HastNode;
  const root = document.createElement("div");
  root.className = "markdown";
  for (const child of tree.children || []) {
    appendHastNode(root, child);
  }
  referenceDefinitionsByRoot.set(root, referenceDefinitions);
  return root;
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

function appendHastNode(parent: HTMLElement, node: HastNode): void {
  if (node.type === "text") {
    parent.appendChild(document.createTextNode(node.value || ""));
    return;
  }
  if (node.type !== "element" || !node.tagName) return;
  const element = document.createElement(node.tagName);
  applySanitizedProperties(element, node.properties || {});
  for (const child of node.children || []) appendHastNode(element, child);
  parent.appendChild(element);
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
