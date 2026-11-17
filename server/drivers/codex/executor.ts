/** Codex on an executor machine: its app-server launch and its model list probe. */
import { with_machine_environment } from '../core/executor.ts';
import type { ExecutorDriver } from '../core/executor.ts';
import {
  codex_app_server_child_launch,
  create_codex_backend_child_factory,
} from './runtime/adapter.ts';
import { RememberedCodexLoginRejection } from './runtime/login.ts';
import { probe_codex_model_catalog } from './runtime/model_catalog.ts';
import { read_codex_login_standing } from './runtime/providers.ts';
import { create_codex_status } from './runtime/status.ts';

export const codex_executor: ExecutorDriver = {
  child_factory(host) {
    return create_codex_backend_child_factory(
      with_machine_environment(
        codex_app_server_child_launch({
          codex_executable: host.executable,
          panels_server_url: host.panels_server_url,
        }),
        host,
      ),
      {
        child_processes: host.child_processes,
        worker_supervision: host.worker_supervision,
        // A machine has no Panels database, so the rejection is remembered per factory.
        remembered_login_rejection: new RememberedCodexLoginRejection(),
      },
    );
  },
  catalog(host, custody) {
    return {
      status: create_codex_status({
        catalog: (executable) =>
          custody.probe((signal) =>
            probe_codex_model_catalog(executable, {
              child_processes: host.child_processes,
              signal,
              environment: host.environment,
            }),
          ),
        // Machine cards read login standing independently of each child factory.
        read_login_standing: () => read_codex_login_standing(null, host.home!),
      }),
    };
  },
};
