/** Best-effort courtesy messages after a committed review transfer. */
import type { Database } from '../core/database/contracts.ts';
import { OWNER } from '../core/contracts.ts';
import type { Principal } from '../core/contracts.ts';
import type { ConversationSystem } from '../conversation/contracts.ts';
import {
  PromptDeliveryRefused,
  PromptDeliverySuppressed,
  PromptDeliveryUncertain,
} from '../conversation/contracts.ts';
import { text_message_content } from '../conversation/message_content.ts';
import type { MessageContent } from '../conversation/message_content.ts';
import * as conversation_start from '../runtime/conversation_start.ts';
import { manager_lifecycle_lock } from '../managers/service.ts';
import { read_manager } from '../managers/data.ts';
import { CHIEF_SETTINGS_KEY } from '../worker_settings/service.ts';
import { read_ticket } from './data/index.ts';
import { notify_new_review_holder } from './logic/review_transfer.ts';
import type { ReviewTransferNotice } from './contracts.ts';
export async function notify_review_transfers(
  system: ConversationSystem,
  database: Database,
  notices: readonly ReviewTransferNotice[],
  options: {
    now: number;
  },
): Promise<void> {
  for (const notice of notices) {
    try {
      await notify_review_transfer(system, database, notice, options.now);
    } catch (error) {
      console.error(`review transfer notice failed (ticket=${notice.ticket_id})`, error);
    }
  }
}
async function notify_review_transfer(
  system: ConversationSystem,
  database: Database,
  notice: ReviewTransferNotice,
  now: number,
): Promise<void> {
  if (notify_new_review_holder(notice.new_holder)) {
    const message =
      notice.mover_name !== null
        ? `${notice.mover_name} moved the review of ${notice.ticket_title} to you.`
        : `You now hold the review of ${notice.ticket_title}.`;
    await deliver(
      system,
      database,
      notice.ticket_id,
      notice.new_holder,
      text_message_content(message),
      now,
    );
  }
  if (notice.notify_previous_holder) {
    await deliver(
      system,
      database,
      notice.ticket_id,
      notice.previous_holder,
      text_message_content(
        `${notice.mover_name} moved the review of ${notice.ticket_title} to ${notice.new_holder_name}.`,
      ),
      now,
    );
  }
}
async function deliver(
  system: ConversationSystem,
  database: Database,
  ticket_id: string,
  target: Principal,
  content: MessageContent,
  now: number,
): Promise<void> {
  if (target.kind === 'owner') {
    await conversation_start.conversation_link_lock(`ticket:${ticket_id}`).run(async () => {
      const ticket = database.read((sql) => read_ticket(sql, ticket_id));
      let current = ticket.conversation_id;
      if (current === null) {
        const linked = await conversation_start.start_ticket_conversation(
          system,
          database,
          ticket,
          conversation_start.worker_resolve(database, ticket),
          { conversation_id: conversation_start.new_conversation_id(), now },
        );
        current = linked.conversation_id;
      }
      await system.record_message_to_owner(current, content, {
        sender_label: 'Panels',
        sender: { kind: 'ticket', id: ticket_id },
        recipient: OWNER,
      });
    });
    return;
  }
  const delivered =
    target.kind === 'manager'
      ? await manager_lifecycle_lock(target.id).run(async () => {
          const item = database.read((sql) => read_manager(sql, target.id).item);
          return conversation_start
            .conversation_link_lock(`agent:${item.manager_agent_key}`)
            .run(() =>
              conversation_start.send_to_agent_conversation(
                system,
                database,
                item.manager_agent_key,
                content,
                conversation_start.manager_resolve(database, item),
                {
                  conversation_id: conversation_start.read_agent_conversation(
                    database,
                    item.manager_agent_key,
                  ),
                  sender_label: 'Panels',
                  mode: 'queue',
                  required_manager_id: item.id,
                },
              ),
            );
        })
      : await conversation_start.conversation_link_lock(`agent:${CHIEF_SETTINGS_KEY}`).run(() =>
          conversation_start.send_to_agent_conversation(
            system,
            database,
            CHIEF_SETTINGS_KEY,
            content,
            conversation_start.agent_resolve(database),
            {
              conversation_id: conversation_start.read_agent_conversation(
                database,
                CHIEF_SETTINGS_KEY,
              ),
              sender_label: 'Panels',
              mode: 'queue',
            },
          ),
        );
  if (delivered.fate instanceof PromptDeliverySuppressed) {
    console.info(
      `review transfer notice suppressed (ticket=${ticket_id} reason=${delivered.fate.reason})`,
    );
  } else if (
    delivered.fate instanceof PromptDeliveryRefused ||
    delivered.fate instanceof PromptDeliveryUncertain
  ) {
    console.warn(
      `review transfer notice did not reach its holder (ticket=${ticket_id})`,
      delivered.fate,
    );
  }
}
