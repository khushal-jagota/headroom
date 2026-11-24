/** Installation records bind a login account and its server Node, never a Python probe. */
import { accessSync, constants, fstatSync, lstatSync, readFileSync, realpathSync } from 'node:fs';
import { dirname, isAbsolute, join } from 'node:path';
import { is_record } from '../../../core/records.ts';
import { selected_release } from '../installer/selection.ts';
import { path_is_within } from '../../../core/path_containment.ts';
import {
  close_native_descriptor,
  open_directory_descriptor,
  open_relative,
} from '../../../executor/mac/platform.ts';
import type { DarwinHomeIdentity } from './contracts.ts';

export function home_node(root: string, path: string): string {
  if (!isAbsolute(path) || path.includes('\0')) throw new Error('Installer Node must be absolute');
  const actual = realpathSync(path);
  if (
    !lstatSync(actual).isFile() ||
    (path_is_within(root, actual) && actual !== join(selected_release(root), 'node/bin/node'))
  )
    throw new Error('Installer Node must be the bundled server Node or an external executable');
  accessSync(actual, constants.X_OK);
  return actual;
}
export function read_owned_home_file(path: string, uid: number): Buffer {
  const parent = open_directory_descriptor(dirname(path));
  let descriptor: number | undefined;
  try {
    descriptor = open_relative(
      parent,
      path.slice(dirname(path).length + 1),
      constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK,
    );
    const info = fstatSync(descriptor);
    if (!info.isFile() || info.uid !== uid || info.nlink !== 1)
      throw new Error(`Mac home file has a foreign owner or type: ${path}`);
    return readFileSync(descriptor);
  } finally {
    try {
      if (descriptor !== undefined) close_native_descriptor(descriptor);
    } finally {
      close_native_descriptor(parent);
    }
  }
}
export function read_home_identity(root: string, service: string, uid: number): DarwinHomeIdentity {
  if (lstatSync(root).isSymbolicLink())
    throw new Error('Mac home root must not be a symbolic link');
  root = realpathSync(root);
  const value: unknown = JSON.parse(
    read_owned_home_file(join(root, 'data/mac-home-installation.json'), uid).toString('utf8'),
  );
  if (!is_record(value)) throw new Error('Mac installation identity is invalid');
  if (value.version !== 2) throw new Error('Mac installation identity has an unsupported version');
  const keys = ['version', 'uid', 'root', 'label', 'home_plist', 'backup_plist', 'installer_node'];
  if (Object.keys(value).length !== keys.length || !keys.every((key) => Object.hasOwn(value, key)))
    throw new Error('Mac installation identity has missing or unknown fields');
  if (
    value.uid !== uid ||
    value.root !== root ||
    typeof value.label !== 'string' ||
    /^[A-Za-z0-9][A-Za-z0-9.-]*$/.exec(value.label)?.[0] !== value.label
  )
    throw new Error('Mac installation identity differs from this account or root');
  const label = value.label,
    domain = `gui/${uid}`;
  if (service !== `${domain}/${label}`)
    throw new Error('Mac service differs from this installation');
  const home_plist = join(root, 'agents', label + '.plist'),
    backup_plist = join(root, 'agents', label + '.backup.plist');
  if (value.home_plist !== home_plist || value.backup_plist !== backup_plist)
    throw new Error('Mac installation plist differs from this home');
  const selected = value.installer_node;
  if (typeof selected !== 'string') throw new Error('Mac installation Node is invalid');
  const installer_node = home_node(root, selected);
  if (installer_node !== selected && selected !== join(root, 'app/node/bin/node'))
    throw new Error('Mac installation Node must be canonical');
  return { version: 2, uid, root, label, domain, home_plist, backup_plist, installer_node };
}
