export type Endpoint = readonly [host: string, port: number];
export interface PeerLookupResult {
  readonly is_owner: boolean;
  readonly skipped_unreadable_processes: number;
}
/** Readonly proc evidence. Implementations retain directory enumeration order. */
export interface ProcReader {
  readText(path: string): Promise<string>;
  list(path: string): Promise<readonly string[]>;
  uid(path: string): Promise<bigint>;
  readlink(path: string): Promise<string>;
}
export interface PeerLookupContext {
  readonly proc_root: string;
  readonly current_uid: bigint;
  readonly little_endian: boolean;
  readonly files: ProcReader;
}
export type PeerLookup = (
  client: Endpoint | null,
  server: Endpoint | null,
) => Promise<PeerLookupResult>;
export interface UnidentifiedCallRequest {
  readonly kind: 'http' | 'websocket' | 'other';
  readonly authenticated_owner: boolean;
  readonly headers: readonly (readonly [name: string, value: string])[];
  readonly client: Endpoint | null;
  readonly server: Endpoint | null;
}
export interface UnidentifiedCallAdmission {
  readonly admitted: boolean;
  /** Null means that the bypass did not inspect proc. */
  readonly peer_lookup: PeerLookupResult | null;
}
export interface UnidentifiedHttpRefusal {
  readonly status: 403;
  readonly body: {
    readonly error: {
      readonly code: 'agent_forbidden';
      readonly message: string;
      readonly detail: { readonly skipped_unreadable_processes: number };
    };
  };
}
export interface UnidentifiedWebSocketRefusal {
  readonly type: 'websocket.close';
  readonly code: 1008;
  readonly reason: string;
}
export class InvalidPeerEvidence extends Error {}
