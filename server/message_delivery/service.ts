/** Resolve a Panels recipient through its canonical conversation send door. */
import type { Database } from '../core/database/contracts.ts';
import type { Clock } from '../core/clock.ts';
import type { Principal, RequestContext } from '../core/contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import type { ConversationMessageContent, ConversationSystem, NativeMessageOperation, PromptDeliveryMode } from '../conversation/contracts.ts';
import { PromptDeliveryQueued, PromptDeliveryStarted } from '../conversation/contracts.ts';
import { text_message_content } from '../conversation/message_content.ts';
import * as conversation_start from '../runtime/conversation_start.ts';
import { NO_CONVERSATION_START_OVERRIDES, overrides_for_existing_conversation } from '../runtime/logic/conversation_start_resolution.ts';
import type { ConversationStartOverrides } from '../runtime/logic/conversation_start_resolution.ts';
import { read_ticket } from '../tickets/data/index.ts';
import { read_manager, update_manager_launch_configuration } from '../managers/data.ts';
import { manager_lifecycle_lock } from '../managers/service.ts';
import { validate_launch_configuration } from '../managers/launch_settings.ts';
import { CHIEF_SETTINGS_KEY } from '../worker_settings/service.ts';
import { MessageRecordedToOwner } from './contracts.ts';
import type { MessageAdmission, MessageDeliveryResult } from './contracts.ts';
export function sender_label(ctx: RequestContext): string {
    if (ctx.principal.kind === 'owner')
        return 'owner';
    if (ctx.principal.kind === 'chief')
        return 'Chief';
    if (ctx.principal.kind === 'ticket')
        return `Ticket ${ctx.principal.id}`;
    return `Manager ${ctx.principal.id}`;
}
function sender_conversation_id(database: Database, sender: Principal): string | null {
    if (sender.kind === 'ticket')
        return database.read(sql => read_ticket(sql, sender.id).conversation_id);
    if (sender.kind === 'chief')
        return conversation_start.read_agent_conversation(database, CHIEF_SETTINGS_KEY);
    if (sender.kind === 'manager') {
        const item = database.read(sql => read_manager(sql, sender.id).item);
        return conversation_start.read_agent_conversation(database, item.manager_agent_key);
    }
    return null;
}
function sender_conversation_link_key(database: Database, sender: Principal): string {
    if (sender.kind === 'ticket')
        return `ticket:${sender.id}`;
    if (sender.kind === 'chief')
        return `agent:${CHIEF_SETTINGS_KEY}`;
    if (sender.kind === 'manager') {
        return `agent:${database.read(sql => read_manager(sql, sender.id).item.manager_agent_key)}`;
    }
    throw new Error('the owner has no sender conversation link');
}
export interface SendMessageOptions {
    conversation_id?: string | null;
    created_conversation_id?: string | null;
    runs_under?: ConversationStartOverrides;
    owner_read_through_sequence?: number | null;
    sender_message_id?: string | null;
    sent_at_unix_milliseconds?: number | null;
    retain_created_conversation?: boolean;
    system_message?: boolean;
    admission?: MessageAdmission | null;
    recording_receipt?: NativeMessageOperation | null;
}
export async function send_message(conversations: ConversationSystem, database: Database, clock: Clock, ctx: RequestContext, recipient: Principal, message: string | ConversationMessageContent, mode: PromptDeliveryMode = 'queue', options: SendMessageOptions = {}): Promise<MessageDeliveryResult> {
    const caller = ctx.principal;
    const kinds = new Set(['owner', 'chief', 'manager', 'ticket']);
    if (!kinds.has(caller.kind) || !kinds.has(recipient.kind)) {
        throw new PlannerError('validation', 'unsupported message principal kind');
    }
    const system_message = options.system_message ?? false;
    if (options.owner_read_through_sequence != null && (caller.kind !== 'owner' || system_message)) {
        throw new Error('only the owner can acknowledge messages with a reply');
    }
    if (system_message && recipient.kind === 'owner') {
        throw new PlannerError('validation', 'a system message cannot be addressed to the owner');
    }
    const label = system_message ? 'Panels' : sender_label(ctx);
    const content = typeof message === 'string' ? text_message_content(message) : message;
    const runs_under = options.runs_under ?? NO_CONVERSATION_START_OVERRIDES;
    const sender = system_message ? null : caller;
    const admission = options.admission ?? null;
    if (recipient.kind === 'owner') {
        if (caller.kind === 'owner') {
            throw new PlannerError('validation', 'the owner cannot send a message to the owner');
        }
        return conversation_start.conversation_link_lock(sender_conversation_link_key(database, caller)).run(async () => {
            const current = sender_conversation_id(database, caller);
            if (current === null) {
                throw new PlannerError('not_found', 'the sender has no current conversation', { kind: caller.kind, id: caller.id });
            }
            const resolved = typeof content === 'function' ? await content(current) : content;
            await conversations.record_message_to_owner(current, resolved, {
                sender_label: label,
                sender: caller,
                recipient,
                sender_message_id: options.sender_message_id ?? null,
                sent_at_unix_milliseconds: options.sent_at_unix_milliseconds ?? null,
                recording_receipt: options.recording_receipt ?? null,
            });
            return {
                recipient, conversation_id: current, fate: new MessageRecordedToOwner()
            };
        });
    }
    const metadata = {
        runs_under,
        sender_label: label,
        mode,
        owner_read_through_sequence: options.owner_read_through_sequence ?? null,
        sender_message_id: options.sender_message_id ?? null,
        sent_at_unix_milliseconds: options.sent_at_unix_milliseconds ?? null,
        sender,
        recipient,
        recording_receipt: options.recording_receipt ?? null,
    };
    let delivered: conversation_start.DeliveredMessage | MessageDeliveryResult;
    try {
        if (recipient.kind === 'ticket') {
            delivered = await conversation_start.conversation_link_lock(`ticket:${recipient.id}`).run(async () => {
                const ticket = database.read(sql => read_ticket(sql, recipient.id));
                if (admission !== null) {
                    const prior = await admission.preflight(database.read(sql => read_ticket(sql, recipient.id).conversation_id));
                    if (prior !== null)
                        return prior;
                }
                return conversation_start.send_to_ticket_conversation(conversations, database, recipient.id, content, {
                    ...metadata,
                    conversation_id: options.conversation_id != null && caller.kind === 'owner'
                        ? options.conversation_id : ticket.conversation_id,
                    created_conversation_id: options.created_conversation_id ?? conversation_start.new_conversation_id(),
                    now: clock.nowUnix(),
                });
            });
        }
        else if (recipient.kind === 'chief') {
            delivered = await conversation_start.conversation_link_lock(`agent:${CHIEF_SETTINGS_KEY}`).run(async () => {
                if (admission !== null) {
                    const prior = await admission.preflight(conversation_start.read_agent_conversation(database, CHIEF_SETTINGS_KEY));
                    if (prior !== null)
                        return prior;
                }
                const current = conversation_start.read_agent_conversation(database, CHIEF_SETTINGS_KEY);
                const target = options.conversation_id != null && caller.kind === 'owner'
                    ? options.conversation_id : current;
                const choice = target === null ? runs_under : overrides_for_existing_conversation(runs_under);
                const values = target !== null && choice.model == null
                    ? null : conversation_start.agent_resolve(database, choice);
                return conversation_start.send_to_agent_conversation(conversations, database, CHIEF_SETTINGS_KEY, content, values, {
                    ...metadata, conversation_id: target,
                    created_conversation_id: options.created_conversation_id ?? conversation_start.new_conversation_id()
                });
            });
        }
        else {
            delivered = await manager_lifecycle_lock(recipient.id).run(async () => {
                const item = database.read(sql => read_manager(sql, recipient.id).item);
                return conversation_start.conversation_link_lock(`agent:${item.manager_agent_key}`).run(async () => {
                    const current = conversation_start.read_agent_conversation(database, item.manager_agent_key);
                    if (admission !== null) {
                        const prior = await admission.preflight(current);
                        if (prior !== null)
                            return prior;
                    }
                    const target = options.conversation_id != null && caller.kind === 'owner'
                        ? options.conversation_id : current;
                    const choice = target === null ? runs_under : overrides_for_existing_conversation(runs_under);
                    const resolved = conversation_start.manager_resolve(database, item, choice);
                    const explicit_choice = choice.backend_key != null || choice.model != null
                        || choice.reasoning_effort != null || Boolean(choice.reasoning_effort_is_set);
                    const configuration = {
                        employee_backend: resolved.backend_key,
                        employee_launch_model: resolved.model,
                        employee_launch_reasoning_effort: resolved.reasoning_effort,
                    };
                    if (explicit_choice)
                        database.read(sql => validate_launch_configuration(sql, configuration));
                    const result = await conversation_start.send_to_agent_conversation(conversations, database, item.manager_agent_key, content, resolved, {
                        ...metadata, conversation_id: target,
                        created_conversation_id: options.created_conversation_id ?? conversation_start.new_conversation_id(),
                        required_manager_id: item.id,
                        retain_created_conversation: options.retain_created_conversation ?? false
                    });
                    if (caller.kind === 'owner' && ((current === null && result.conversation_id !== null)
                        || (explicit_choice && (result.fate instanceof PromptDeliveryStarted || result.fate instanceof PromptDeliveryQueued)))) {
                        database.write(tx => update_manager_launch_configuration(tx, item.id, configuration, {
                            principal: caller,
                            clock,
                            expected_configuration: item.manager_launch_configuration,
                            explicit_choice,
                        }));
                    }
                    return result;
                });
            });
        }
    }
    catch (error) {
        admission?.abort_before_dispatch();
        throw error;
    }
    if ('recipient' in delivered)
        return delivered;
    admission?.finish(delivered.fate);
    return {
        recipient, conversation_id: delivered.conversation_id, fate: delivered.fate
    };
}
