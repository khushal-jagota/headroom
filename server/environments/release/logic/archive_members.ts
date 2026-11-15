/** node-tar owns TAR decoding. This layer owns admission policy and inspection. */
import { posix } from 'node:path';
import { createHash } from 'node:crypto';
import { isDeepStrictEqual } from 'node:util';
import { is_record } from '../../../core/records.ts';
import { Parser } from 'tar';
import type { ReadEntry } from 'tar';
import { AppValidationError, MacArchiveInspectionError, LINUX_EXECUTOR } from '../contracts.ts';
import type { MacArchiveInspection } from '../contracts.ts';
import { manifest_identity, require_runtime_target } from './manifest.ts';
import { parse_optional_runtime_contract } from '../../updates/logic/runtime_contract.ts';
import { decode_mac_manifest } from '../../updates/mac/manifest.ts';
import {
  MAX_MAC_RUNTIME_EXPANDED_BYTES,
  MAX_MAC_RUNTIME_MANIFEST_BYTES,
} from '../../updates/mac/configuration.ts';
import {
  ARCHIVE_MAX_ENTRIES,
  ARCHIVE_MAX_METADATA_BYTES,
  ARCHIVE_MAX_METADATA_ENTRY_BYTES,
  ARCHIVE_MAX_MEMBER_BYTES,
  ARCHIVE_MAX_DEPTH,
} from '../configuration.ts';

export interface ArchiveMember {
  path: string;
  type: ReadEntry['type'];
  mode: number;
  size: number;
  mtime: Date | undefined;
  linkpath: string;
}
const files = new Set<ReadEntry['type']>(['File', 'OldFile', 'ContiguousFile']);
const ordinary = new Set<ReadEntry['type']>([...files, 'Directory', 'Link', 'SymbolicLink']);
function refuse(message: string): never {
  throw new AppValidationError(message);
}
function normalized(path: string, inspection: boolean): string {
  const parts = path.split('/');
  if (posix.isAbsolute(path) || path.includes('\0') || parts.includes('..')) {
    if (inspection)
      throw new MacArchiveInspectionError('refusal', 'The Mac runtime contains an invalid path.');
    refuse('app archive contains unsafe or duplicate paths');
  }
  const clean = parts.filter((part) => part && part !== '.');
  if (!inspection && clean.length > ARCHIVE_MAX_DEPTH)
    refuse('app archive path exceeds depth limit');
  return clean.join('/');
}
export class ArchiveMembers {
  readonly members: ArchiveMember[] = [];
  manifest_bytes: Buffer | null = null;
  python = false;
  node = false;
  entrypoint = false;
  native_linux: MacArchiveInspection['native_linux'];
  readonly #platform: 'darwin' | 'linux';
  readonly #linux_files = new Set<string>();
  readonly #linux_json = new Map<string, Buffer>();
  #inventory: string | undefined;
  #panels = false;
  #linux_invalid: string | undefined;
  readonly purpose: 'extraction' | 'mac-inspection';
  readonly #parser: Parser;
  readonly #on_entry: ((entry: ReadEntry, member: ArchiveMember) => void) | undefined;
  #entries = 0;
  #metadataBytes = 0;
  #logicalSize = 0;
  #names = new Set<string>();
  #manifest = false;
  #prefix = Buffer.alloc(0);
  #started = false;

  constructor(
    purpose: 'extraction' | 'mac-inspection' = 'extraction',
    on_entry?: (entry: ReadEntry, member: ArchiveMember) => void,
    platform: 'darwin' | 'linux' = 'darwin',
  ) {
    this.#platform = platform;
    this.purpose = purpose;
    this.#on_entry = on_entry;
    this.#parser = new Parser({
      strict: true,
      zstd: false,
      brotli: false,
      maxMetaEntrySize: ARCHIVE_MAX_METADATA_ENTRY_BYTES,
      onReadEntry: (entry) => this.#entry(entry),
    });
    this.#parser.on('error', (error) => {
      throw new AppValidationError(error.message);
    });
    this.#parser.on('meta', (data: string) => {
      this.#count();
      this.#account(Buffer.byteLength(data));
      // Sparse maps alter the meaning of the body. They are not an admitted payload.
      if (/(?:^|\n)\d+ (?:GNU\.sparse\.|SCHILY\.filetype=sparse)/.test(data))
        refuse('app archive contains sparse metadata');
    });
    this.#parser.on('ignoredEntry', (entry: ReadEntry) => {
      this.#count();
      if (entry.meta) refuse('app archive exceeds metadata header limit');
      if (this.purpose === 'extraction' || entry.type === 'SparseFile')
        refuse('app archive contains unsupported entries');
      const member = this.#inspect(entry);
      if (this.#platform === 'linux') this.#linux_entry(entry, member);
      if (member.path === 'mac-runtime.json')
        throw new MacArchiveInspectionError('refusal', 'The Mac runtime manifest is invalid.');
      if (member.path === (this.#platform === 'linux' ? '.venv/bin/python' : 'python/bin/python3'))
        this.python = false;
      if (member.path === 'node/bin/node') this.node = false;
      if (member.path === 'server/executor/mac/entrypoint.ts') this.entrypoint = false;
    });
  }
  #count(): void {
    if (++this.#entries > ARCHIVE_MAX_ENTRIES) refuse('app archive exceeds member count limit');
  }
  #account(bytes: number): void {
    this.#metadataBytes += bytes;
    if (this.#metadataBytes > ARCHIVE_MAX_METADATA_BYTES)
      refuse('app archive exceeds metadata limit');
  }
  #inspect(entry: ReadEntry): ArchiveMember {
    if (
      !Number.isSafeInteger(entry.size) ||
      entry.size < 0 ||
      entry.size > ARCHIVE_MAX_MEMBER_BYTES
    )
      refuse('app archive exceeds member size limit');
    const member: ArchiveMember = {
      path: normalized(entry.path, this.purpose === 'mac-inspection'),
      type: entry.type,
      mode: entry.mode ?? 0,
      size: entry.size,
      mtime: entry.mtime,
      linkpath: entry.linkpath ?? '',
    };
    this.#account(Buffer.byteLength(member.path) + Buffer.byteLength(member.linkpath));
    this.#logicalSize += member.size;
    if (this.purpose === 'mac-inspection' && this.#logicalSize > MAX_MAC_RUNTIME_EXPANDED_BYTES)
      throw new MacArchiveInspectionError(
        'refusal',
        'The expanded Mac runtime exceeds its size limit.',
      );
    return member;
  }
  #entry(entry: ReadEntry): void {
    this.#count();
    const member = this.#inspect(entry);
    if (this.purpose === 'extraction') {
      if (!ordinary.has(member.type)) refuse('app archive contains unsupported entries');
      const mode = entry.header.mode ?? 0;
      if (!Number.isSafeInteger(mode) || mode < 0 || mode > 0o777)
        refuse('app archive contains special permission bits');
      if (member.mtime && !Number.isFinite(member.mtime.getTime()))
        refuse('app archive contains invalid modification time');
      if (['Directory', 'Link', 'SymbolicLink'].includes(member.type) && member.size)
        refuse('app archive contains a body on a directory or link');
      if ((!member.path && member.type !== 'Directory') || this.#names.has(member.path))
        refuse('app archive contains unsafe or duplicate paths');
      this.#names.add(member.path);
      this.members.push(member);
    } else {
      if (this.#platform === 'linux') this.#linux_entry(entry, member);
      if (member.path === 'mac-runtime.json') {
        if (
          this.#manifest ||
          !files.has(member.type) ||
          member.size > MAX_MAC_RUNTIME_MANIFEST_BYTES
        )
          throw new MacArchiveInspectionError('refusal', 'The Mac runtime manifest is invalid.');
        this.#manifest = true;
        const chunks: Buffer[] = [];
        entry.on('data', (bytes: Buffer) => chunks.push(bytes));
        entry.on('end', () => {
          this.manifest_bytes = Buffer.concat(chunks);
          decode_mac_manifest(this.manifest_bytes);
        });
      }
      if (member.path === (this.#platform === 'linux' ? '.venv/bin/python' : 'python/bin/python3'))
        this.python = files.has(member.type) || member.type === 'SymbolicLink';
      if (member.path === 'node/bin/node')
        this.node = files.has(member.type) && member.size > 0 && (member.mode & 0o111) !== 0;
      if (member.path === 'server/executor/mac/entrypoint.ts')
        this.entrypoint = files.has(member.type) && member.size > 0;
    }
    this.#on_entry?.(entry, member);
    entry.resume();
  }
  #linux_entry(entry: ReadEntry, member: ArchiveMember): void {
    const json = member.path === 'manifest.json' || member.path === 'runtime/contract.json';
    if (
      !json &&
      !['runtime/inventory.json', 'bin/panels', 'server/executor/mac/entrypoint.ts'].includes(
        member.path,
      )
    )
      return;
    if (this.#linux_files.has(member.path) || !files.has(member.type)) {
      this.#linux_invalid = 'The Linux runtime contains an invalid or duplicate required file.';
      return;
    }
    this.#linux_files.add(member.path);
    if (json) {
      if (member.size > MAX_MAC_RUNTIME_MANIFEST_BYTES) {
        this.#linux_invalid = 'The Linux runtime metadata exceeds its size limit.';
        return;
      }
      const chunks: Buffer[] = [];
      entry.on('data', (bytes: Buffer) => chunks.push(bytes));
      entry.on('end', () => this.#linux_json.set(member.path, Buffer.concat(chunks)));
    } else if (member.path === 'runtime/inventory.json') {
      const hash = createHash('sha256');
      entry.on('data', (bytes: Buffer) => hash.update(bytes));
      entry.on('end', () => {
        this.#inventory = hash.digest('hex');
      });
    } else if (member.path === 'bin/panels')
      this.#panels = member.size > 0 && (member.mode & 0o111) !== 0;
  }
  #finish_linux(): void {
    if (this.python) {
      const document = this.#linux_json.get('manifest.json');
      let declared: unknown;
      try {
        declared = document ? decode_mac_manifest(document) : null;
      } catch {
        // Source legacy Python archives ignore optional, undecodable application metadata.
      }
      if (
        !is_record(declared) ||
        !is_record(declared.runtime_contract) ||
        declared.runtime_contract.version !== 2
      )
        return;
    }
    if (this.#linux_invalid) throw new MacArchiveInspectionError('refusal', this.#linux_invalid);
    const marker = this.manifest_bytes === null ? null : decode_mac_manifest(this.manifest_bytes);
    if (
      !is_record(marker) ||
      marker.schema_version !== 1 ||
      typeof marker.application_revision !== 'string'
    )
      throw new MacArchiveInspectionError('refusal', 'The Linux runtime manifest is invalid.');
    const document = this.#linux_json.get('manifest.json'),
      payload = this.#linux_json.get('runtime/contract.json');
    if (!document || !payload || !this.#inventory || !this.#panels || !this.entrypoint)
      throw new MacArchiveInspectionError(
        'refusal',
        'The Linux runtime lacks its native Node metadata or executable.',
      );
    // Decode failures retain the existing strict JSON/encoding error categories.
    const app = decode_mac_manifest(document),
      actual = decode_mac_manifest(payload);
    try {
      require_runtime_target(
        {
          system: marker.platform,
          architecture: marker.architecture,
          role: marker.role,
        } as typeof LINUX_EXECUTOR,
        LINUX_EXECUTOR,
      );
      const identity = manifest_identity(app, marker.application_revision);
      const contract = parse_optional_runtime_contract(
        (app as Record<string, unknown>).runtime_contract,
      );
      if (contract?.version !== 2 || contract.system !== 'linux')
        throw new Error('Runtime requires a native Linux Node contract.');
      if (!isDeepStrictEqual(actual, contract))
        throw new Error('Runtime manifest contract differs from payload.');
      if (this.#inventory !== contract.inventory_sha256)
        throw new Error('Runtime inventory differs from contract.');
      this.native_linux = {
        version: 2,
        app_sha: identity.app_sha,
        inventory_sha256: contract.inventory_sha256,
        panels: this.#panels,
      };
    } catch (error) {
      if (!(error instanceof Error)) throw error;
      throw new MacArchiveInspectionError('refusal', error.message);
    }
  }
  feed(chunk: Buffer): void {
    if (!this.#started) {
      const prefix = Buffer.concat([this.#prefix, chunk]);
      if (prefix.length < 2) {
        this.#prefix = prefix;
        return;
      }
      chunk = prefix;
      // The caller already inflated and counted raw TAR bytes. Do not inflate twice.
      if (chunk[0] === 0x1f && chunk[1] === 0x8b) refuse('app archive contains nested gzip data');
      this.#started = true;
      this.#prefix = Buffer.alloc(0);
    }
    this.#parser.write(chunk);
  }
  finish(): void {
    this.#parser.end();
    if (this.purpose === 'mac-inspection') {
      if (this.#platform === 'linux') this.#finish_linux();
      return;
    }
    const entries = new Map(this.members.map((member) => [member.path, member]));
    const resolve = (path: string, base: string[] = [], active = new Set<string>()): string => {
      if (posix.isAbsolute(path) || path.includes('\0'))
        refuse('app archive contains an outside link');
      const current = [...base];
      for (const part of path.split('/')) {
        if (!part || part === '.') continue;
        if (part === '..') {
          if (!current.length) refuse('app archive contains an outside link');
          current.pop();
          continue;
        }
        current.push(part);
        if (current.length > ARCHIVE_MAX_DEPTH) refuse('app archive path exceeds depth limit');
        const name = current.join('/'),
          entry = entries.get(name);
        if (entry?.type !== 'Link' && entry?.type !== 'SymbolicLink') continue;
        if (active.has(name)) refuse('app archive contains a link cycle');
        if (active.size >= ARCHIVE_MAX_DEPTH) refuse('app archive link exceeds depth limit');
        active.add(name);
        const target = resolve(
          entry.linkpath,
          entry.type === 'SymbolicLink' ? current.slice(0, -1) : [],
          active,
        );
        active.delete(name);
        current.splice(0, current.length, ...target.split('/').filter(Boolean));
      }
      return current.join('/');
    };
    for (const entry of this.members) {
      let parent = posix.dirname(entry.path);
      while (parent !== '.' && parent !== '') {
        const ancestor = entries.get(parent);
        if (ancestor && ancestor.type !== 'Directory')
          refuse('app archive contains a member behind a link or file');
        parent = posix.dirname(parent);
      }
      if (entry.type === 'Link' || entry.type === 'SymbolicLink') {
        if (!entry.linkpath) refuse('app archive contains an outside link');
        const target = resolve(entry.path);
        if (!target) refuse('app archive contains an outside link');
        if (entry.type === 'Link') {
          const actual = entries.get(target);
          if (!actual || !files.has(actual.type))
            refuse('app archive contains an invalid hard link');
          entry.linkpath = target;
        }
      }
    }
  }
}
