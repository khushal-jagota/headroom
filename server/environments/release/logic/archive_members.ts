/** One bounded interpretation of TAR headers, extensions, and extraction ranges. */
import { posix } from 'node:path';
import { AppValidationError, MacArchiveInspectionError } from '../contracts.ts';
import { decode_mac_manifest } from '../../updates/mac/manifest.ts';
import {
  MAX_MAC_RUNTIME_EXPANDED_BYTES,
  MAX_MAC_RUNTIME_MANIFEST_BYTES,
} from '../../updates/mac/configuration.ts';
import {
  ARCHIVE_MAX_HEADERS,
  ARCHIVE_MAX_METADATA_BYTES,
  ARCHIVE_MAX_METADATA_ENTRY_BYTES,
  ARCHIVE_MAX_MEMBER_BYTES,
  ARCHIVE_MAX_DEPTH,
  ARCHIVE_MAX_END_PADDING_BYTES,
} from '../configuration.ts';
export interface ArchiveMember {
  path: string;
  type: string;
  mode: number;
  size: number;
  mtime: number;
  linkpath: string;
  offset: number;
}
const text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true });
const keys = new Set([
  'path',
  'linkpath',
  'size',
  'mtime',
  'uid',
  'gid',
  'uname',
  'gname',
  'atime',
  'ctime',
  'charset',
  'hdrcharset',
  'comment',
  'mode',
  'SCHILY.dev',
  'SCHILY.ino',
  'SCHILY.nlink',
]);
const metadata = new Set(['x', 'X', 'g', 'L', 'K']);
const ordinary = new Set(['0', '\0', '7', '5', '1', '2']);
function refuse(message: string): never {
  throw new AppValidationError(message);
}
function string(bytes: Uint8Array): string {
  const end = bytes.indexOf(0);
  return text.decode(end < 0 ? bytes : bytes.subarray(0, end));
}
function numeric(bytes: Uint8Array): number {
  let value = 0n;
  if (bytes[0] === 0x80 || bytes[0] === 0xff) {
    for (const byte of bytes.subarray(1)) value = value * 256n + BigInt(byte);
    if (bytes[0] === 0xff) value -= 1n << BigInt((bytes.length - 1) * 8);
  } else {
    const raw = string(bytes).trim();
    if (raw && !/^[+-]?[0-7]+$/.test(raw)) refuse('app archive contains an invalid numeric header');
    for (const digit of raw.replace(/^[+-]/, '')) value = value * 8n + BigInt(digit);
    if (raw.startsWith('-')) value = -value;
  }
  if (value > BigInt(Number.MAX_SAFE_INTEGER) || value < BigInt(Number.MIN_SAFE_INTEGER))
    refuse('app archive contains an out-of-range numeric header');
  return Number(value);
}
function header(block: Buffer, offset: number): ArchiveMember {
  let unsigned = 0,
    signed = 0;
  for (let i = 0; i < 512; i++) {
    const byte = i >= 148 && i < 156 ? 32 : block[i]!;
    unsigned += byte;
    signed += byte > 127 ? byte - 256 : byte;
  }
  const checksum = numeric(block.subarray(148, 156));
  if (checksum !== unsigned && checksum !== signed) refuse('app archive header checksum differs');
  const magic = block.subarray(257, 265).toString('latin1');
  if (magic !== '\0'.repeat(8) && magic !== 'ustar\0' + '00' && magic !== 'ustar  \0')
    refuse('app archive contains an unsupported header layout');
  let path = string(block.subarray(0, 100));
  if (magic === 'ustar\0' + '00') {
    const prefix = string(block.subarray(345, 500));
    if (prefix) path = prefix + '/' + path;
  }
  let type = String.fromCharCode(block[156]!);
  if ((type === '0' || type === '\0') && path.endsWith('/')) type = '5';
  return {
    path,
    type,
    mode: numeric(block.subarray(100, 108)),
    size: numeric(block.subarray(124, 136)),
    mtime: numeric(block.subarray(136, 148)),
    linkpath: string(block.subarray(157, 257)),
    offset,
  };
}
function pax(bytes: Buffer): Record<string, string> {
  const result: Record<string, string> = Object.create(null);
  let start = 0;
  while (start < bytes.length) {
    const space = bytes.indexOf(32, start);
    if (space < 0) refuse('app archive contains invalid PAX metadata');
    const raw = text.decode(bytes.subarray(start, space));
    if (!/^[0-9]+$/.test(raw)) refuse('app archive contains invalid PAX metadata');
    const length = Number(raw),
      end = start + length;
    if (!Number.isSafeInteger(length) || length < 5 || end > bytes.length || bytes[end - 1] !== 10)
      refuse('app archive contains invalid PAX metadata');
    const equals = bytes.indexOf(61, space + 1);
    if (equals <= space + 1 || equals >= end - 1)
      refuse('app archive contains invalid PAX metadata');
    const key = text.decode(bytes.subarray(space + 1, equals)),
      value = text.decode(bytes.subarray(equals + 1, end - 1));
    if (key.startsWith('GNU.sparse.')) refuse('app archive contains GNU sparse metadata');
    if (!keys.has(key)) refuse(`app archive contains unsupported PAX key: ${key}`);
    if (key === 'hdrcharset' && value !== 'ISO-IR 10646 2000 UTF-8')
      refuse('app archive contains an unsupported path encoding');
    if (value.includes('\0')) refuse('app archive contains invalid PAX metadata');
    result[key] = value;
    start = end;
  }
  return result;
}
/** tarfile catches float(ValueError) and applies zero, rather than a JS radix literal. */
function pax_mtime(raw: string): number {
  const value = raw
    .replace(/\p{Decimal_Number}/gu, (character) => {
      const point = character.codePointAt(0)!;
      let first = point;
      while (first > 0 && /\p{Decimal_Number}/u.test(String.fromCodePoint(first - 1))) first--;
      return String((point - first) % 10);
    })
    .replace(/^\p{White_Space}+|\p{White_Space}+$/gu, '');
  if (
    !/^[+-]?(?:(?:\d(?:_?\d)*(?:\.(?:\d(?:_?\d)*)?)?|\.\d(?:_?\d)*)(?:e[+-]?\d(?:_?\d)*)?|inf(?:inity)?|nan)$/i.test(
      value,
    )
  )
    return 0;
  if (/^[+-]?inf(?:inity)?$/i.test(value)) return value.startsWith('-') ? -Infinity : Infinity;
  return Number(value.replaceAll('_', ''));
}
function normalized(path: string, inspection = false): string {
  const parts = path.split('/');
  if (inspection && (posix.isAbsolute(path) || parts.includes('..')))
    throw new MacArchiveInspectionError('refusal', 'The Mac runtime contains an invalid path.');
  if (posix.isAbsolute(path) || path.includes('\0') || parts.includes('..'))
    refuse('app archive contains unsafe or duplicate paths');
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
  #manifest: unknown = null;
  #capture: { bytes: Buffer; used: number } | undefined;
  #logicalSize = 0;
  readonly purpose: 'extraction' | 'mac-inspection';
  constructor(purpose: 'extraction' | 'mac-inspection' = 'extraction') {
    this.purpose = purpose;
  }
  #position = 0;
  #headers = 0;
  #metadataBytes = 0;
  #end = 0;
  #left = 0;
  #block = Buffer.alloc(512);
  #used = 0;
  #meta: { header: ArchiveMember; bytes: Buffer; used: number } | undefined;
  #global: Record<string, string> = Object.create(null);
  #frames: Record<string, string>[] = [];
  #names = new Set<string>();
  #account(bytes: number): void {
    this.#metadataBytes += bytes;
    if (this.#metadataBytes > ARCHIVE_MAX_METADATA_BYTES)
      refuse('app archive exceeds metadata limit');
  }
  #extension(entry: ArchiveMember, bytes: Buffer): void {
    if (entry.type === 'g') {
      const values = pax(bytes);
      // Proposed Mac-only subset. Application/source export policy is unchanged.
      if (this.purpose === 'mac-inspection' && Object.hasOwn(values, 'size'))
        refuse('Mac inspection does not support global PAX size');
      Object.assign(this.#global, values);
    } else
      this.#frames.push(
        entry.type === 'L'
          ? { path: string(bytes) }
          : entry.type === 'K'
            ? { linkpath: string(bytes) }
            : { ...this.#global, ...pax(bytes) },
      );
  }
  feed(chunk: Buffer): void {
    let cursor = 0;
    while (cursor < chunk.length) {
      if (this.#end) {
        const tail = chunk.subarray(cursor);
        if (tail.some((byte) => byte !== 0)) refuse('app archive contains bytes after TAR end');
        this.#end += tail.length;
        this.#position += tail.length;
        if (this.#end > ARCHIVE_MAX_END_PADDING_BYTES)
          refuse('app archive exceeds end-padding limit');
        return;
      }
      if (this.#left) {
        const count = Math.min(this.#left, chunk.length - cursor);
        if (this.#capture) {
          const copy = Math.min(count, this.#capture.bytes.length - this.#capture.used);
          chunk.copy(this.#capture.bytes, this.#capture.used, cursor, cursor + copy);
          this.#capture.used += copy;
          if (this.#capture.used === this.#capture.bytes.length) {
            this.#manifest = decode_mac_manifest(this.#capture.bytes);
            this.manifest_bytes = this.#capture.bytes;
            this.#capture = undefined;
          }
        }
        if (this.#meta) {
          const copy = Math.min(count, this.#meta.bytes.length - this.#meta.used);
          chunk.copy(this.#meta.bytes, this.#meta.used, cursor, cursor + copy);
          this.#meta.used += copy;
        }
        this.#left -= count;
        cursor += count;
        this.#position += count;
        if (!this.#left && this.#meta) {
          const { header: entry, bytes } = this.#meta;
          this.#meta = undefined;
          this.#extension(entry, bytes);
        }
        continue;
      }
      const count = Math.min(512 - this.#used, chunk.length - cursor);
      chunk.copy(this.#block, this.#used, cursor, cursor + count);
      this.#used += count;
      cursor += count;
      this.#position += count;
      if (this.#used !== 512) continue;
      this.#used = 0;
      if (this.#block.every((byte) => byte === 0)) {
        this.#end = 512;
        continue;
      }
      if (++this.#headers > ARCHIVE_MAX_HEADERS) refuse('app archive exceeds member count limit');
      const entry = header(this.#block, this.#position);
      if (entry.type === 'S') refuse('app archive contains GNU sparse entries');
      const inspection = this.purpose === 'mac-inspection';
      if (!inspection && !ordinary.has(entry.type) && !metadata.has(entry.type))
        refuse('app archive contains unsupported entries');
      if (!inspection && (entry.mode < 0 || entry.mode > 0o777))
        refuse('app archive contains special permission bits');
      if (entry.size < 0 || entry.size > ARCHIVE_MAX_MEMBER_BYTES)
        refuse('app archive exceeds member size limit');
      if (metadata.has(entry.type)) {
        if (entry.size > ARCHIVE_MAX_METADATA_ENTRY_BYTES)
          refuse('app archive exceeds metadata header limit');
        this.#account(entry.size);
        if (entry.size) this.#meta = { header: entry, bytes: Buffer.alloc(entry.size), used: 0 };
        else this.#extension(entry, Buffer.alloc(0));
      } else {
        const overrides = { ...this.#global };
        for (let i = this.#frames.length - 1; i >= 0; i--)
          Object.assign(overrides, this.#frames[i]);
        this.#frames = [];
        entry.path = overrides.path ?? entry.path;
        entry.linkpath = overrides.linkpath ?? entry.linkpath;
        if (overrides.size !== undefined) {
          if (!/^\+?[0-9]+$/.test(overrides.size)) refuse('app archive contains invalid PAX size');
          const digits = overrides.size.replace(/^\+?0*/, '') || '0';
          if (digits.length > 10 || Number(digits) > ARCHIVE_MAX_MEMBER_BYTES)
            refuse('app archive exceeds member size limit');
          entry.size = Number(digits);
        }
        if (overrides.mtime !== undefined) entry.mtime = pax_mtime(overrides.mtime);
        if (!inspection && !Number.isFinite(entry.mtime))
          refuse('app archive contains invalid modification time');
        if (inspection) {
          entry.path = normalized(entry.path, true);
          this.#account(Buffer.byteLength(entry.path) + Buffer.byteLength(entry.linkpath));
          this.#logicalSize += entry.size;
          if (this.#logicalSize > MAX_MAC_RUNTIME_EXPANDED_BYTES)
            throw new MacArchiveInspectionError(
              'refusal',
              'The expanded Mac runtime exceeds its size limit.',
            );
          if (entry.path === 'mac-runtime.json') {
            if (
              this.#manifest !== null ||
              !['0', '\0', '7'].includes(entry.type) ||
              entry.size > MAX_MAC_RUNTIME_MANIFEST_BYTES
            )
              throw new MacArchiveInspectionError(
                'refusal',
                'The Mac runtime manifest is invalid.',
              );
            this.#capture = { bytes: Buffer.alloc(entry.size), used: 0 };
            if (!entry.size) decode_mac_manifest(this.#capture.bytes);
          }
          if (entry.path === 'python/bin/python3')
            this.python = ['0', '\0', '7', '2'].includes(entry.type);
          // Node packages require real payload members. Inspection never follows links.
          if (entry.path === 'node/bin/node')
            this.node =
              ['0', '\0', '7'].includes(entry.type) && entry.size > 0 && (entry.mode & 0o111) !== 0;
          if (entry.path === 'server/executor/mac/entrypoint.ts')
            this.entrypoint = ['0', '\0', '7'].includes(entry.type) && entry.size > 0;
          // tarfile._proc_builtin skips known nonregular bodies, even with a declared size.
          this.#left = ['1', '2', '3', '4', '5', '6'].includes(entry.type)
            ? 0
            : Math.ceil(entry.size / 512) * 512;
          continue;
        }
        if (
          ['5', '1', '2'].includes(entry.type) &&
          (entry.size || numeric(this.#block.subarray(124, 136)))
        )
          refuse('app archive contains a body on a directory or link');
        entry.path = normalized(entry.path);
        if ((!entry.path && entry.type !== '5') || this.#names.has(entry.path))
          refuse('app archive contains unsafe or duplicate paths');
        this.#names.add(entry.path);
        this.#account(Buffer.byteLength(entry.path) + Buffer.byteLength(entry.linkpath));
        this.members.push(entry);
      }
      this.#left = Math.ceil(entry.size / 512) * 512;
    }
  }
  finish(): void {
    if (this.#left || this.#used || this.#frames.length || this.#end < 1024 || this.#end % 512)
      refuse('app archive contains truncated TAR data');
    if (this.purpose === 'mac-inspection') return;
    const entries = new Map(this.members.map((member) => [member.path, member]));
    const resolve = (path: string, base: string[] = [], active = new Set<string>()): string => {
      if (posix.isAbsolute(path)) refuse('app archive contains an outside link');
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
        if (entry?.type !== '1' && entry?.type !== '2') continue;
        if (active.has(name)) refuse('app archive contains a link cycle');
        if (active.size >= ARCHIVE_MAX_DEPTH) refuse('app archive link exceeds depth limit');
        active.add(name);
        const target = resolve(
          entry.linkpath,
          entry.type === '2' ? current.slice(0, -1) : [],
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
        if (ancestor && ancestor.type !== '5')
          refuse('app archive contains a member behind a link or file');
        parent = posix.dirname(parent);
      }
      if (entry.type === '1' || entry.type === '2') {
        if (!entry.linkpath) refuse('app archive contains an outside link');
        const target = resolve(entry.path);
        if (!target) refuse('app archive contains an outside link');
        if (entry.type === '1') {
          const actual = entries.get(target);
          if (!actual || !['0', '\0', '7'].includes(actual.type))
            refuse('app archive contains an invalid hard link');
          entry.linkpath = target;
        }
      }
    }
  }
}
