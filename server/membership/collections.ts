/** Collection entries route writes to the domain that owns their membership rules. */
import { classify_ticket, unclassify_ticket } from '../tickets/data/index.ts';
import { add_ticket_block, remove_ticket_block } from '../core/ticket_blocks.ts';
import type { Collection, MembershipWrite } from './contracts.ts';
interface CollectionEntry {
  add(write: MembershipWrite): void;
  remove(write: MembershipWrite): void;
}
export const ENTRIES: Record<Collection, CollectionEntry> = {
  manager_tickets: {
    add(write) {
      classify_ticket(write.tx, write.member_id, {
        manager_id: write.container_id,
        principal: write.principal,
        now: write.now,
        review_transfer_notices: write.review_transfer_notices,
      });
    },
    remove(write) {
      unclassify_ticket(write.tx, write.member_id, {
        manager_id: write.container_id,
        principal: write.principal,
        now: write.now,
        review_transfer_notices: write.review_transfer_notices,
      });
    },
  },
  blockers: {
    add(write) {
      add_ticket_block(write.tx, write.member_id, write.container_id, write.now);
    },
    remove(write) {
      remove_ticket_block(write.tx, write.member_id, write.container_id, write.now);
    },
  },
};
