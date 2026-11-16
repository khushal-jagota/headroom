/** Readable notification words bounded by code points and complete grapheme clusters. */
import MarkdownIt from 'markdown-it';
import { NOTIFICATION_LABEL_MAX_CHARS, NOTIFICATION_PREVIEW_MAX_CHARS } from '../config.ts';

const markdown = new MarkdownIt('commonmark');
const graphemes = new Intl.Segmenter('und', { granularity: 'grapheme' });
const whitespace = /[\p{White_Space}\u001c-\u001f]+/gu;
export function bounded_text(text: string, limit: number): string {
  const compact = text.replace(whitespace, ' ').replace(/^ +| +$/g, '');
  if (Array.from(compact).length <= limit) return compact;
  let prefix = '';
  let length = 0;
  for (const { segment } of graphemes.segment(compact)) {
    const size = Array.from(segment).length;
    if (length + size > limit - 1) break;
    prefix += segment;
    length += size;
  }
  return prefix.replace(/ +$/, '') + '…';
}
export const notification_label = (text: string): string =>
  bounded_text(text, NOTIFICATION_LABEL_MAX_CHARS);
export function message_preview(source: string): string {
  const pieces: string[] = [];
  for (const token of markdown.parse(source, {})) {
    if (token.type === 'inline')
      pieces.push(
        (token.children ?? [])
          .map((child) =>
            ['text', 'code_inline'].includes(child.type)
              ? child.content
              : ['softbreak', 'hardbreak'].includes(child.type)
                ? ' '
                : '',
          )
          .join(''),
      );
    else if (['fence', 'code_block'].includes(token.type)) pieces.push(token.content);
  }
  return bounded_text(pieces.join(' '), NOTIFICATION_PREVIEW_MAX_CHARS) || 'Sent an attachment.';
}
