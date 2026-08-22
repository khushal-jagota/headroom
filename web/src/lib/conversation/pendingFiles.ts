import type { SentMessagePiece } from "./wire";

export const MAX_CONVERSATION_MESSAGE_FILE_BYTES = 10 * 1024 * 1024;

const MEDIA_TYPE_BY_EXTENSION = new Map<string, string>([
  ["pdf", "application/pdf"],
  ["txt", "text/plain"],
  ["md", "text/markdown"],
  ["markdown", "text/markdown"],
  ["csv", "text/csv"],
  ["tsv", "text/tab-separated-values"],
  ["json", "application/json"],
  ["jsonl", "application/x-ndjson"]
]);

export const CONVERSATION_FILE_ACCEPT = [...MEDIA_TYPE_BY_EXTENSION.keys()]
  .map((extension) => `.${extension}`)
  .join(",");

export type PendingConversationFile = {
  id: number;
  fileName: string;
  mediaType: string;
  byteCount: number;
  data: string;
};

export type PendingFileIntake = {
  accepted: PendingConversationFile[];
  rejected: File[];
  nextId: number;
};

function extensionOf(fileName: string): string {
  const dot = fileName.lastIndexOf(".");
  return dot < 0 ? "" : fileName.slice(dot + 1).toLowerCase();
}

export function conversationFileMediaType(fileName: string): string | null {
  return MEDIA_TYPE_BY_EXTENSION.get(extensionOf(fileName)) ?? null;
}

export function isSafeConversationFileName(fileName: string): boolean {
  return (
    fileName !== ""
    && fileName.length <= 255
    && fileName.trim() === fileName
    && !fileName.includes("/")
    && !fileName.includes("\\")
    && !/[\u0000-\u001f\u007f]/.test(fileName)
  );
}

export function isSupportedConversationFile(file: File): boolean {
  return (
    file.size > 0
    && file.size <= MAX_CONVERSATION_MESSAGE_FILE_BYTES
    && isSafeConversationFileName(file.name)
    && conversationFileMediaType(file.name) !== null
  );
}

function bytesAsBase64(bytes: Uint8Array): string {
  let binary = "";
  const chunkSize = 0x8000;
  for (let at = 0; at < bytes.length; at += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(at, at + chunkSize));
  }
  return btoa(binary);
}

function validUtf8(bytes: Uint8Array): string | null {
  try {
    const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    return text.includes("\u0000") ? null : text;
  } catch {
    return null;
  }
}

function contentMatches(mediaType: string, bytes: Uint8Array): boolean {
  if (mediaType === "application/pdf") {
    const headerMatches = bytes.length >= 5
      && new TextDecoder().decode(bytes.subarray(0, 5)) === "%PDF-";
    const tail = new TextDecoder().decode(bytes.subarray(Math.max(0, bytes.length - 1024)));
    return headerMatches && tail.includes("%%EOF");
  }
  const text = validUtf8(bytes);
  if (text === null) return false;
  try {
    if (mediaType === "application/json") JSON.parse(text);
    if (mediaType === "application/x-ndjson") {
      const lines = text.split(/\r?\n/).filter((line) => line.trim() !== "");
      if (lines.length === 0) return false;
      for (const line of lines) JSON.parse(line);
    }
  } catch {
    return false;
  }
  return true;
}

export async function createPendingConversationFiles(
  files: Iterable<File> | ArrayLike<File> | null | undefined,
  firstId: number,
  currentFileBytes = 0
): Promise<PendingFileIntake> {
  const accepted: PendingConversationFile[] = [];
  const rejected: File[] = [];
  let acceptedBytes = currentFileBytes;
  let nextId = firstId;
  for (const file of Array.from(files ?? [])) {
    const mediaType = conversationFileMediaType(file.name);
    if (
      mediaType === null
      || !isSupportedConversationFile(file)
      || acceptedBytes + file.size > MAX_CONVERSATION_MESSAGE_FILE_BYTES
    ) {
      rejected.push(file);
      continue;
    }
    const bytes = new Uint8Array(await file.arrayBuffer());
    if (!contentMatches(mediaType, bytes)) {
      rejected.push(file);
      continue;
    }
    accepted.push({
      id: nextId,
      fileName: file.name,
      mediaType,
      byteCount: file.size,
      data: bytesAsBase64(bytes)
    });
    nextId += 1;
    acceptedBytes += file.size;
  }
  return { accepted, rejected, nextId };
}

export function pendingFilesAsPieces(
  files: readonly PendingConversationFile[]
): SentMessagePiece[] {
  return files.map((file) => ({
    piece: "file",
    data: file.data,
    media_type: file.mediaType,
    file_name: file.fileName
  }));
}

export function restoredPendingFiles(
  pieces: readonly SentMessagePiece[],
  firstId: number
): { files: PendingConversationFile[]; nextId: number } {
  const filePieces = pieces.filter(
    (piece): piece is Extract<SentMessagePiece, { piece: "file" }> => piece.piece === "file"
  );
  return {
    files: filePieces.map((piece, index) => ({
      id: firstId + index,
      fileName: piece.file_name,
      mediaType: piece.media_type,
      byteCount: base64DecodedByteCount(piece.data),
      data: piece.data
    })),
    nextId: firstId + filePieces.length
  };
}

export function pendingConversationFileBytes(files: readonly PendingConversationFile[]): number {
  return files.reduce((total, file) => total + file.byteCount, 0);
}

export function base64DecodedByteCount(data: string): number {
  if (data === "") return 0;
  const padding = data.endsWith("==") ? 2 : data.endsWith("=") ? 1 : 0;
  return Math.floor(data.length / 4) * 3 - padding;
}
