import type { FastifyPluginAsync } from 'fastify';
import type { AreaServices } from '../core/authority/register.ts';
import { PlannerError } from '../core/contracts.ts';
import { notify_review_transfers } from '../tickets/review_transfer.ts';
import type { ReviewTransferNotice } from '../tickets/contracts.ts';
import type { Collection, MembershipAnswer } from './contracts.ts';
import { ENTRIES } from './collections.ts';
export const routes: FastifyPluginAsync<AreaServices> = async (app, services) => {
  for (const method of ['put', 'delete'] as const) {
    app[method]<{
      Params: {
        collection: string;
        container_id: string;
        member_id: string;
      };
    }>('/collections/:collection/:container_id/:member_id', async (request) => {
      const { collection, container_id, member_id } = request.params;
      if (
        collection !== 'manager_tickets' &&
        collection !== 'outcome_tickets' &&
        collection !== 'blockers'
      ) {
        throw new PlannerError('validation', 'invalid collection', { collection });
      }
      const kind: Collection = collection === 'outcome_tickets' ? 'manager_tickets' : collection;
      const notices: ReviewTransferNotice[] = [];
      const now = services.clock.nowUnix();
      services.database.write((tx) => {
        ENTRIES[kind][method === 'put' ? 'add' : 'remove']({
          tx,
          principal: services.identity(request).principal,
          container_id,
          member_id,
          now,
          review_transfer_notices: notices,
        });
      });
      if (services.conversationSystem) {
        await notify_review_transfers(services.conversationSystem, services.database, notices, {
          now,
        });
      }
      const answer: MembershipAnswer = {
        collection: kind,
        container_id,
        member_id,
        ok: true,
      };
      return answer;
    });
  }
};
