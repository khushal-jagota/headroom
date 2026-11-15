// Generated from planner.files.catalog_contracts. Do not edit.
export type ArtifactOwnerKind = "ticket" | "manager" | "goal";
export interface ArtifactCatalogEntry { key: string; path: string; label: string; kind: string; href: string | null; directory: string | null; child_count: number; }
export interface ArtifactDirectoryPage { revision: string; directory: string; offset: number; total: number; entries: Array<ArtifactCatalogEntry>; }
