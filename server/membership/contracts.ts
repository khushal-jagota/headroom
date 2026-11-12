import type { TransactionSql } from '../core/database/contracts.ts';
import type { Principal } from '../core/contracts.ts';
import type { ReviewTransferNotice } from '../tickets/contracts.ts';
export type Collection = 'manager_tickets' | 'blockers';
export interface MembershipWrite {
    tx: TransactionSql;
    principal: Principal;
    container_id: string;
    member_id: string;
    now: number;
    review_transfer_notices: ReviewTransferNotice[];
}
export interface MembershipAnswer {
    collection: Collection;
    container_id: string;
    member_id: string;
    ok: true;
}
