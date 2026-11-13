/** Build-only tool limits and the Node archive's private library location. */
export const NATIVE_TOOL_SECONDS = 60;
export const PROVENANCE_TOOL_SECONDS = 30;
export const PRIVATE_LIBRARY_DIRECTORY = 'runtime/lib';
export const LICENSE_DIRECTORY = 'runtime/licenses';
export const GLIBC_INTERPRETER = '/lib64/ld-linux-x86-64.so.2';
export const MAXIMUM_GLIBC = [2, 39] as const;
export const MACH_O_MAGIC = new Set([
  'feedface',
  'cefaedfe',
  'feedfacf',
  'cffaedfe',
  'cafebabe',
  'bebafeca',
  'cafebabf',
  'bfbafeca',
]);
