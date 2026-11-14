/** Private thread messages. These are not executor wire or file-manifest types. */
import type { FileVersion } from './file_metadata.ts';
export interface DirectoryHandle {
  readonly id: number;
}
export interface TransferHandle {
  readonly id: number;
}
/** Local immutable-blob result, without wire scope, owner, or manifest fields. */
export interface CapturedBlob {
  sha256: string;
  byte_count: bigint;
  modified_ns: bigint;
}
export interface FileOperationArguments {
  open_transfer: [path: string, mode: 'read' | 'write'];
  read_transfer: [handle: number, limit: number];
  write_transfer: [handle: number, bytes: Uint8Array];
  finish_transfer: [handle: number, sha256: string, byte_count: bigint];
  close_transfer: [handle: number, discard?: boolean];
  capture_file: [source: string, blobs: number, checked_ns: bigint];
  sweep_directory: [directory: number, path: string, keep: readonly string[]];
  file_digest: [path: string];
  verified_version: [path: string, sha256: string, byte_count: number];
  copy_verified: [source: string, target: string, sha256: string, byte_count: number];
  install_verified: [source: string, target: string, sha256: string, byte_count: number];
  fsync_directory: [path: string];
  open_directory: [path: string, options: { create?: boolean }];
  close_directory: [handle: number];
  copy_verified_at: [
    source: string,
    handle: number,
    name: string,
    sha256: string,
    byte_count: number,
  ];
  install_verified_at: [
    source: string,
    handle: number,
    name: string,
    sha256: string,
    byte_count: number,
  ];
}
export interface FileOperationResults {
  open_transfer: number;
  read_transfer: Uint8Array;
  write_transfer: void;
  finish_transfer: void;
  close_transfer: void;
  capture_file: CapturedBlob | null;
  sweep_directory: void;
  file_digest: readonly [string, number];
  verified_version: FileVersion | null;
  copy_verified: void;
  install_verified: void;
  fsync_directory: void;
  open_directory: number;
  close_directory: void;
  copy_verified_at: void;
  install_verified_at: void;
}
export type FileOperation = keyof FileOperationArguments;
export type FileWorkerRequest =
  | {
      [Operation in FileOperation]: {
        id: number;
        operation: Operation;
        arguments: FileOperationArguments[Operation];
      };
    }[FileOperation]
  | { operation: 'stop' };
export interface FileWorkerError {
  name: string;
  message: string;
  code?: string;
  errno?: number;
  syscall?: string;
  errors?: FileWorkerError[];
  cause?: FileWorkerError;
}
export type FileWorkerResponse =
  | { cleanup_failed: FileWorkerError }
  | { id: number; value: FileOperationResults[FileOperation] }
  | { id: number; error: FileWorkerError };
