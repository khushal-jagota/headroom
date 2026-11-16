/** Count replay events by Xcode task, excluding misses and duplicate "Cache hit" lines. */
export function cache_diagnostics(output: string) {
  const cache_hit_lines: string[] = [];
  const source_cache_keys: { task: string; key: string }[] = [];
  const cache_hits = { source: 0, module: 0, other: 0 };
  let task = "";
  for (const line of output.split("\n")) {
    const task_header = /^(Swift\S+)\s/.exec(line);
    if (task_header) task = task_header[1]!;
    const key =
      /^note: local cache (?:miss|hit|found) for key: (llvmcas:\/\/[a-f0-9]+)/i.exec(
        line,
      );
    if (key && (task === "SwiftCompile" || task === "SwiftEmitModule"))
      source_cache_keys.push({ task, key: key[1]! });
    if (!/^note: replay cache hit(?:\s|$)/i.test(line)) continue;
    cache_hit_lines.push(line);
    if (task === "SwiftCompile" || task === "SwiftEmitModule")
      cache_hits.source++;
    else if (task.startsWith("SwiftExplicitDependency")) cache_hits.module++;
    else cache_hits.other++;
  }
  return { cache_hit_lines, cache_hits, source_cache_keys };
}
