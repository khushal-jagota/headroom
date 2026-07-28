import type { SentMessagePiece } from "./wire";

export const MAX_CONVERSATION_MESSAGE_IMAGE_BYTES = 3 * 1024 * 1024;
export const MAX_CONVERSATION_IMAGE_BYTES = MAX_CONVERSATION_MESSAGE_IMAGE_BYTES;
export const CONVERSATION_IMAGE_MEDIA_TYPES = [
  "image/png",
  "image/jpeg",
  "image/gif",
  "image/webp"
] as const;

const conversationImageMediaTypes = new Set<string>(CONVERSATION_IMAGE_MEDIA_TYPES);

export type PendingConversationImage = {
  id: number;
  fileName: string;
  mediaType: string;
  byteCount: number;
  data: string;
  previewUrl: string;
  previewUrlNeedsRevoking: boolean;
};

export type PendingImageIntake = {
  accepted: PendingConversationImage[];
  rejected: File[];
  nextId: number;
};

export function isImageFile(file: File): boolean {
  return (
    file.size > 0
    && file.size <= MAX_CONVERSATION_IMAGE_BYTES
    && conversationImageMediaTypes.has(file.type.toLowerCase())
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

export async function createPendingConversationImages(
  files: Iterable<File> | ArrayLike<File> | null | undefined,
  firstId: number,
  currentImageBytes = 0,
  createObjectURL: (file: File) => string = URL.createObjectURL,
  revokeObjectURL: (url: string) => void = URL.revokeObjectURL
): Promise<PendingImageIntake> {
  const acceptedFiles: File[] = [];
  const rejected: File[] = [];
  let acceptedImageBytes = currentImageBytes;
  for (const file of Array.from(files ?? [])) {
    if (
      isImageFile(file)
      && acceptedImageBytes + file.size <= MAX_CONVERSATION_MESSAGE_IMAGE_BYTES
    ) {
      acceptedFiles.push(file);
      acceptedImageBytes += file.size;
    } else {
      rejected.push(file);
    }
  }

  const encoded = await Promise.all(
    acceptedFiles.map(async (file) => bytesAsBase64(new Uint8Array(await file.arrayBuffer())))
  );
  const accepted: PendingConversationImage[] = [];
  try {
    for (const [index, file] of acceptedFiles.entries()) {
      accepted.push({
        id: firstId + index,
        fileName: file.name,
        mediaType: file.type,
        byteCount: file.size,
        data: encoded[index] ?? "",
        previewUrl: createObjectURL(file),
        previewUrlNeedsRevoking: true
      });
    }
  } catch (error) {
    releasePendingImages(accepted, revokeObjectURL);
    throw error;
  }
  return { accepted, rejected, nextId: firstId + accepted.length };
}

export function pendingImagesAsPieces(
  images: readonly PendingConversationImage[]
): SentMessagePiece[] {
  return images.map((image) => ({
    piece: "image",
    data: image.data,
    media_type: image.mediaType,
    ...(image.fileName === "" ? {} : { file_name: image.fileName })
  }));
}

export function restoredPendingImages(
  pieces: readonly SentMessagePiece[],
  firstId: number
): { images: PendingConversationImage[]; nextId: number } {
  const imagePieces = pieces.filter(
    (piece): piece is Extract<SentMessagePiece, { piece: "image" }> => piece.piece === "image"
  );
  return {
    images: imagePieces.map((piece, index) => ({
      id: firstId + index,
      fileName: piece.file_name ?? "",
      mediaType: piece.media_type,
      byteCount: base64DecodedByteCount(piece.data),
      data: piece.data,
      previewUrl: `data:${piece.media_type};base64,${piece.data}`,
      previewUrlNeedsRevoking: false
    })),
    nextId: firstId + imagePieces.length
  };
}

export function pendingConversationImageBytes(
  images: readonly PendingConversationImage[]
): number {
  return images.reduce((total, image) => total + image.byteCount, 0);
}

function base64DecodedByteCount(data: string): number {
  if (data === "") return 0;
  const padding = data.endsWith("==") ? 2 : data.endsWith("=") ? 1 : 0;
  return Math.floor(data.length / 4) * 3 - padding;
}

export function releasePendingImages(
  images: readonly PendingConversationImage[],
  revokeObjectURL: (url: string) => void = URL.revokeObjectURL
): void {
  for (const image of images) {
    if (image.previewUrlNeedsRevoking) revokeObjectURL(image.previewUrl);
  }
}
