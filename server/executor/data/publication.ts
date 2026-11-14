/** Prepared holds keep overwritten inode destruction outside the SQLite writer. */
import { randomUUID } from 'node:crypto';
import { PlannerError } from '../../core/contracts.ts';
import type { HeldFileLocation } from '../contracts.ts';
import type { FileVersion } from '../files.ts';
import { FileCleanupError, file_version_at, fsync_directory, link_at, rename_at_sync, unlink_at } from '../files.ts';
import { same_version } from '../mac/file_metadata.ts';

export function artifact_changed(): PlannerError {
  return new PlannerError(
    'retry_later',
    'An artifact changed while this item was saved; send it again',
  );
}
export interface PublicationEntry {
  readonly source: HeldFileLocation;
  readonly target: HeldFileLocation;
  readonly version: FileVersion;
}
interface HeldDestination {
  readonly entry: PublicationEntry;
  readonly hold: string | null;
  readonly destination: FileVersion | null;
}
/** Parents are borrowed. Their owner closes them only after this group's cleanup settles. */
export class PublicationGroup {
  private readonly held: HeldDestination[] = [];
  private readonly changed = new Set<PublicationEntry['source']['parent']>();
  private cleanup: Promise<void> | null = null;

  static async prepare(entries: readonly PublicationEntry[]): Promise<PublicationGroup> {
    const group = new PublicationGroup();
    try {
      for (const entry of entries) {
        const existing = file_version_at(entry.target.parent, entry.target.leaf);
        if (existing === null) {
          group.held.push({ entry, hold: null, destination: null });
          continue;
        }
        const hold = '.hold-' + randomUUID().replaceAll('-', '');
        await link_at(entry.target.parent, entry.target.leaf, hold);
        // Register cleanup immediately, including if the next metadata check fails.
        group.held.push({ entry, hold, destination: existing });
        group.changed.add(entry.target.parent);
        const pinned = file_version_at(entry.target.parent, hold);
        if (pinned === null || pinned[0] !== existing[0] || pinned[1] !== existing[1])
          throw artifact_changed();
      }
      return group;
    } catch (error) {
      try {
        await group.close();
      } catch (cleanup) {
        throw new FileCleanupError(
          [error, cleanup],
          cleanup instanceof FileCleanupError ? cleanup.cleanup_errors : [cleanup],
        );
      }
      throw error;
    }
  }
  /** The caller performs authority checks first, inside its synchronous writer callback. */
  publish(): readonly FileVersion[] {
    const installed: FileVersion[] = [];
    for (const { entry, destination } of this.held) {
      if (!same_version(file_version_at(entry.source.parent, entry.source.leaf), entry.version))
        throw artifact_changed();
      const current = file_version_at(entry.target.parent, entry.target.leaf);
      if (
        destination === null
          ? current !== null
          : current === null || current[0] !== destination[0] || current[1] !== destination[1]
      )
        throw artifact_changed();
      rename_at_sync(
        entry.source.parent,
        entry.source.leaf,
        entry.target.parent,
        entry.target.leaf,
      );
      this.changed.add(entry.source.parent);
      this.changed.add(entry.target.parent);
      const observed = file_version_at(entry.target.parent, entry.target.leaf);
      // Rename changes ctime, but preserves device, inode, size and mtime.
      if (
        observed === null ||
        entry.version.slice(0, 4).some((value, index) => value !== observed[index])
      )
        throw artifact_changed();
      installed.push(observed);
    }
    return installed;
  }
  close(): Promise<void> {
    return (this.cleanup ??= this.cleanup_holds());
  }
  private async cleanup_holds(): Promise<void> {
    const errors: unknown[] = [];
    for (const { entry, hold } of this.held) {
      if (hold === null) continue;
      try {
        await unlink_at(entry.target.parent, hold);
      } catch (error) {
        errors.push(error);
      }
    }
    for (const parent of this.changed) {
      try {
        await fsync_directory(parent);
      } catch (error) {
        errors.push(error);
      }
    }
    if (errors.length) throw new FileCleanupError(errors, errors);
  }
}
