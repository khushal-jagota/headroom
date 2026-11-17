/** Adapted from T3 Code 75e4ceb96489a2fe92c710c79dbea59778cccfd1.
 * Copyright (c) 2026 T3 Tools Inc. MIT permission notice: ./T3-LICENSE.
 */
/**
 * Streaming filesystem access stays separate from pricing and aggregation.
 * Each scan reports a byte position so a later scan can parse only appended
 * bytes. The reader retains a provisional tail and a bounded resume guard.
 */
import * as NodeFSP from 'node:fs/promises';
import type { Dirent } from 'node:fs';

import type { LineTranscriptParser } from '../../drivers/core/usage.ts';
import { TRANSCRIPT_CHUNK_BYTES } from './configuration.ts';

import type { UsageRecord } from './usageTranscripts.ts';

/** A file that vanished is an expected scan race. Other filesystem errors are logged. */
function missing(error: unknown): boolean {
  return (error as NodeJS.ErrnoException | null)?.code === 'ENOENT';
}

export interface TranscriptFile {
  readonly path: string;
  readonly size: number;
  readonly mtimeMs: number;
}

/**
 * Where a parse stopped, with enough state to continue from there.
 *
 * The guard hash fingerprints the bytes immediately before `resumeOffset`. A
 * resume only proceeds when those bytes still match: transcripts are
 * append-only by design, but a rotated or rewritten file silently mis-parsed
 * from the middle would corrupt usage totals. The window is a cheap tripwire
 * for those realistic failure shapes, all of which disturb the file's tail at
 * that exact offset; it deliberately does not hash the whole prefix, which
 * would cost the full re-read the resume exists to avoid.
 */
export interface TranscriptParsePosition {
  /** Byte offset just past the last newline-terminated line consumed. */
  readonly resumeOffset: number;
  /** Length of the fingerprinted window ending at `resumeOffset`. */
  readonly guardLength: number;
  /** FNV-1a hash of that window. */
  readonly guardHash: number;
  /** The parser's state as of `resumeOffset`; `null` for a stateless parser. */
  readonly state: unknown;
}

export interface TranscriptParseResult {
  /** Records from newline-terminated lines at or after the parse start. */
  readonly records: readonly UsageRecord[];
  /**
   * Records from a trailing segment the writer has not newline-terminated yet.
   * Kept out of `records` because `position` deliberately excludes that
   * segment: the next scan re-reads it once the writer finishes the line.
   */
  readonly tailRecords: readonly UsageRecord[];
  readonly position: TranscriptParsePosition;
  /** Whether the parse continued from `resumeFrom` rather than byte 0. */
  readonly resumed: boolean;
}

/** 64 bytes of JSONL tail is ample to distinguish a replaced file. */
export const GUARD_LENGTH = 64;
const NEWLINE = 0x0a;
const CARRIAGE_RETURN = 0x0d;

function fnv1a(buffer: Buffer): number {
  let hash = 0x811c9dc5;
  for (let index = 0; index < buffer.length; index += 1) {
    hash ^= buffer[index]!;
    hash = Math.imul(hash, 0x01000193);
  }
  return hash >>> 0;
}

/**
 * Lists `.jsonl` transcripts under `root` last modified at or after `sinceMs`,
 * or every file when `everyFile` is set.
 *
 * Errors on individual entries are swallowed: session files rotate and get
 * removed while the walk is in flight, and a partial listing is far better than
 * failing the page.
 *
 * `fileName` restricts the walk to a single basename (Grok's `updates.jsonl`).
 * Grok sessions also ship multi-megabyte `chat_history` and `events` logs that
 * never carry usage, so the basename filter keeps a cold scan off those files.
 */
export async function listTranscriptFiles(
  root: string,
  sinceMs: number,
  options?: { readonly fileName?: string; readonly everyFile?: boolean },
): Promise<readonly TranscriptFile[]> {
  const found: TranscriptFile[] = [];
  const fileName = options?.fileName;
  const everyFile = options?.everyFile ?? false;

  const walk = async (dir: string): Promise<void> => {
    let entries: Dirent[];
    try {
      entries = [];
      const opened = await NodeFSP.opendir(dir);
      // opendir retains native scandir order. readdir sorts names in libuv.
      for await (const entry of opened) entries.push(entry);
    } catch (error) {
      if (!missing(error))
        console.warn(`usage transcript directory ${dir} could not be read`, error);
      return;
    }
    for (const entry of entries) {
      // Retain '..' until the filesystem follows symlinks.
      const child = dir.endsWith('/') ? dir + entry.name : dir + '/' + entry.name;
      if (entry.isDirectory()) {
        await walk(child);
        continue;
      }
      if (fileName !== undefined) {
        if (entry.name !== fileName) continue;
      } else if (!everyFile && !entry.name.endsWith('.jsonl')) {
        continue;
      }
      try {
        const stats = await NodeFSP.stat(child, { bigint: true });
        // Divide integer nanoseconds before conversion to preserve sub-millisecond precision.
        const mtimeMs =
          Number(stats.mtimeNs / 1_000_000n) + Number(stats.mtimeNs % 1_000_000n) / 1_000_000;
        if (mtimeMs >= sinceMs) {
          found.push({ path: child, size: Number(stats.size), mtimeMs });
        }
      } catch (error) {
        // Vanished between readdir and stat.
        if (!missing(error)) console.warn(`usage transcript ${child} could not be stat'd`, error);
      }
    }
  };

  await walk(root);
  return found;
}

/**
 * Filesystem identity of a directory, as `device:inode`.
 *
 * Used to tell "two servers reading the same transcript directory" apart from
 * "two machines whose hostname and home path happen to match". Returns an empty
 * string when the directory cannot be stat'd.
 */
export async function readDirectoryVolumeId(path: string): Promise<string> {
  try {
    const stats = await NodeFSP.stat(path, { bigint: true });
    return `${stats.dev}:${stats.ino}`;
  } catch (error) {
    if (!missing(error))
      console.warn(`usage transcript directory ${path} could not be stat'd`, error);
    return '';
  }
}

async function guardMatches(
  handle: NodeFSP.FileHandle,
  position: TranscriptParsePosition,
): Promise<boolean> {
  if (position.guardLength <= 0 || position.guardLength > GUARD_LENGTH) return false;
  try {
    const window = Buffer.alloc(position.guardLength);
    const { bytesRead } = await handle.read(
      window,
      0,
      position.guardLength,
      position.resumeOffset - position.guardLength,
    );
    return bytesRead === position.guardLength && fnv1a(window) === position.guardHash;
  } catch (error) {
    // The transcript is read again from the start.
    console.warn('usage transcript resume guard could not be read', error);
    return false;
  }
}

/**
 * Streams one transcript and returns the usage records it contains, or `null`
 * when the file could not be read.
 *
 * The distinction matters to the caller's cache: a genuinely empty transcript
 * is a stable fact worth memoising, while a transient read failure memoised
 * under the same `(size, mtime)` key would silently drop that file's usage
 * until the file next changes.
 *
 * With `resumeFrom`, parsing continues from that position when its guard bytes
 * still match, so only appended lines are read; otherwise the whole file is
 * re-parsed from the start and `resumed` reports `false`.
 *
 * A resume also needs the parser to revive the position's state. A state it
 * cannot revive is not its own, or is corrupt, and the file is read again.
 *
 * Every line the parser's `might_carry_usage` admits passes through it, so a
 * parser that carries state on lines without usage admits those lines too.
 */
export async function readTranscriptRecords(
  filePath: string,
  parser: LineTranscriptParser<unknown>,
  resumeFrom?: TranscriptParsePosition,
): Promise<TranscriptParseResult | null> {
  let handle: NodeFSP.FileHandle;
  try {
    handle = await NodeFSP.open(filePath, 'r');
  } catch (error) {
    if (!missing(error)) console.warn(`usage transcript ${filePath} could not be opened`, error);
    return null;
  }

  try {
    let state = parser.initial_state();
    let resumed = false;
    let start = 0;
    const revived =
      resumeFrom !== undefined && resumeFrom.resumeOffset > 0
        ? parser.revive_state(resumeFrom.state)
        : undefined;
    if (revived !== undefined && (await guardMatches(handle, resumeFrom!))) {
      state = structuredClone(revived);
      start = resumeFrom!.resumeOffset;
      resumed = true;
    }

    const parseLine = (line: string, lineState: unknown, out: UsageRecord[]): void => {
      if (!parser.might_carry_usage(line)) return;
      for (const record of parser.parse_line(line, lineState)) out.push(record);
    };

    const toLineString = (lineBuffer: Buffer): string => {
      const content =
        lineBuffer.length > 0 && lineBuffer[lineBuffer.length - 1] === CARRIAGE_RETURN
          ? lineBuffer.subarray(0, -1)
          : lineBuffer;
      return content.toString('utf8');
    };

    const records: UsageRecord[] = [];
    // Buffer-level line splitting rather than `readline`, because resuming
    // needs byte-exact offsets and decoded strings cannot provide them.
    // Newline-free chunks are collected rather than concatenated as they
    // arrive, so a single huge line costs one copy instead of one per chunk.
    let resumeOffset = start;
    let pendingChunks: Buffer[] = [];
    const stream = handle.createReadStream({
      start,
      autoClose: false,
      highWaterMark: TRANSCRIPT_CHUNK_BYTES,
    }) as AsyncIterable<Buffer>;
    for await (const chunk of stream) {
      if (!chunk.includes(NEWLINE)) {
        pendingChunks.push(chunk);
        continue;
      }
      const buffer: Buffer =
        pendingChunks.length === 0 ? chunk : Buffer.concat([...pendingChunks, chunk]);
      pendingChunks = [];
      let lineStart = 0;
      for (;;) {
        const newlineIndex = buffer.indexOf(NEWLINE, lineStart);
        if (newlineIndex === -1) break;
        parseLine(toLineString(buffer.subarray(lineStart, newlineIndex)), state, records);
        lineStart = newlineIndex + 1;
      }
      resumeOffset += lineStart;
      if (lineStart < buffer.length) pendingChunks.push(buffer.subarray(lineStart));
    }

    // A trailing segment without its newline is parsed for this result but not
    // consumed: a writer may still be appending to it, and counting a half
    // record now and its full form later would double count.
    const tailRecords: UsageRecord[] = [];
    if (pendingChunks.length > 0) {
      const pending = pendingChunks.length === 1 ? pendingChunks[0]! : Buffer.concat(pendingChunks);
      if (pending.length > 0) parseLine(toLineString(pending), structuredClone(state), tailRecords);
    }

    const guardLength = Math.min(GUARD_LENGTH, resumeOffset);
    let guardHash = 0;
    if (guardLength > 0) {
      const window = Buffer.alloc(guardLength);
      await handle.read(window, 0, guardLength, resumeOffset - guardLength);
      guardHash = fnv1a(window);
    }

    return {
      records,
      tailRecords,
      position: {
        resumeOffset,
        guardLength,
        guardHash,
        state,
      },
      resumed,
    };
  } catch (error) {
    console.warn(`usage transcript ${filePath} could not be read`, error);
    return null;
  } finally {
    await handle
      .close()
      .catch((error: unknown) =>
        console.warn(`usage transcript ${filePath} could not be closed`, error),
      );
  }
}
