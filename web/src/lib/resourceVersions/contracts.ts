// Generated from planner.resource_versions.contracts. Do not edit.
export interface ResourceVersion { key: string; revision: string | null; }
export interface ResourceVersionsResponse { epoch: string; resources: Array<ResourceVersion>; }
export interface ResourceBodyResponse { epoch: string; key: string; revision: string; body: unknown; }
