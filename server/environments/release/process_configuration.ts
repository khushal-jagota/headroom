/** Build tools can emit full ELF tables. Overflow refuses the result before parsing. */
export const MAX_BUILD_COMMAND_OUTPUT_BYTES = 64 * 1024 * 1024;
export const RUNTIME_PROBE_TIMEOUT_SECONDS = 45;
export const RUNTIME_ADMISSION_TIMEOUT_SECONDS = 120;
export const RUNTIME_PROBE_REPORT_CHARACTERS = 1024 * 1024;
export const RUNTIME_PROBE_DIAGNOSTIC_BYTES = 2000;
