/** Message pieces retain the exact content and order supplied by their author. */
import { isDeepStrictEqual } from 'node:util';

export class MessageText {
  readonly text: string;
  constructor(values: { text: string }) {
    this.text = values.text;
  }
}
export class MessageImage {
  readonly stored_file_id: string;
  readonly media_type: string;
  readonly file_name: string | null;
  constructor(values: { stored_file_id: string; media_type: string; file_name?: string | null }) {
    this.stored_file_id = values.stored_file_id;
    this.media_type = values.media_type;
    this.file_name = values.file_name ?? null;
  }
}
export class MessageFile {
  readonly stored_file_id: string;
  readonly media_type: string;
  readonly file_name: string;
  readonly byte_count: number;
  constructor(values: {
    stored_file_id: string;
    media_type: string;
    file_name: string;
    byte_count: number;
  }) {
    this.stored_file_id = values.stored_file_id;
    this.media_type = values.media_type;
    this.file_name = values.file_name;
    this.byte_count = values.byte_count;
  }
}
export type MessagePiece = MessageText | MessageImage | MessageFile;
export type MessageContent = readonly MessagePiece[];
export class MessageContentEmpty extends Error {}
export class MessageContentNotPieces extends TypeError {}
export class ComposedMessageDoesNotContainSenderContent extends Error {}
export function text_message_content(text: string): MessageContent {
  return [new MessageText({ text })];
}
export function require_message_content(content: unknown): MessageContent {
  if (!Array.isArray(content))
    throw new MessageContentNotPieces(
      'a message is a run of pieces; use text_message_content(...) to send words',
    );
  if (
    !content.every(
      (piece) =>
        piece instanceof MessageText ||
        piece instanceof MessageImage ||
        piece instanceof MessageFile,
    )
  )
    throw new MessageContentNotPieces('a message may only hold message pieces');
  if (!content.length) throw new MessageContentEmpty('a message must have something in it');
  return [...content];
}
export function message_content_text(content: MessageContent): string {
  return content
    .filter((piece) => piece instanceof MessageText)
    .map((piece) => piece.text)
    .join('\n\n');
}
export function joined_runs_of_text(content: MessageContent): MessageContent {
  const joined: MessagePiece[] = [];
  for (const piece of content) {
    const last = joined.at(-1);
    if (piece instanceof MessageText && last instanceof MessageText)
      joined[joined.length - 1] = new MessageText({ text: last.text + piece.text });
    else joined.push(piece);
  }
  return joined;
}
export function prefix_message_content_text(
  content: MessageContent,
  prefix: string,
  separator: string,
): MessageContent {
  const first = content[0];
  return first instanceof MessageText
    ? [new MessageText({ text: prefix + separator + first.text }), ...content.slice(1)]
    : [new MessageText({ text: prefix }), ...content];
}
export function sender_labeled_message_content(
  content: MessageContent,
  sender_label: string,
): MessageContent {
  return prefix_message_content_text(content, sender_label + ':', '\n');
}
export function sender_labeled_composed_message_content(
  content: MessageContent,
  sender_content: MessageContent,
  sender_label: string,
): MessageContent {
  const count = sender_content.length;
  if (count && isDeepStrictEqual(content.slice(-count), sender_content))
    return [
      ...content.slice(0, -count),
      ...sender_labeled_message_content(sender_content, sender_label),
    ];
  const firstSender = sender_content[0];
  const index = content.length - count;
  const firstComposed = count && index >= 0 ? content[index] : undefined;
  if (
    firstSender instanceof MessageText &&
    firstComposed instanceof MessageText &&
    firstComposed.text.endsWith(firstSender.text) &&
    isDeepStrictEqual(content.slice(index + 1), sender_content.slice(1))
  ) {
    const prefix = firstSender.text
      ? firstComposed.text.slice(0, -firstSender.text.length)
      : firstComposed.text;
    const labeled = sender_labeled_message_content(sender_content, sender_label);
    return [
      ...content.slice(0, index),
      new MessageText({ text: prefix + (labeled[0] as MessageText).text }),
      ...labeled.slice(1),
    ];
  }
  throw new ComposedMessageDoesNotContainSenderContent(
    'composed message does not end with its sender content',
  );
}
export function message_content_starts_with_slash_token(content: MessageContent): boolean {
  const first = content[0];
  return (
    first instanceof MessageText &&
    first.text.startsWith('/') &&
    first.text.split(/\s+/u)[0].length > 1
  );
}
export function message_content_starts_with_command(
  content: MessageContent,
  command_names: ReadonlySet<string>,
): boolean {
  return (
    message_content_starts_with_slash_token(content) &&
    command_names.has((content[0] as MessageText).text.split(/\s+/u)[0].slice(1))
  );
}
export const STORED_TEXT_ONLY_FIELD = 'text';
export const STORED_CONTENT_FIELD = 'content';
export const PIECE_FIELD = 'piece';
export const PIECE_NAME_TEXT = 'text';
export const PIECE_NAME_IMAGE = 'image';
export const PIECE_NAME_FILE = 'file';
export function message_content_json_entries(content: MessageContent): Record<string, unknown> {
  if (content.length === 1 && content[0] instanceof MessageText) return { text: content[0].text };
  return {
    content: content.map((piece) => {
      if (piece instanceof MessageText) return { piece: 'text', text: piece.text };
      if (piece instanceof MessageImage)
        return {
          piece: 'image',
          stored_file_id: piece.stored_file_id,
          media_type: piece.media_type,
          ...(piece.file_name === null ? {} : { file_name: piece.file_name }),
        };
      return {
        piece: 'file',
        stored_file_id: piece.stored_file_id,
        media_type: piece.media_type,
        file_name: piece.file_name,
        byte_count: piece.byte_count,
      };
    }),
  };
}
function text(stored: Record<string, unknown>, field: string): string {
  if (!(field in stored)) throw new Error(`'${field}'`);
  const value = stored[field];
  if (typeof value !== 'string') throw new Error(`${field} must be text`);
  return value;
}
export function message_content_from_stored(stored: Record<string, unknown>): MessageContent {
  const pieces = stored.content;
  if (pieces === null || pieces === undefined)
    return [new MessageText({ text: text(stored, 'text') })];
  if (!Array.isArray(pieces) || !pieces.length)
    throw new Error('content must be a non-empty list of pieces');
  return pieces.map((piece: unknown) => {
    if (piece === null || typeof piece !== 'object' || Array.isArray(piece))
      throw new Error('a message piece is not a JSON object');
    const fields = piece as Record<string, unknown>;
    switch (text(fields, 'piece')) {
      case 'text':
        return new MessageText({ text: text(fields, 'text') });
      case 'image': {
        const file_name = fields.file_name ?? null;
        if (file_name !== null && typeof file_name !== 'string')
          throw new Error('file_name must be text or absent');
        return new MessageImage({
          stored_file_id: text(fields, 'stored_file_id'),
          media_type: text(fields, 'media_type'),
          file_name,
        });
      }
      case 'file': {
        const byte_count = fields.byte_count;
        if (typeof byte_count !== 'number' || !Number.isInteger(byte_count) || byte_count < 0)
          throw new Error('byte_count must be a non-negative integer');
        return new MessageFile({
          stored_file_id: text(fields, 'stored_file_id'),
          media_type: text(fields, 'media_type'),
          file_name: text(fields, 'file_name'),
          byte_count,
        });
      }
      default:
        throw new Error(`unknown message piece ${fields.piece}`);
    }
  });
}
