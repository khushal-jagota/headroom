/**
 * The one scratch directory for tests that build a throwaway Svelte host and drive it in a
 * real browser.
 *
 * The hosts import `../src/...` and `../../assets/...`, and Vite resolves those against
 * `web/`. The system temp directory is therefore not an option: Vite rejects an entry that
 * sits outside its root. `web/.test-scratch/` is at the same depth as `web/tests/`, so the
 * import paths inside a host read the same as they did beside the test.
 *
 * A run that is killed never reaches its `finally` block, so leftovers are normal. The
 * directory is ignored, which keeps `git status` clean. Every run also sweeps the leftovers
 * of processes that are gone, which keeps the disk clean.
 */
import { mkdir, readdir, rm } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const scratchRoot = join(dirname(fileURLToPath(import.meta.url)), "..", "..", ".test-scratch");

/** Scratch names end in `-<pid>` before their extension. That suffix names the owner. */
const owner = /-(\d+)(?:\.[^.]+)?$/;

function ownerIsAlive(entryName) {
  const match = owner.exec(entryName);
  if (match === null) return true;
  const pid = Number(match[1]);
  if (pid === process.pid) return true;
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    // EPERM means the process is alive and owned by somebody else. ESRCH means it is gone.
    return error.code === "EPERM";
  }
}

/** Create the scratch directory, drop what dead runs left in it, and return its path. */
export async function scratchDirectory() {
  await mkdir(scratchRoot, { recursive: true });
  for (const entryName of await readdir(scratchRoot)) {
    if (ownerIsAlive(entryName)) continue;
    await rm(join(scratchRoot, entryName), { recursive: true, force: true });
  }
  return scratchRoot;
}
