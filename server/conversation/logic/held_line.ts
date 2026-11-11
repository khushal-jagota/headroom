/** Pure rules for the next prompt from the held line. */
import type { MessageContent } from '../message_content.ts';
import { sender_labeled_message_content } from '../message_content.ts';

export interface WaitingMessage {
    readonly content: MessageContent;
    readonly sender_label: string;
    readonly model_change: string | null;
    readonly reasoning_effort_change: string | null;
    readonly reasoning_effort_is_set?: boolean;
}
export function leading_run_that_can_share_a_turn<T extends WaitingMessage>(waiting: Iterable<T>): readonly T[] {
    const run: T[] = [];
    for (const message of waiting) {
        const carries_a_change = message.model_change !== null || message.reasoning_effort_change !== null || Boolean(message.reasoning_effort_is_set);
        if (run.length && carries_a_change) break;
        run.push(message);
    }
    return run;
}
export function one_prompt_from(run: readonly WaitingMessage[]): MessageContent {
    if (run.length === 1) return run[0].content;
    return [...run[0].content, ...run.slice(1).flatMap(message => sender_labeled_message_content(message.content, message.sender_label))];
}
