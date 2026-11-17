import type { BackendInstanceId } from '../../drivers/core/identity.ts';
import type { BackendSnapshotSource } from '../snapshot/contracts.ts';
import type { ExecutorIdentity } from '../../execution_identity.ts';
export interface ModelVariant {
  readonly backend_key: BackendInstanceId;
  readonly name: string;
}
export interface RemoteCatalogSource extends BackendSnapshotSource {
  readonly identity: ExecutorIdentity | null;
  readonly ready: boolean;
  owns_conversation(conversation_id: string): boolean;
}
