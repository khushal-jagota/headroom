/** Claude on an executor machine: its Agent SDK launch and its model list probe. */
import { with_machine_environment } from '../core/executor.ts';
import type { ExecutorDriver } from '../core/executor.ts';
import { create_claude_backend_child_factory } from './runtime/adapter.ts';
import { ClaudeModelCatalogUnavailable } from './runtime/contracts.ts';
import {
  probe_claude_model_catalog,
  ClaudeModelCatalogUnavailable as NativeClaudeUnavailable,
} from './runtime/model_catalog.ts';
import { create_claude_status } from './runtime/status.ts';

export const claude_executor: ExecutorDriver = {
  child_factory(host) {
    return create_claude_backend_child_factory(
      with_machine_environment(
        {
          claude_executable: host.executable,
          environment_overrides: [['PLAN_SERVER_URL', host.panels_server_url]],
        },
        host,
      ),
      {
        child_processes: host.child_processes,
        one_shot_children: host.one_shot_children,
        worker_supervision: host.worker_supervision,
      },
    );
  },
  catalog(host, custody) {
    return {
      status: create_claude_status({
        catalog: (executable) =>
          custody.probe(async (signal) => {
            try {
              return await probe_claude_model_catalog(executable, host.one_shot_children, {
                signal,
                stop_options: () => custody.stop_options(),
                environment: host.environment,
              });
            } catch (error) {
              if (error instanceof NativeClaudeUnavailable)
                throw new ClaudeModelCatalogUnavailable(error.message, { cause: error });
              throw error;
            }
          }),
      }),
    };
  },
};
