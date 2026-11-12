/** Explicit test-only scheduling and collection, without a background dispatcher. */
import { setTimeout } from 'node:timers/promises';
import type { Area, OwnedAreaContext } from '../../core/authority/register.ts';
import { SourceDispatcher } from '../dispatcher.ts';
import { Runtime } from '../runtime/supervisor.ts';
import { SourceScheduleLoop } from '../schedule_loop.ts';
import { build_participants } from '../scheduling.ts';
export function sourceTestArea(context: OwnedAreaContext): Area {
  const runtime = new Runtime();
  const dispatcher = new SourceDispatcher({ context, runtime });
  const loop = new SourceScheduleLoop({
    database: context.database,
    clock: context.clock,
    participants: build_participants(context, runtime),
    assert_database_owned: context.assert_database_owned,
    workspace_admission: context.conversationSystem?.workspace_admission,
  });
  return {
    prefix: '',
    routes: async (app) => {
      app.post<{ Body: { dispatch?: boolean } }>('/api/test/poll/sources', async (request) => {
        loop.poll_once();
        // Admission comparisons intentionally leave runs pending; browsers request collection.
        if (!request.body?.dispatch) return { complete: true };
        const claimed = dispatcher.poll_once();
        while (
          context.database.read((sql) =>
            claimed.some(
              (id) =>
                sql.get<{ status: string }>('SELECT status FROM measure_source_runs WHERE id=?', [
                  id,
                ])?.status === 'running',
            ),
          )
        ) {
          await setTimeout(10);
        }
        return { complete: true };
      });
      app.addHook('onClose', async () => {
        const options = { deadline: performance.now() / 1000 + 10 };
        await Promise.all([loop.stop(options), dispatcher.stop(options)]);
      });
    },
  };
}
