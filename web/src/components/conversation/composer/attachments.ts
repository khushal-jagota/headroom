import type { PendingConversationImage } from "../../../lib/conversation/pendingImages";
import type { PendingConversationFile } from "../../../lib/conversation/pendingFiles";

export type ComposerAttachmentReference = Readonly<{ kind: "image" | "file"; id: number }>;
export type ComposerAttachment =
  | { kind: "image"; value: PendingConversationImage }
  | { kind: "file"; value: PendingConversationFile };

export function orderedComposerAttachments(
  order: readonly ComposerAttachmentReference[],
  images: readonly PendingConversationImage[],
  files: readonly PendingConversationFile[]
): ComposerAttachment[] {
  return order.flatMap((reference): ComposerAttachment[] => {
    if (reference.kind === "image") {
      const image = images.find((candidate) => candidate.id === reference.id);
      return image ? [{ kind: "image", value: image }] : [];
    }
    const file = files.find((candidate) => candidate.id === reference.id);
    return file ? [{ kind: "file", value: file }] : [];
  });
}

export function attachmentExtension(name: string): string {
  const dot = name.lastIndexOf(".");
  return (dot < 0 ? "" : name.slice(dot + 1).toUpperCase()).slice(0, 4) || "FILE";
}

export function attachmentByteLabel(bytes: number): string {
  return bytes < 1024 ? `${bytes} B` : bytes < 1024 * 1024
    ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
