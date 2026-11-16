/** A copied installer selects existing sealed bytes. It is not another runtime inventory. */
export type InstallerRecipeEntry =
  | { readonly kind: 'file'; readonly path: string; readonly mode: number; readonly sha256: string }
  | { readonly kind: 'directory'; readonly path: string; readonly mode: number }
  | { readonly kind: 'symlink'; readonly path: string; readonly target: string };

export interface InstallerFileManifest {
  readonly version: 1;
  readonly source_sha: string;
  readonly entry: 'server/environments/updates/installer/main.ts';
  readonly runtime_inventory_sha256: string;
  readonly files: readonly InstallerRecipeEntry[];
}
