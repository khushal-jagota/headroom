function stringValue(value: unknown): string {
  return value === null || value === undefined ? "" : String(value);
}

function setEmptyState(el: HTMLElement, raw: string): void {
  if (raw.trim()) {
    el.removeAttribute("data-empty");
  } else {
    el.setAttribute("data-empty", "true");
  }
}

export function paintMarkdownEditable(el: HTMLElement, value: unknown): void {
  const raw = stringValue(value);
  el.replaceChildren();
  if (raw.trim()) {
    const rendered = window.Planner?.markdown?.render(raw);
    if (rendered) {
      rendered.classList.add("markdown-block");
      el.appendChild(rendered);
    } else {
      el.textContent = raw;
    }
  }
  setEmptyState(el, raw);
}

export function paintPlainEditable(el: HTMLElement, value: unknown): void {
  const raw = stringValue(value);
  el.textContent = raw;
  setEmptyState(el, raw);
}

export function readPlainEditable(el: HTMLElement): string {
  return el.textContent || "";
}

export function readMarkdownEditable(el: HTMLElement): string {
  const markdownBlock = directMarkdownBlock(el);
  const raw = serializeBlockContainer(markdownBlock || el);
  setEmptyState(el, raw);
  return raw;
}

export function refreshEditableEmptyState(el: HTMLElement, markdown: boolean): void {
  setEmptyState(el, markdown ? readMarkdownEditable(el) : readPlainEditable(el));
}

export function handlePlainTextPaste(event: ClipboardEvent): void {
  const text = event.clipboardData?.getData("text/plain") || "";
  event.preventDefault();
  if (document.queryCommandSupported?.("insertText")) {
    document.execCommand("insertText", false, text);
    return;
  }
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0) return;
  const range = selection.getRangeAt(0);
  range.deleteContents();
  const node = document.createTextNode(text);
  range.insertNode(node);
  range.setStartAfter(node);
  range.collapse(true);
  selection.removeAllRanges();
  selection.addRange(range);
  event.currentTarget?.dispatchEvent(new InputEvent("input", { bubbles: true }));
}

function directMarkdownBlock(el: HTMLElement): HTMLElement | null {
  const meaningful = Array.from(el.childNodes).filter((node) => {
    if (node.nodeType === Node.TEXT_NODE) return Boolean(node.textContent);
    return node.nodeType === Node.ELEMENT_NODE;
  });
  if (meaningful.length !== 1) return null;
  const only = meaningful[0];
  if (!(only instanceof HTMLElement)) return null;
  return only.classList.contains("markdown-block") ? only : null;
}

function serializeBlockContainer(parent: Node): string {
  const blocks: string[] = [];
  let text = "";
  for (const child of Array.from(parent.childNodes)) {
    if (child.nodeType === Node.TEXT_NODE) {
      text += child.textContent || "";
      continue;
    }
    if (!(child instanceof HTMLElement)) continue;
    if (child.tagName === "BR") {
      text += "\n";
      continue;
    }
    const block = serializeBlockElement(child);
    if (block === null) {
      text += serializeInlineNode(child);
      continue;
    }
    if (text !== "") {
      blocks.push(text);
      text = "";
    }
    if (block !== "") blocks.push(block);
  }
  if (text !== "") blocks.push(text);
  return blocks.join("\n\n");
}

function serializeBlockElement(el: HTMLElement): string | null {
  const tag = el.tagName;
  if (tag === "H1" || tag === "H2" || tag === "H3") {
    return `${"#".repeat(Number(tag.slice(1)))} ${serializeInlineChildren(el)}`;
  }
  if (tag === "P") return serializeInlineChildren(el);
  if (tag === "UL") return serializeList(el, false);
  if (tag === "OL") return serializeList(el, true);
  if (tag === "PRE") return serializePre(el);
  if (tag === "DIV") {
    const nested = serializeBlockContainer(el);
    return nested || serializeInlineChildren(el);
  }
  return null;
}

function serializeList(el: HTMLElement, ordered: boolean): string {
  const items = Array.from(el.children).filter((child): child is HTMLElement => child instanceof HTMLElement && child.tagName === "LI");
  return items
    .map((item, index) => {
      const marker = ordered ? `${index + 1}. ` : "- ";
      return `${marker}${serializeInlineChildren(item).replace(/\n+/g, " ")}`;
    })
    .join("\n");
}

function serializePre(el: HTMLElement): string {
  const code = el.querySelector("code");
  const text = code ? code.textContent || "" : el.textContent || "";
  return `\`\`\`\n${text}\n\`\`\``;
}

function serializeInlineChildren(parent: Node): string {
  return Array.from(parent.childNodes).map(serializeInlineNode).join("");
}

function serializeInlineNode(node: Node): string {
  if (node.nodeType === Node.TEXT_NODE) return node.textContent || "";
  if (!(node instanceof HTMLElement)) return "";
  const tag = node.tagName;
  if (tag === "BR") return "\n";
  if (tag === "STRONG" || tag === "B") return `**${serializeInlineChildren(node)}**`;
  if (tag === "EM" || tag === "I") return `*${serializeInlineChildren(node)}*`;
  if (tag === "CODE") {
    const text = node.textContent || "";
    return text.includes("`") ? text : `\`${text}\``;
  }
  if (tag === "A") {
    const text = serializeInlineChildren(node);
    const href = node.getAttribute("href");
    return href ? `[${text}](${href})` : text;
  }
  return serializeInlineChildren(node);
}
