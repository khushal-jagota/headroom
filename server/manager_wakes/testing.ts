import type { Area } from '../core/authority/register.ts';
import { ConversationLinkLock } from '../runtime/conversation_start.ts';
import { ManagerWakeLoop, type WakeRuntimeContext } from './runtime.ts';

/** One entry in the shared comparison host, composed only after database ownership. */
export type OwnedWakeContext = WakeRuntimeContext;
export function managerWakeTestArea(context: OwnedWakeContext): Area {
  let loop: ManagerWakeLoop | undefined;
  const passes = new ConversationLinkLock();
  return {
    prefix: '/api/test/poll',
    routes: async (app) => {
      app.addHook('onClose', async () => {
        await loop?.stop();
      });
      app.post('/manager-wakes', async () =>
        passes.run(async () => {
          context.assert_database_owned();
          if (!context.conversation_system)
            throw new Error('manager wake test requires a conversation system');
          loop ??= new ManagerWakeLoop({
            ...context,
            conversation_system: context.conversation_system,
          });
          return { batch_ids: (await loop.poll_once()).sort((a, b) => a - b) };
        }),
      );
    },
  };
}
