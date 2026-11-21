import { createPendingConversationFiles, pendingConversationFileBytes, type PendingConversationFile } from "./pendingFiles";
import { createPendingConversationImages, type PendingConversationImage } from "./pendingImages";

export const ATTACHMENT_REJECTED = "Some attachments have an unsupported format or exceed the attachment limit.";

/** Adds chosen, pasted or dropped files to what is already attached: pictures first, then the
 *  rest as files, under the same limits as the conversation composer. `rejected` is true when
 *  any file was refused. */
export async function takePendingAttachments(
  picked: readonly File[],
  current: { images: readonly PendingConversationImage[]; files: readonly PendingConversationFile[] }
): Promise<{ images: PendingConversationImage[]; files: PendingConversationFile[]; rejected: boolean }> {
  let nextId = Math.max(0, ...current.images.map((image) => image.id + 1), ...current.files.map((file) => file.id + 1));
  const pictureFiles = picked.filter((file) => file.type.startsWith("image/"));
  const pictures = await createPendingConversationImages(
    pictureFiles, nextId, current.images.reduce((sum, image) => sum + image.byteCount, 0));
  nextId = pictures.nextId;
  const others = await createPendingConversationFiles(
    picked.filter((file) => !pictureFiles.includes(file)), nextId, pendingConversationFileBytes([...current.files]));
  return {
    images: [...current.images, ...pictures.accepted],
    files: [...current.files, ...others.accepted],
    rejected: pictures.rejected.length > 0 || others.rejected.length > 0
  };
}
