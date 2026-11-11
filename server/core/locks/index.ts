import { closeSync, mkdirSync, openSync, realpathSync } from "node:fs";
import { dirname, resolve } from "node:path";
import koffi from "koffi";
const libc = koffi.load(process.platform === "darwin" ? "/usr/lib/libSystem.B.dylib" : "libc.so.6");
const flock = libc.func("int flock(int fd, int operation)");
export interface LockHandle {
    close(): void;
}
/** Descriptors from Node's openSync are close-on-exec. Never unlink held lock files. */
export function acquireLock(path: string): LockHandle | null {
    mkdirSync(dirname(path), { recursive: true });
    const fd = openSync(path, "a+", 0o644);
    if (flock(fd, 2 | 4) !== 0) {
        const errno = koffi.errno();
        closeSync(fd);
        if (errno === 11 || errno === 35)
            return null;
        throw new Error(`flock failed: errno ${errno}`);
    }
    let closed = false;
    return {
        close() {
            if (closed) return;
            closed = true;
            try {
                flock(fd, 8);
            } finally {
                closeSync(fd);
            }
        },
    };
}
export function acquireRuntimeOwnership(dbPath: string): LockHandle {
    const canonical = realpathSync(resolve(dbPath));
    const handle = acquireLock(`${canonical}.conversation-owner.lock`);
    if (!handle)
        throw new Error(`ConversationRuntimeAlreadyOwned: ${canonical}`);
    return handle;
}
