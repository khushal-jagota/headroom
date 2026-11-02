/** Mirrors conversation/video_uploads/contracts.py. Video bytes never enter JSON sends. */
export type UploadDestination = {
  kind: "chief" | "manager" | "ticket" | "conversation";
  id: string;
  expected_conversation_id: string | null;
};
export type VideoUploadStatus = {
  upload_id: string;
  destination: UploadDestination;
  filename: string;
  byte_count: number;
  received_bytes: number;
  state: "reserved" | "transferring" | "ready" | "admitted" | "linked" | "failed" | "cancelled";
  media_type: string | null;
  expires_at: number;
  error: string | null;
};
export type VideoUploadAdapter = {
  reserve(destination: UploadDestination, file: File, signal: AbortSignal): Promise<VideoUploadStatus>;
  transfer(uploadId: string, file: File, signal: AbortSignal, progress: (bytes: number) => void): Promise<VideoUploadStatus>;
  status(uploadId: string): Promise<VideoUploadStatus>;
  cancel(uploadId: string): Promise<void>;
};
export type PendingVideo = {
  id: number;
  fileName: string;
  byteCount: number;
  mediaType: string;
  destination: UploadDestination;
  uploadId?: string;
  phase: "uploading" | "validating" | "ready" | "failed" | "interrupted";
  receivedBytes: number;
  error?: string;
  /** Memory only: never included by the persistence serializer. */
  file?: File;
};
export type ComposerSendContext = {
  messageId: string;
  expectedConversationId: string | null;
  destination?: UploadDestination;
};
