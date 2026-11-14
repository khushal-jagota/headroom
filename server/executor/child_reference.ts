/** Both executor peers compare the complete retained child identity. */
import type { ChildReference } from './wire.generated.ts';

export function same_reference(left: ChildReference | null, right: ChildReference): boolean {
  return (
    left !== null &&
    left.executor_incarnation === right.executor_incarnation &&
    left.child_id === right.child_id &&
    left.conversation_id === right.conversation_id
  );
}
