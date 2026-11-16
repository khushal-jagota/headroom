/** Inventory only validated, extracted sealed bytes, before admission tools run. */
import { createHash } from 'node:crypto';
import { lstat, readFile, readlink, realpath } from 'node:fs/promises';
import { basename, dirname, isAbsolute, join, relative, sep } from 'node:path';
import { is_file, missing, sha256_file } from '../backup/evidence.ts';
import { path_is_within } from '../../core/path_containment.ts';
import { AppValidationError } from './contracts.ts';
import { app_tree_paths } from './identity.ts';
import type {
  DeclaredLicenseFile,
  PackageLicenseInventory,
  PackageLicenseRecord,
  RetainedLicenseFile,
} from './package_license_contracts.ts';

type MutablePackage = {
  -readonly [
    Key in keyof PackageLicenseRecord
  ]: PackageLicenseRecord[Key] extends readonly (infer Item)[] ? Item[] : PackageLicenseRecord[Key];
};
const order = (left: string, right: string): number =>
  Buffer.compare(Buffer.from(left), Buffer.from(right));
const own_name = /^(LICENSE|LICENCE|COPYING)([._-].+)?$/i;
const notice_name = /^(NOTICE|COPYRIGHT)([._-].+)?$/i;
function installed_slot(path: string): boolean {
  const parts = path.split('/'),
    index = parts.lastIndexOf('node_modules');
  if (index < 0) return false;
  const tail = parts.slice(index + 1);
  return (
    (tail.length === 1 && !tail[0]!.startsWith('@') && !tail[0]!.startsWith('.')) ||
    (tail.length === 2 && tail[0]!.startsWith('@') && !tail[1]!.startsWith('.'))
  );
}
function declared_text(metadata: Record<string, unknown>, field: string): string | null {
  if (!Object.hasOwn(metadata, field)) return null;
  if (typeof metadata[field] !== 'string')
    throw new AppValidationError(`package metadata ${field} must be a string`);
  return metadata[field];
}
export async function inventory_package_licenses(
  sealed_root: string,
): Promise<PackageLicenseInventory> {
  const root = await realpath(sealed_root),
    paths = await app_tree_paths(root);
  const archive_path = (path: string): string => relative(root, path).split(sep).join('/') || '.';
  const packages = new Map<string, MutablePackage>();
  for (const path of paths) {
    if (basename(path) !== 'package.json') continue;
    const actual = await realpath(path);
    if (!path_is_within(root, actual) || !(await is_file(actual)))
      throw new AppValidationError('package metadata must be a regular file inside the payload');
    const bytes = await readFile(actual);
    const value: unknown = JSON.parse(
      new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes),
    );
    if (value === null || typeof value !== 'object' || Array.isArray(value))
      throw new AppValidationError('package metadata must be a JSON object');
    const metadata = value as Record<string, unknown>,
      directory = dirname(path),
      name = declared_text(metadata, 'name');
    const location = archive_path(directory),
      slot = installed_slot(location);
    packages.set(directory, {
      root: location,
      kind:
        name === null
          ? 'descriptor'
          : ['.', 'server', 'web', 'agent_backends'].includes(location)
            ? 'workspace'
            : slot
              ? 'installed'
              : 'embedded',
      name,
      version: declared_text(metadata, 'version'),
      installed_slots: slot ? [location] : [],
      aliases: [],
      package_json_sha256: createHash('sha256').update(bytes).digest('hex'),
      license_declaration: Object.hasOwn(metadata, 'license') ? metadata.license : null,
      license_files: [],
      notice_files: [],
      declared_file: null,
      missing_own_license: true,
    });
  }
  for (const path of paths) {
    const slot = archive_path(path);
    if (!installed_slot(slot) || !(await lstat(path)).isSymbolicLink()) continue;
    const actual = await realpath(path);
    if (!path_is_within(root, actual))
      throw new AppValidationError('package alias escapes the payload');
    const record = packages.get(actual);
    if (!record) throw new AppValidationError('package alias has no bundled package metadata');
    record.installed_slots.push(slot);
    record.aliases.push({
      path: slot,
      target: await readlink(path),
      resolved_path: archive_path(actual),
    });
    if (record.kind === 'embedded') record.kind = 'installed';
  }
  const owner = (path: string): MutablePackage | undefined => {
    let current = path;
    while (path_is_within(root, current)) {
      const record = packages.get(current);
      if (record || current === root) return record;
      current = dirname(current);
    }
    return undefined;
  };
  async function retained(path: string, actual: string): Promise<RetainedLicenseFile> {
    return {
      path: archive_path(path),
      sha256: await sha256_file(actual),
      link: (await lstat(path)).isSymbolicLink()
        ? { target: await readlink(path), resolved_path: archive_path(actual) }
        : null,
    };
  }
  const source_license_files: RetainedLicenseFile[] = [],
    source_notice_files: RetainedLicenseFile[] = [];
  for (const path of paths) {
    const name = basename(path),
      license = own_name.test(name),
      notice = notice_name.test(name);
    if (!license && !notice) continue;
    let actual: string;
    try {
      actual = await realpath(path);
    } catch (error) {
      if (!missing(error)) throw error;
      continue;
    }
    const record = owner(path);
    if (!path_is_within(root, actual) || owner(actual) !== record || !(await is_file(actual)))
      continue;
    const file = await retained(path, actual);
    (license
      ? (record?.license_files ?? source_license_files)
      : (record?.notice_files ?? source_notice_files)
    ).push(file);
  }
  for (const [directory, record] of packages) {
    const declaration =
      typeof record.license_declaration === 'string'
        ? /^SEE LICENSE IN (.+)$/.exec(record.license_declaration.trim())
        : null;
    if (!declaration) continue;
    const path = declaration[1]!;
    let finding: DeclaredLicenseFile['finding'];
    if (path.includes('\0') || isAbsolute(path) || path.split('/').includes('..'))
      finding = 'unsafe';
    else {
      try {
        const file = join(directory, path);
        if (owner(file) !== record) finding = 'outside-package';
        else {
          const actual = await realpath(file);
          if (!path_is_within(root, actual) || owner(actual) !== record)
            finding = 'outside-package';
          else if (!(await is_file(actual))) finding = 'not-file';
          else {
            const physical = join(await realpath(dirname(file)), basename(file));
            if (!path_is_within(root, physical) || owner(physical) !== record)
              finding = 'outside-package';
            else {
              const retained_file = await retained(physical, actual);
              if (!record.license_files.some((entry) => entry.path === retained_file.path))
                record.license_files.push(retained_file);
              finding = 'retained';
            }
          }
        }
      } catch (error) {
        if (!missing(error)) throw error;
        finding = 'missing';
      }
    }
    record.declared_file = { path, finding };
  }
  const records = [...packages.values()].sort((left, right) => order(left.root, right.root));
  const files = (values: RetainedLicenseFile[]) =>
    values.sort((left, right) => order(left.path, right.path));
  for (const record of records) {
    record.installed_slots.sort(order);
    record.aliases.sort((left, right) => order(left.path, right.path));
    files(record.license_files);
    files(record.notice_files);
    record.missing_own_license = record.license_files.length === 0;
  }
  return {
    version: 1,
    packages: records,
    source_license_files: files(source_license_files),
    source_notice_files: files(source_notice_files),
    missing_own_licenses: records
      .filter(
        (record) =>
          record.missing_own_license && (record.name !== null || record.installed_slots.length > 0),
      )
      .map(({ root, name, version, installed_slots }) => ({
        root,
        name,
        version,
        installed_slots,
      })),
  };
}
