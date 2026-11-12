import type { Area, OwnedAreaContext } from '../core/authority/register.ts';
import { ConversationLinkLock } from '../runtime/conversation_start.ts';
import { ManagerWakeLoop } from './runtime.ts';

/** One entry in the shared comparison host, composed only after database ownership. */
export type OwnedWakeContext = OwnedAreaContext;
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
          if (!context.conversationSystem)
            throw new Error('manager wake test requires a conversation system');
          loop ??= new ManagerWakeLoop({
            database: context.database,
            clock: context.clock,
            assert_database_owned: context.assert_database_owned,
            workspace_context: {
              db_path: context.config.db_path,
              workspace_root: context.config.workspace_root,
            },
            conversation_system: context.conversationSystem,
          });
          return { batch_ids: (await loop.poll_once()).sort((a, b) => a - b) };
        }),
      );
    },
  };
}
