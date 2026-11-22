/** Version identity is diagnostic data, never an authentication credential. */
export const CLIENT_IDENTITY_HEADER = 'X-Panels-Client';
export type ClientKind = 'ios' | 'desktop' | 'browser' | 'machine' | 'cli';
export interface ClientIdentity {
  kind: ClientKind;
  device_id: string;
  version: string;
  build?: number;
  bridge_version?: number;
  protocol_version?: number;
  outbox_format?: number;
}
export interface ClientCompatibility {
  ios: { minimum_build: number };
  desktop_shell: { minimum_bridge_version: number };
  machine: { minimum_protocol_version: number; maximum_protocol_version: number };
  outbox_format: number;
}
