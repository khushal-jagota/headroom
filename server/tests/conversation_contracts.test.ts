import { describe, expect, it } from 'vitest';
import {
    MessageText, MessageImage, MessageContentEmpty, MessageContentNotPieces,
    ComposedMessageDoesNotContainSenderContent, require_message_content,
    message_content_from_stored, message_content_json_entries,
    sender_labeled_composed_message_content,
} from '../conversation/message_content.ts';
import { idle_publication } from '../conversation/publication/idle.ts';
import type { TransactionSql } from '../core/database/contracts.ts';

describe('conversation contract boundaries outside the comparison host', () => {
    it('refuses runtime values that are not message pieces', () => {
        // HTTP JSON decoding creates pieces first, so it cannot exercise this in-process door.
        expect(() => require_message_content('words')).toThrow(MessageContentNotPieces);
        expect(() => require_message_content([{text: 'untyped'}])).toThrow(MessageContentNotPieces);
        expect(() => require_message_content([])).toThrow(MessageContentEmpty);
    });
    it('preserves sender content through composition and stored message conversion', () => {
        // The fake composes its own correct role prefix. It cannot produce a broken suffix.
        const image = new MessageImage({stored_file_id: 'picture', media_type: 'image/png'});
        const sender = [new MessageText({text: 'exact words'}), image];
        const composed = [new MessageText({text: 'role\n\nexact words'}), image];
        const labeled = sender_labeled_composed_message_content(composed, sender, 'Owner');
        expect(labeled).toEqual([new MessageText({text: 'role\n\nOwner:\nexact words'}), image]);
        expect(message_content_from_stored(message_content_json_entries(labeled))).toEqual(labeled);
        expect(() => sender_labeled_composed_message_content([image], sender, 'Owner')).toThrow(ComposedMessageDoesNotContainSenderContent);
    });
    it('publishes independent idle values on the source transaction', () => {
        // The comparison fake has no rows. This proves the commit-step consumer boundary.
        const tx: TransactionSql = {
            all: <Row,>() => [{conversation_id: 'first'}, {conversation_id: 'second'}] as Row[],
            get: () => undefined,
            run: () => { throw new Error('the source must not write or commit'); },
        };
        const ids: string[] = [];
        idle_publication((actual, id, attention) => {
            expect(actual).toBe(tx);
            expect(attention).toEqual({signal: {working: false, message_for_owner: false}, awaiting_answer: false, last_turn_failed: false});
            ids.push(id);
            attention.signal.working = true;
        })(tx);
        expect(ids).toEqual(['first', 'second']);
        // A runtime caller can bypass the TS return type. No promise can escape the commit step.
        const asyncPublish = (async () => { throw new Error('late rejection'); }) as unknown as Parameters<typeof idle_publication>[0];
        expect(() => idle_publication(asyncPublish)(tx)).toThrow('idle publication callbacks must be synchronous');
    });
});
