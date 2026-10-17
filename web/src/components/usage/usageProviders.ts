/** Usage supports Codex and Claude. Other backend capabilities stay in Config. */
export function usageBackends<T extends { backend_key: string }>(backends: readonly T[]): T[] {
  return backends.filter(backend => backend.backend_key === "codex" || backend.backend_key === "claude");
}
