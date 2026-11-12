/** Video intake limits match the Python configuration. */
export const MAX_VIDEO_BYTES = 250 * 1024 * 1024;
export const VIDEO_BUDGET_BYTES = 10 * 1024 * 1024 * 1024;
export const FREE_SPACE_FLOOR_BYTES = 5 * 1024 * 1024 * 1024;
export const UPLOAD_TTL_SECONDS = 24 * 60 * 60;
export const TRANSFER_CHUNK_BYTES = 256 * 1024;
export const PROGRESS_RECORD_BYTES = 8 * 1024 * 1024;
export const PROBE_TIMEOUT_SECONDS = 10;
export const PROBE_OUTPUT_BYTES = 64 * 1024;
export const CLEANUP_INTERVAL_SECONDS = 60;
export const CONTAINER_HEADER_BYTES = 4096;
export interface VideoUploadLimits {
  max_video_bytes: number;
  video_budget_bytes: number;
  free_space_floor_bytes: number;
  upload_ttl_seconds: number;
  transfer_chunk_bytes: number;
  progress_record_bytes: number;
}
export const VIDEO_UPLOAD_LIMITS: Readonly<VideoUploadLimits> = {
  max_video_bytes: MAX_VIDEO_BYTES,
  video_budget_bytes: VIDEO_BUDGET_BYTES,
  free_space_floor_bytes: FREE_SPACE_FLOOR_BYTES,
  upload_ttl_seconds: UPLOAD_TTL_SECONDS,
  transfer_chunk_bytes: TRANSFER_CHUNK_BYTES,
  progress_record_bytes: PROGRESS_RECORD_BYTES,
};
