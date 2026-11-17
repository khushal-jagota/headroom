/** Readable tool output shared by public conversation reads. */
import { built_in_driver_of } from '../drivers/built_in_manifests.ts';
import type { BackendInstanceId } from '../drivers/core/identity.ts';

/** The backend's driver reads the stored detail; a null key or a driver without the hook keeps it. */
export function readable_tool_call_detail(
  detail: string | null,
  options: {
    backend_key: BackendInstanceId | null;
  },
): string | null {
  if (detail === null || options.backend_key === null) return detail;
  const hook = built_in_driver_of(options.backend_key)?.readable_tool_call_detail;
  // Null is an answer, so no `??`.
  return hook === undefined ? detail : hook(detail);
}
