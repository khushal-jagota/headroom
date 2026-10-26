/** Sanitized logical document exchanged with the Markdown parser worker. */
export type MarkdownTreeNode = {
  type: string;
  tagName?: string;
  value?: string;
  properties?: Record<string, unknown>;
  children?: MarkdownTreeNode[];
};
export type MarkdownReferenceDefinition = {
  type: "definition";
  identifier: string;
  label?: string;
  url: string;
  title?: string | null;
};
export type ParsedMarkdown = {
  children: MarkdownTreeNode[];
  definitions: MarkdownReferenceDefinition[];
};
export type MarkdownParseRequest = { id: number; source: string; kind: "markdown" | "view-body" };
export type MarkdownParseReply =
  | { id: number; document: ParsedMarkdown }
  | { id: number; body: import("../conversation/view/contracts").ViewRowBody }
  | { id: number; error: string };
