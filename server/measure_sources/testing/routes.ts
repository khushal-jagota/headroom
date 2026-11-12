/** One awaited schedule pass, matching the Python comparison host without a dispatcher. */
import type { Area, OwnedAreaContext } from '../../core/authority/register.ts';
import { Runtime } from '../runtime/supervisor.ts';
import { SourceScheduleLoop } from '../schedule_loop.ts';
import { build_participants } from '../scheduling.ts';
export function sourceTestArea(context: OwnedAreaContext): Area {
  const loop = new SourceScheduleLoop({
    database: context.database,
    clock: context.clock,
    participants: build_participants(context, new Runtime()),
    assert_database_owned: context.assert_database_owned,
    workspace_admission: context.conversationSystem?.workspace_admission,
  });
  return {
    prefix: '',
    routes: async (app) => {
      app.post('/api/test/poll/sources', async () => {
        loop.poll_once();
        return { complete: true };
      });
      app.addHook('onClose', async () => loop.stop({ deadline: performance.now() / 1000 + 10 }));
    },
  };
}
