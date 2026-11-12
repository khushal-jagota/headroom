/** Serialize Manager lifecycle operations across asynchronous conversation calls. */
class LifecycleLock {
    private tail: Promise<void> = Promise.resolve();
    async run<T>(operation: () => Promise<T>): Promise<T> {
        const previous = this.tail;
        let release!: () => void;
        this.tail = new Promise<void>(resolve => {
            release = resolve;
        });
        await previous;
        try {
            return await operation();
        }
        finally {
            release();
        }
    }
}
const locks = new Map<string, LifecycleLock>();
export function manager_lifecycle_lock(id: string): LifecycleLock {
    let lock = locks.get(id);
    if (!lock) {
        lock = new LifecycleLock();
        locks.set(id, lock);
    }
    return lock;
}
