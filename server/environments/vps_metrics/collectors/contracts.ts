/** Collection uses filesystem and OS evidence only, never process arguments or environments. */
export interface CollectorPaths {
  proc_root: string;
  sys_root: string;
  cgroup_root: string;
}
export interface FilesystemStatistics {
  blocks: number | bigint;
  available: number | bigint;
  fragment_size: number | bigint;
}
export interface CollectorDependencies {
  read_text(path: string): Promise<string>;
  directory(path: string): Promise<string[]>;
  exists(path: string): Promise<boolean>;
  inode(path: string): Promise<bigint>;
  wall_clock(): number;
  monotonic(): number;
  clock_ticks(): number;
  page_size(): number;
  statvfs(path: string): FilesystemStatistics | Promise<FilesystemStatistics>;
  service_query(scope: string, timeout: number, output_bytes: number): Promise<string>;
}
