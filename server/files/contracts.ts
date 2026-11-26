export interface ArtifactEntry {
  name: string;
  opens: string | null;
  modified_at: number;
  children: ArtifactEntry[];
}
export type OwnerKind = 'ticket' | 'manager' | 'goal';
export interface CatalogEntry {
  key: string;
  path: string;
  label: string;
  kind: string;
  href: string | null;
  directory: string | null;
  child_count: number;
}
export interface FolderListing {
  checked_at: number;
  rows: CatalogEntry[];
  revision: string;
  bytes: number;
}
