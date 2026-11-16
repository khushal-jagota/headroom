/** Shared executor archive names and bounded inspection limits. */
export const MAC_UPDATE_SCHEMA_VERSION = 1;
export const MAC_RUNTIME_ARCHIVE_NAME = 'panels-mac-runtime.tar.gz';
export const LINUX_RUNTIME_ARCHIVE_NAME = 'panels-linux-executor.tar.gz';
export const LINUX_RUNTIME_PROOF_NAME = 'linux-executor-publication.json';
export const MAC_RUNTIME_MANIFEST_NAME = 'mac-runtime.json';
export const MAX_MAC_RUNTIME_ARCHIVE_BYTES = 2 * 1024 ** 3;
export const MAX_MAC_RUNTIME_EXPANDED_BYTES = 4 * 1024 ** 3;
export const MAX_MAC_RUNTIME_MANIFEST_BYTES = 16 * 1024;
export const MAX_MAC_UPDATE_DETAIL_CHARACTERS = 500;
export const MAC_RUNTIME_INSPECTION_TIMEOUT_SECONDS = 120;
