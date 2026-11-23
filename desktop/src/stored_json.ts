// Small JSON files in the app's own data folder.
import { app } from 'electron';
import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

export function readJson<T>(name: string): T | null {
  try {
    return JSON.parse(readFileSync(join(app.getPath('userData'), name), 'utf8')) as T;
  } catch {
    return null;
  }
}

export function writeJson(name: string, value: unknown): void {
  const folder = app.getPath('userData');
  mkdirSync(folder, { recursive: true });
  const path = join(folder, name);
  writeFileSync(`${path}.next`, JSON.stringify(value, null, 2));
  renameSync(`${path}.next`, path);
}
