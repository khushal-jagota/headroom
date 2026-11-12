/** Private thread messages. These are not executor wire or file-manifest types. */
import type { FileVersion } from './file_metadata.ts';
export interface DirectoryHandle {
  readonly id: number;
}
export interface FileOperationArguments {
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
}
export type FileWorkerResponse =
  | { id: number; value: FileOperationResults[FileOperation] }
  | { id: number; error: FileWorkerError };
