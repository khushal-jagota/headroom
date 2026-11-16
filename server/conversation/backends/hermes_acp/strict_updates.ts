/** Validate the complete official wire shape before SDK best-effort deserialization. */
import { readFileSync } from 'node:fs';
import { Ajv2020 } from 'ajv/dist/2020.js';

const schema = JSON.parse(
  readFileSync(
    new URL(import.meta.resolve('@agentclientprotocol/sdk/schema/v1/schema.unstable.json')),
    'utf8',
  ),
) as { $defs: Record<string, object> };
const validator = new Ajv2020({
  strict: false, // The SDK's x-* deserialization annotations are not validation rules.
  validateFormats: false, // int64/uint64/double are annotations; schema types/ranges still apply.
  coerceTypes: false,
  useDefaults: false,
  removeAdditional: false,
}).compile({ $ref: '#/$defs/SessionNotification', $defs: schema.$defs });

export function require_strict_session_notification(value: unknown): void {
  if (!validator(value)) throw new Error('invalid ACP session/update notification');
}
