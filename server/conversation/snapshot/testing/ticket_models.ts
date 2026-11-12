/** Mirror the existing Python browser host's explicit synthetic catalog control. */
import { ConversationBackendKey } from '../../contracts.ts';
import type {
  BackendSnapshotServiceContract,
  BackendSnapshot,
  BackendUpdateResult,
} from '../contracts.ts';
export class TicketModelSnapshots implements BackendSnapshotServiceContract {
  credentials_unavailable = false;
  calls = 0;
  model = 'gpt-scripted';
  models: readonly string[] = [];
  efforts: readonly string[] = ['medium'];
  retained(): BackendSnapshot | null {
    return null;
  }
  async snapshots(): Promise<readonly BackendSnapshot[]> {
    return Promise.all(Object.values(ConversationBackendKey).map((key) => this.snapshot(key)));
  }
  async snapshot(backend_key: ConversationBackendKey): Promise<BackendSnapshot> {
    this.calls++;
    return {
      backend_key,
      installed: true,
      executable_path: '/scripted',
      version: '1.0.0',
      identity: {
        status: this.credentials_unavailable ? 'unauthenticated' : 'authenticated',
        account_label: null,
        detail: null,
        login_command: null,
      },
      available_models: (this.models.length ? this.models : [this.model]).map((model_id) => ({
        model_id,
        display_name: null,
        detail: null,
        reasoning_effort_options: this.efforts,
        selected_variant: null,
        version: [],
        resolved_model_id: null,
      })),
      reasoning_effort_options: this.efforts,
      default_model_id: this.model,
      default_reasoning_effort: 'medium',
      update_advisory: null,
      diagnoses: [],
      models_complete: true,
    };
  }
  async update_backend(): Promise<BackendUpdateResult> {
    return {
      outcome: 'failed',
      detail: 'There is no update Panels can run for this backend.',
      output_tail: '',
    };
  }
}
