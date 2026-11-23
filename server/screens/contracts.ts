/** The server names the exact screen snapshot that belongs to its installed release. */
export interface ScreenDescriptor {
  sha256: string;
}

export interface ScreenFile {
  path: string;
  sha256: string;
}

/**
 * Files are sorted by path. The snapshot digest is SHA256 of UTF-8 JSON.stringify
 * with version then files, and path then sha256 for each file.
 */
export interface ScreenManifest {
  version: 1;
  files: ScreenFile[];
}

/** File values are base64 bytes. The manifest covers every payload file. */
export interface ScreenPayload {
  manifest: ScreenManifest;
  files: Record<string, string>;
}

/** Independent of optional publication parts. Null means no screen build exists. */
export interface ScreenMeta {
  screens: ScreenDescriptor | null;
  waiting_screens: ScreenDescriptor | null;
}
