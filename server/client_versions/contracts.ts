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
/** Optional installed publication facts. Publication and installation own their source. */
export interface ReleaseParts {
  server: { app_sha: string; artifact_digest: string };
  screens: { sha256: string };
  machines: Record<string, { archive_sha256: string; build_id: string | null }>;
}
export interface ClientVersionMeta {
  compatibility: ClientCompatibility;
  release_number?: number;
  parts?: ReleaseParts;
}
