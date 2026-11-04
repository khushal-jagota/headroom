import { describe, expect, it } from 'vitest';
import { compareRevision, encodeCursor } from '../src/lib/navigation/identity';
import { SCHEMA_VERSION, SUPPORTED_SEMANTICS, WIRE_VERSION } from '../src/lib/navigation/contracts';

describe('navigation identities', () => {
  it('keeps large revisions exact and encodes the server cursor', () => {
    const continuity: import('../src/lib/navigation/contracts').Continuity = {scope:'navigation',schema_version:SCHEMA_VERSION,semantics:SUPPORTED_SEMANTICS,epoch:'é',revision:'9007199254740993'};
    expect(compareRevision('9007199254740993','9007199254740992')).toBe(1);
    const encoded = encodeCursor(continuity);
    const bytes = Uint8Array.from(atob(encoded.replaceAll('-','+').replaceAll('_','/') + '='.repeat((4-encoded.length%4)%4)), c => c.charCodeAt(0));
    expect(JSON.parse(new TextDecoder().decode(bytes))).toEqual({...continuity,wire_version:WIRE_VERSION});
  });
});
