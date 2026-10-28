import { unified } from 'unified';
import remarkParse from 'remark-parse';
import remarkGfm from 'remark-gfm';
import type { TranscriptRow } from './transcript';
import { messageContentText } from './wire';

/** Only the newest addressed reply supplies the preview, including attachment-only replies. */
export function latestUnreadReply(rows: readonly TranscriptRow[], readThrough: number): string | null {
  for (let index = rows.length - 1; index >= 0; index--) {
    const row = rows[index];
    if (row.kind === 'agent_message' && row.toOwner) {
      return row.sequence > readThrough ? messageContentText(row.content) : null;
    }
  }
  return null;
}

const parser = unified().use(remarkParse).use(remarkGfm);
/** Plain text keeps block boundaries without turning a closed dock into another renderer. */
export function dockPreviewText(source: string | null | undefined): string | null {
  if (!source?.trim()) return null;
  const tree = parser.parse(source);
  function text(node: { type: string; value?: string; alt?: string | null; children?: unknown[] }): string {
    if (node.type === 'html' || node.type === 'definition' || node.type === 'thematicBreak') return '';
    if (node.type === 'image') return node.alt ?? '';
    if (node.type === 'break') return '\n';
    if (node.value !== undefined) return node.value;
    const separator = ['root', 'blockquote', 'list', 'listItem', 'table', 'tableRow'].includes(node.type) ? '\n' : '';
    return (node.children ?? []).map(child => text(child as Parameters<typeof text>[0])).join(separator);
  }
  return text(tree).replace(/\n[ \t]*\n+/g, '\n').trim() || null;
}
