/** Keep one import identity across address changes inside an already mounted view. */
const modules = new Map<string, Promise<unknown>>();

export function cachedImport(key: string, load: () => Promise<unknown>): Promise<unknown> {
  let module = modules.get(key);
  if (!module) { module = load(); modules.set(key, module); }
  return module;
}
