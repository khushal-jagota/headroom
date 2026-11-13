/** Retained native custody. Command outcomes and event retirement stay with the runner. */
import type {
  BackendChild,
  BackendChildFactory,
  BackendEventSink,
} from '../../../conversation/backends/contracts.ts';
import type { ResolvedConversationStart } from '../../../conversation/contracts.ts';
import type { ConversationMessageFiles } from '../../../conversation/message_files.ts';
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import type { ChildReference, StartRun, TurnToken } from '../../wire.generated.ts';
import type { ExecutorPaths } from '../configuration.ts';
import type { FilesService } from '../files.ts';
import type { MacSkillsPublisher } from '../skills/index.ts';
import type { FifoLock } from '../../../conversation/runtime/state.ts';
export interface MacRunningChild {
  readonly reference: ChildReference;
  readonly capability: string;
  readonly native: BackendChild;
  readonly ticket_id: string;
  readonly launch_input: StartRun;
  readonly localized_start: ResolvedConversationStart;
  readonly lock: FifoLock;
  active_turn: TurnToken | null;
  model: string | null;
  reasoning_effort: string | null;
}
export class MacChildAdmissionRefused extends Error {}
export interface MacNativeChildrenOptions {
  readonly paths: ExecutorPaths;
  readonly workspace_home: string;
  readonly factory: (start: ResolvedConversationStart) => BackendChildFactory;
  readonly native_skill_home: (backend: ResolvedConversationStart['backend_key']) => string;
  readonly event_sink: (reference: ChildReference) => BackendEventSink;
  readonly message_files: ConversationMessageFiles;
  readonly files: Pick<FilesService, 'open_directory' | 'close_directory'>;
  /** This owner closes publication before it joins native starts during shutdown. */
  readonly skills: Pick<MacSkillsPublisher, 'install_native_skill_snapshot' | 'stop' | 'settled'>;
  readonly record_children: () => Promise<void>;
  readonly on_failure: (error: Error) => void;
}
export interface MacNativeChildrenContract {
  readonly admission_stopped: boolean;
  start(reference: ChildReference, input: StartRun): Promise<MacRunningChild>;
  authenticate(child_id: string | null, capability: string | null): MacRunningChild;
  get(child_id: string): MacRunningChild | null;
  is_retiring(child_id: string): boolean;
  /** Fence new native work first. The command owner joins its commands/source work separately. */
  begin_retirement(reference: ChildReference): void;
  stop_child(reference: ChildReference): Promise<void>;
  /** Called only after the runner's required source work and stop proof settle. */
  retire(reference: ChildReference): void;
  stop(options: StopOptions): Promise<void>;
  settled(): Promise<void>;
}
