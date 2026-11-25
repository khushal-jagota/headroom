/** Build-time check of the unpacked home, using the checkout's compiler to read executable imports. */
import { readFile, stat } from 'node:fs/promises';
import { dirname, relative, resolve } from 'node:path';
import { stripTypeScriptTypes } from 'node:module';
import { pathToFileURL } from 'node:url';
import ts from 'typescript';
import { NODE_RUNTIME_FILES, SHARED_RUNTIME_FILES } from '../configuration.ts';

export async function check_home_imports(root: string): Promise<void> {
  for (const file of [...NODE_RUNTIME_FILES, ...SHARED_RUNTIME_FILES]) {
    if (!(await stat(resolve(root, file))).isFile()) throw new Error('Missing home file: ' + file);
  }
  const files = new Set([
    'server/main.ts',
    'server/cli/main.ts',
    'server/installation/home_main.ts',
    ...NODE_RUNTIME_FILES.filter((file) => file.endsWith('.ts')),
  ]);
  for (const file of files) {
    const path = resolve(root, file);
    const source = stripTypeScriptTypes(await readFile(path, 'utf8'));
    for (const item of ts.preProcessFile(source, true, true).importedFiles) {
      if (!item.fileName.startsWith('.')) continue;
      const target = relative(root, resolve(dirname(path), item.fileName));
      if (
        target.startsWith('..') ||
        !(await stat(resolve(root, target)).catch(() => null))?.isFile()
      )
        throw new Error(`Missing packaged import: ${file} -> ${item.fileName}`);
      if (target.endsWith('.ts') || target.endsWith('.js') || target.endsWith('.mjs'))
        files.add(target);
    }
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href)
  await check_home_imports(resolve(process.argv[2]!));
