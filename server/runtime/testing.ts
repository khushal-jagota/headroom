/** One explicitly requested pass. This module never constructs a provider. */
import type { Area } from '../core/authority/register.ts';
import { PlannerError } from '../core/contracts.ts';
import { configured_worker_type_registry } from '../worker_types/configuration.ts';
import { WorkerStepReadinessLoop } from './worker_step_readiness_loop.ts';
export const readinessTestRoutes: Area = {
  prefix: '/api/test/poll',
  routes: async (app, services) => {
    let loop: WorkerStepReadinessLoop | undefined;
    let previous = Promise.resolve();
    app.post('/readiness', async () => {
      if (!services.config.test_mode || !services.conversationSystem) {
        throw new PlannerError('validation', 'readiness passes require a test conversation system');
      }
      loop ??= new WorkerStepReadinessLoop({
        connect_database: () => services.database,
        conversation_system: services.conversationSystem,
        worker_type_registry: configured_worker_type_registry(),
        now: () => services.clock.nowUnix(),
      });
      const pass = previous.then(async () => {
        const ticket_ids = loop!.poll_once();
        await loop!.drain();
        return { ticket_ids: ticket_ids.sort() };
      });
      previous = pass.then(
        () => undefined,
        () => undefined,
      );
      return pass;
    });
    app.addHook('onClose', async () => {
      await loop?.stop();
    });
  },
};
