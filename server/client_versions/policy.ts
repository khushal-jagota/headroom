/** The published limits and request admission use the same values. */
import { MINIMUM_SUPPORTED_BUILD } from '../ios_compatibility/policy.ts';
import {
  MIN_SUPPORTED_PROTOCOL_VERSION,
  MAX_SUPPORTED_PROTOCOL_VERSION,
  SPOOL_FORMAT_VERSION,
} from '../execution_configuration.ts';
import type { ClientCompatibility, ClientIdentity } from './contracts.ts';

export const CLIENT_COMPATIBILITY: ClientCompatibility = {
  ios: { minimum_build: MINIMUM_SUPPORTED_BUILD },
  desktop_shell: { minimum_bridge_version: 2 },
  machine: {
    minimum_protocol_version: MIN_SUPPORTED_PROTOCOL_VERSION,
    maximum_protocol_version: MAX_SUPPORTED_PROTOCOL_VERSION,
  },
  outbox_format: SPOOL_FORMAT_VERSION,
};

export function supported_ios_build(value: unknown): boolean {
  return (
    typeof value === 'number' &&
    Number.isSafeInteger(value) &&
    value >= CLIENT_COMPATIBILITY.ios.minimum_build
  );
}

export function supported_desktop_bridge(identity: ClientIdentity): boolean {
  return (
    typeof identity.bridge_version === 'number' &&
    Number.isSafeInteger(identity.bridge_version) &&
    identity.bridge_version >= CLIENT_COMPATIBILITY.desktop_shell.minimum_bridge_version
  );
}

export function supported_machine_versions(protocol: unknown, outbox: unknown): boolean {
  return (
    typeof protocol === 'number' &&
    Number.isSafeInteger(protocol) &&
    protocol >= CLIENT_COMPATIBILITY.machine.minimum_protocol_version &&
    protocol <= CLIENT_COMPATIBILITY.machine.maximum_protocol_version &&
    outbox === CLIENT_COMPATIBILITY.outbox_format
  );
}
