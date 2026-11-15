import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);

/** The package's worker/default entry uses the same entity map without document.
 * @returns {import("vite").Plugin[]}
 */
export function markdownWorkerPlugins() {
  return [{
    name: 'markdown-worker-character-references',
    enforce: 'pre',
    resolveId(source) {
      if (source === 'decode-named-character-reference') return require.resolve(source);
    }
  }];
}
