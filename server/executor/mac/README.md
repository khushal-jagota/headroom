# Mac executor file foundation

The Mac executor uses `FilesService` for byte copies, hashes, directory creation, and durable flushes.
Each caller owns its service instance and must pass the shared absolute shutdown deadline to `stop()`.
The later host can retain separate instances for independent conversation transfers. This slice adds no worker pool.

The public methods return promises:

- `file_digest(path)` and `verified_version(path, sha256, byte_count)` read regular files.
- `copy_verified(source, target, sha256, byte_count)` creates an exclusive destination inode.
- `install_verified(source, target, sha256, byte_count)` publishes a verified temporary inode through atomic rename.
- `fsync_directory(path)` flushes a retained directory.
- `open_directory(path, {create})` returns an opaque capability owned by that service.
- `copy_verified_at` and `install_verified_at` take that capability as their second argument.
- `close_directory(capability)` releases it. Shutdown also releases every retained capability.

No caller receives a raw file descriptor. A capability from another service is refused.
Streaming transfers use `open_transfer`, bounded `read_transfer` or `write_transfer`,
`finish_transfer`, and `close_transfer`. The worker retains the opened inode and parent,
computes the digest, and performs the full flush. Upload reads accept the remaining byte limit.
An ordinary transfer/directory close failure seals admission and faults the host.
The worker retains failed close capabilities and stays alive for a later `stop()` retry.
Its actual join retains the first failure after successful cleanup.
The private synchronous implementation refuses import on the main thread.
The lightweight `safe_component`, `contained_path`, and `file_version` helpers perform validation or metadata observations only.
They do not hash or copy bytes.

One lazy worker processes admitted file operations in order.
An ordinary filesystem error rejects only its operation. Later operations remain usable.
Stop closes admission before its first wait and queues cleanup after previously admitted work.
It does not interrupt an active copy. A shutdown deadline failure does not imply that the worker stopped.
`settled()` waits for the actual worker exit. A stopped service cannot create another worker.
After exit, a retained fatal transport or worker cleanup failure rejects that actual join. Ordinary file-operation refusals do not.
Fatal JavaScript errors release retained directory capabilities through the worker's exit hooks.
A failed message send seals the service and requests the same cleanup. A failed stop send remains retryable.

The worker uses no inherited command-line flags. Supported Node 22.18+ and Node 24 execute its `.ts` entry directly.
This prevents a parent's eval, `--input-type`, or test-loader arguments from changing the file-backed worker entry.
It does not change the existing usage worker or its runtime policy.

## Native operations and containment

The Manager permits the branch's existing Koffi bridge only to OS libc or libSystem.
Every new native call resides in `platform.ts`. An absent library or symbol refuses the operation.
There is no path-based fallback.
The module refuses startup if `O_NOFOLLOW`, `O_DIRECTORY`, `O_CREAT`, `O_EXCL`, or `O_NONBLOCK` is absent or nonpositive.
`O_RDONLY` legitimately equals zero.

Directory walks retain each opened inode and use `openat` with `O_DIRECTORY`, `O_NOFOLLOW`, and close-on-exec.
Files use no-follow and nonblocking opens, followed by a regular-file check.
A FIFO therefore cannot block the worker before the file-type refusal.
Destination creation, removal, and rename use retained parents. Swapped ancestor symlinks cannot redirect those operations.
An ordinary hardlink source is allowed, as Python allows it. This code does not determine whether another hardlink exists outside the root.
Replacement of an ancestor with another real directory is not detected as an identity violation.
A retained capability continues to name its original inode if that directory moves.
This preserves Python's symlink defense. It is not a sandbox against arbitrary same-user changes.

`contained_path` is a preflight check. Later byte operations do not trust its result without descriptor-relative opens.
`file_version` keeps Python's metadata-only, path-based `lstat` observation.
Nanosecond file versions use BigInt rather than rounded JavaScript numbers.

The spool lock uses the same OS `flock` mechanism as the shared lock helper.
The shared helper opens its path directly and can follow a substituted parent or leaf.
It cannot supply a retained-parent, no-follow lock creation unchanged, so the executor reuses the native mechanism through its platform boundary.
The exposed executor lock modes are nonblocking exclusive acquisition and unlock. No blocking acquisition mode exists.

A regular-file flush calls plain libc `fsync`, then strict Darwin `fcntl(F_FULLFSYNC)` with an explicit variadic `int` zero.
Failure of the second call propagates. The code does not substitute libuv's full-flush fallback policy.
Post-rename publication calls plain directory `fsync`, as Python does.
Directory creation retains Python's stricter parent-flush sequence.

Normal failure cleanup removes each private `.tmp-` entry through its retained directory.
This file helper does not perform a startup sweep after process death.
Python's outbox startup calls `_sweep_blobs` at `mac_outbox.py:183-191` and defines it at `mac_outbox.py:542-560`.
The later storage slice must preserve that sweep for spool blobs.
That sweep does not cover temporary files under other artifact or update directories.
No blanket crash-cleanup claim applies to those directories.

## Native checks still required

Linux checks do not prove Darwin argument layout, filesystem behavior, signing, or the installed arm64 addon.
The separate native session must check each native boundary below with synthetic state and the new installation only.

| Call or boundary                                 | Required Mac check                                                                                                                                                        |
| ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `openat(int, const char *, int, ...)`            | Koffi variadic declaration passes the create mode as promoted `int`. Create a new file and verify mode 0600. Check no-follow and close-on-exec.                           |
| `mkdirat(int, const char *, mode_t)`             | Check fixed `mode_t` width: `uint16_t` on Darwin and 32-bit `unsigned int` on Linux. Create mode-0700 directories through retained parents and refuse symlink components. |
| `renameat(int, const char *, int, const char *)` | Swap a parent symlink before publication. Confirm that only the retained destination changes.                                                                             |
| `unlinkat(int, const char *, int)`               | Confirm failed-copy and temporary-file cleanup through a retained parent.                                                                                                 |
| `flock(int, int)`                                | A second descriptor refuses nonblocking acquisition while the first holds it, then succeeds after unlock.                                                                 |
| `fsync(int)`                                     | Confirm plain directory flush after rename and the parent flush after directory creation.                                                                                 |
| `fcntl(int, int, ...)` with `F_FULLFSYNC = 51`   | Confirm the variadic ABI, success on a regular file, and propagation of an injected failure without fallback.                                                             |
| `getpwnam(const char *)` and Darwin `passwd`     | Check named-user expansion with the native field layout, including `pw_change`, `pw_class`, `pw_expire`, and `pw_fields`.                                                 |
| Native flags                                     | Check Darwin values, including `O_NONBLOCK`, `O_DIRECTORY`, `O_NOFOLLOW`, and `O_CLOEXEC`.                                                                                |
| Packaged Koffi                                   | Verify that the package contains and loads its arm64 Darwin addon, with no external native library beyond the OS library.                                                 |

The existing `server/skills/sources.ts` declaration remains outside this new platform module.
Its older fixed `openat` declaration does not create regular files here. This slice does not claim to repair that separate publisher.
The later skill-publication slice must retain or address its own native requirements explicitly.

The TypeScript default state directory is `~/Library/Application Support/Panels/executor-typescript`.
The Python directory remains `~/Library/Application Support/Panels/executor`.
The Manager requested this default-root difference. Explicit configuration refuses the Python root and its descendants, including symlink aliases.
The new app, LaunchAgent, package, provider homes, and real-Mac installation remain later slices.
