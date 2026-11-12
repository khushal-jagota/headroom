/** Shared owner-send intake and delivery envelopes from conversation/api.py. */
import type { FastifyReply } from 'fastify';
import type { Principal } from '../core/contracts.ts';
import { HttpError } from '../core/http/errors.ts';
import type { ConversationMessageFiles } from './message_files.ts';
import { MessageText, MessageImage, MessageFile, type MessageContent } from './message_content.ts';
import { MAX_CONVERSATION_IMAGE_BYTES, MAX_CONVERSATION_MESSAGE_IMAGE_BYTES, MAX_CONVERSATION_MESSAGE_FILE_BYTES, validated_image_media_type, validated_file_media_type } from './file_validation.ts';
import { PromptDeliveryStarted, PromptDeliveryQueued, PromptDeliveryInjected, PromptDeliveryRefused, PromptDeliverySuppressed, PromptDeliveryUncertain } from './contracts.ts';
export type SentPiece = {
    piece: 'text';
    text: string;
} | {
    piece: 'image';
    data: string;
    media_type: string;
    file_name: string | null;
} | {
    piece: 'file';
    data: string;
    media_type: string;
    file_name: string;
} | {
    piece: 'video_upload';
    upload_id: string;
};
export interface OwnerSendBody {
    conversation_id: string | null;
    owner_read_through_sequence: number | null;
    backend_key: string | null;
    model: string | null;
    reasoning_effort: string | null;
    content: SentPiece[];
    sender_label: string;
    mode: 'queue' | 'send_now' | 'steer';
    sender_message_id: string | null;
    sent_at_unix_milliseconds: number | null;
    provided_fields: Set<string>;
}
interface ValidationIssue {
    type: string;
    loc: (string | number)[];
    msg: string;
    input: unknown;
    ctx?: unknown;
}
const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);
export function parse_owner_send_body(raw: unknown, reply: FastifyReply): OwnerSendBody | null {
    const errors: ValidationIssue[] = [];
    const add = (type: string, path: (string | number)[], msg: string, input: unknown, ctx?: unknown) => {
        errors.push({
            type, loc: ['body', ...path], msg, input, ...(ctx === undefined ? {} : { ctx })
        });
    };
    if (!object(raw)) {
        add('model_attributes_type', [], 'Input should be a valid dictionary or object to extract fields from', raw);
        reply.code(422).send({ detail: errors });
        return null;
    }
    const text = (fields: Record<string, unknown>, key: string, path: (string | number)[] = [], nullable = false, required = false): string | null => {
        if (!(key in fields)) {
            if (required)
                add('missing', [...path, key], 'Field required', fields);
            return null;
        }
        const value = fields[key];
        if (nullable && value === null)
            return null;
        if (typeof value !== 'string') {
            add('string_type', [...path, key], 'Input should be a valid string', value);
            return null;
        }
        return value;
    };
    const integer = (key: string, minimum?: number): number | null => {
        if (raw[key] == null)
            return null;
        const value = raw[key];
        const number = typeof value === 'boolean' ? Number(value) : typeof value === 'string' && /^[+-]?[0-9]+(?:\.0+)?$/.test(value.trim()) ? Number(value) : value;
        if (typeof number !== 'number' || !Number.isInteger(number)) {
            const type = typeof value === 'string' ? 'int_parsing' : typeof value === 'number' ? 'int_from_float' : 'int_type';
            add(type, [key], type === 'int_parsing' ? 'Input should be a valid integer, unable to parse string as an integer' : type === 'int_from_float' ? 'Input should be a valid integer, got a number with a fractional part' : 'Input should be a valid integer', value);
            return null;
        }
        if (minimum !== undefined && number < minimum)
            add('greater_than_equal', [key], `Input should be greater than or equal to ${minimum}`, value, { ge: minimum });
        return number;
    };
    const conversation_id = text(raw, 'conversation_id', [], true);
    const owner_read_through_sequence = integer('owner_read_through_sequence', 0);
    const backend_key = text(raw, 'backend_key', [], true);
    const model = text(raw, 'model', [], true);
    const reasoning_effort = text(raw, 'reasoning_effort', [], true);
    const content: SentPiece[] = [];
    if (!('content' in raw))
        add('missing', ['content'], 'Field required', raw);
    else if (!Array.isArray(raw.content))
        add('list_type', ['content'], 'Input should be a valid list', raw.content);
    else
        for (const [index, value] of raw.content.entries()) {
            // Untagged Pydantic unions report each rejected model, in declaration order.
            const variants = [['text', 'SentTextPiece'], ['image', 'SentImagePiece'], ['file', 'SentFilePiece'], ['video_upload', 'SentVideoUploadPiece']] as const;
            const successful = object(value) && variants.some(([tag]) => value.piece === tag
                && (tag === 'text' ? typeof value.text === 'string' : tag === 'video_upload' ? typeof value.upload_id === 'string' && Object.keys(value).every(k => ['piece', 'upload_id'].includes(k))
                    : typeof value.data === 'string' && typeof value.media_type === 'string' && (tag === 'file' ? typeof value.file_name === 'string' : value.file_name == null || typeof value.file_name === 'string')));
            if (successful) {
                if (value.piece === 'text')
                    content.push({ piece: 'text', text: value.text as string });
                else if (value.piece === 'video_upload')
                    content.push({ piece: 'video_upload', upload_id: value.upload_id as string });
                else if (value.piece === 'file')
                    content.push({
                        piece: 'file', data: value.data as string, media_type: value.media_type as string, file_name: value.file_name as string
                    });
                else
                    content.push({
                        piece: 'image', data: value.data as string, media_type: value.media_type as string, file_name: value.file_name as string | null ?? null
                    });
                continue;
            }
            for (const [tag, label] of variants) {
                const path = ['content', index, label];
                if (!object(value)) {
                    add('model_attributes_type', path, 'Input should be a valid dictionary or object to extract fields from', value);
                    continue;
                }
                if (!('piece' in value))
                    add('missing', [...path, 'piece'], 'Field required', value);
                else if (value.piece !== tag)
                    add('literal_error', [...path, 'piece'], `Input should be '${tag}'`, value.piece, { expected: `'${tag}'` });
                if (tag === 'text')
                    text(value, 'text', path, false, true);
                else if (tag === 'video_upload') {
                    text(value, 'upload_id', path, false, true);
                    for (const key of Object.keys(value))
                        if (!['piece', 'upload_id'].includes(key))
                            add('extra_forbidden', [...path, key], 'Extra inputs are not permitted', value[key]);
                }
                else {
                    text(value, 'data', path, false, true);
                    text(value, 'media_type', path, false, true);
                    text(value, 'file_name', path, tag === 'image', tag === 'file');
                }
            }
        }
    const sender_label = text(raw, 'sender_label', [], false, true);
    const mode = 'mode' in raw ? raw.mode : 'queue';
    if (!['queue', 'send_now', 'steer'].includes(String(mode)))
        add('enum', ['mode'], "Input should be 'queue', 'send_now' or 'steer'", mode, { expected: "'queue', 'send_now' or 'steer'" });
    const sender_message_id = text(raw, 'sender_message_id', [], true);
    const sent_at_unix_milliseconds = integer('sent_at_unix_milliseconds');
    if (errors.length) {
        reply.code(422).send({ detail: errors });
        return null;
    }
    return {
        conversation_id, owner_read_through_sequence, backend_key, model, reasoning_effort, content,
        sender_label: sender_label!, mode: mode as OwnerSendBody['mode'], sender_message_id, sent_at_unix_milliseconds, provided_fields: new Set(Object.keys(raw))
    };
}
function decoded(data: string, maximum: number, too_large: string): Buffer {
    if (data.length > 4 * Math.ceil(maximum / 3))
        throw new HttpError(422, too_large);
    if (!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(data))
        throw new HttpError(422, "a piece's data is not base64");
    return Buffer.from(data, 'base64');
}
export function video_send_admission(sent: SentPiece[], sender_message_id: string | null, principal: Principal): void {
    const count = sent.filter(p => p.piece === 'video_upload').length;
    if (!count)
        return;
    if (principal.kind !== 'owner')
        throw new HttpError(403, 'only the owner can attach video');
    if (count > 1)
        throw new HttpError(422, 'a message permits only one video');
    if (!sender_message_id)
        throw new HttpError(422, 'video requires sender_message_id');
    throw new HttpError(503, 'video uploads are unavailable');
}
export async function conversation_message_content(files: ConversationMessageFiles, conversation_id: string, sent: SentPiece[], options: {
    sender_message_id?: string | null;
} = {}): Promise<MessageContent> {
    const validated = new Map<number, {
        bytes: Buffer;
        media_type: string;
    }>();
    let image_bytes = 0, file_bytes = 0;
    const video_count = sent.filter(p => p.piece === 'video_upload').length;
    if (video_count > 1)
        throw new HttpError(422, 'a message permits only one video');
    if (video_count)
        throw new HttpError(422, 'video upload requires send admission');
    for (const [index, piece] of sent.entries()) {
        if (piece.piece !== 'image' && piece.piece !== 'file')
            continue;
        const bytes = decoded(piece.data, piece.piece === 'image' ? MAX_CONVERSATION_IMAGE_BYTES : MAX_CONVERSATION_MESSAGE_FILE_BYTES, piece.piece === 'image' ? 'a conversation image is too large' : "a conversation message's files are too large");
        if (piece.piece === 'image')
            image_bytes += bytes.length;
        else
            file_bytes += bytes.length;
        if (image_bytes > MAX_CONVERSATION_MESSAGE_IMAGE_BYTES)
            throw new HttpError(422, "a conversation message's images are too large");
        if (file_bytes > MAX_CONVERSATION_MESSAGE_FILE_BYTES)
            throw new HttpError(422, "a conversation message's files are too large");
        try {
            validated.set(index, { bytes, media_type: piece.piece === 'image' ? validated_image_media_type(bytes) : validated_file_media_type(bytes, piece.file_name) });
        }
        catch (error) {
            throw new HttpError(422, (error as Error).message);
        }
    }
    const pieces = [];
    for (const [index, piece] of sent.entries()) {
        if (piece.piece === 'text') {
            pieces.push(new MessageText({ text: piece.text }));
            continue;
        }
        if (piece.piece === 'video_upload')
            throw new HttpError(422, 'video upload requires send admission');
        const value = validated.get(index)!;
        const kept = await files.keep(conversation_id, value.bytes, {
            media_type: value.media_type, sender_message_id: options.sender_message_id, piece_index: index
        });
        pieces.push(piece.piece === 'image' ? new MessageImage({
            stored_file_id: kept.stored_file_id, media_type: value.media_type, file_name: piece.file_name
        })
            : new MessageFile({
                stored_file_id: kept.stored_file_id, media_type: value.media_type, file_name: piece.file_name, byte_count: kept.byte_count
            }));
    }
    return pieces;
}
export function delivery_fate_json(fate: unknown): Record<string, unknown> {
    if (fate instanceof PromptDeliveryStarted)
        return { fate: 'started' };
    if (fate instanceof PromptDeliveryQueued)
        return { fate: 'queued', queue_position: fate.queue_position };
    if (fate instanceof PromptDeliveryInjected)
        return { fate: 'injected' };
    if (fate instanceof PromptDeliveryRefused)
        return { fate: 'refused', refusal_reason: fate.refusal_reason };
    if (fate instanceof PromptDeliverySuppressed)
        return { fate: 'suppressed', reason: fate.reason };
    if (fate instanceof PromptDeliveryUncertain)
        return { fate: 'uncertain' };
    throw new Error('unknown delivery fate');
}
