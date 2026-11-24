/** Resolve the candidate's static ESM graph without evaluating application or package code. */
import { readFileSync, realpathSync, statSync } from 'node:fs';
import Module, { isBuiltin, stripTypeScriptTypes } from 'node:module';
import { extname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { Script, SourceTextModule } from 'node:vm';
import { path_is_within } from '../../core/path_containment.ts';

export function verify_static_runtime_imports(root: string, entries: readonly string[]): void {
  const candidate = realpathSync(root);
  const seen = new Set<string>();
  function visit(url: string, importer: string): void {
    if (isBuiltin(url)) return;
    let path: string;
    try {
      path = realpathSync(fileURLToPath(url));
      if (!path_is_within(candidate, path) || !statSync(path).isFile())
        throw new Error('static import must be a file inside candidate');
    } catch (error) {
      throw new Error(`Unresolved static runtime import ${url} from ${importer}`, { cause: error });
    }
    if (seen.has(path)) return;
    seen.add(path);
    const extension = extname(path);
    if (['.json', '.node', '.cjs', '.cts'].includes(extension)) return;
    let source = readFileSync(path, 'utf8');
    if (extension === '.ts' || extension === '.mts') source = stripTypeScriptTypes(source);
    let module: SourceTextModule;
    try {
      module = new SourceTextModule(source, { identifier: pathToFileURL(path).href });
    } catch (error) {
      // A .js CommonJS file can use syntax such as top-level return. It has no static ESM imports.
      if (extension !== '.js' || !(error instanceof SyntaxError)) throw error;
      new Script(Module.wrap(source), { filename: path });
      return;
    }
    for (const specifier of module.dependencySpecifiers) {
      if (isBuiltin(specifier)) continue;
      let dependency: string;
      try {
        dependency = import.meta.resolve(specifier, pathToFileURL(path).href);
      } catch (error) {
        throw new Error(`Unresolved static runtime import ${specifier} from ${path}`, {
          cause: error,
        });
      }
      visit(dependency, path);
    }
  }
  for (const entry of entries) visit(pathToFileURL(join(candidate, entry)).href, '<entrypoint>');
}
