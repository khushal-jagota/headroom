import type { Continuity } from '../../../src/lib/navigation/contracts';
import { WIRE_VERSION } from '../../../src/lib/navigation/contracts';

export function encodeCursor(value: Continuity): string {
  const text = JSON.stringify({epoch:value.epoch, revision:value.revision, schema_version:value.schema_version, scope:value.scope, semantics:value.semantics, wire_version:WIRE_VERSION});
  return btoa(String.fromCharCode(...new TextEncoder().encode(text))).replaceAll('+','-').replaceAll('/','_').replace(/=+$/, '');
}
