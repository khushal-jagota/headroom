import { friendlyFileType } from "../../../lib/conversation/fileTypes";
import type { PendingVideo } from "../../../lib/conversation/videoUploads/contracts";
import type { PendingConversationImage } from "../../../lib/conversation/pendingImages";
import type { PendingConversationFile } from "../../../lib/conversation/pendingFiles";

export type ComposerAttachmentReference = Readonly<{ kind: "image" | "file" | "video"; id: number }>;
export type ComposerAttachment =
  | { kind: "image"; value: PendingConversationImage }
  | { kind: "file"; value: PendingConversationFile }
  | { kind: "video"; value: PendingVideo };

/** Changing a question draft replaces its images/files while a video stays in the message draft. */
export function replaceQuestionAttachmentOrder(
  current: readonly ComposerAttachmentReference[],
  images: readonly Pick<PendingConversationImage, "id">[],
  files: readonly Pick<PendingConversationFile, "id">[]
): ComposerAttachmentReference[] {
  return [
    ...current.filter((reference) => reference.kind === "video"),
    ...images.map((image) => ({ kind: "image" as const, id: image.id })),
    ...files.map((file) => ({ kind: "file" as const, id: file.id }))
  ];
}

export function orderedComposerAttachments(
  order: readonly ComposerAttachmentReference[],
  images: readonly PendingConversationImage[],
  files: readonly PendingConversationFile[],
  videos: readonly PendingVideo[] = []
): ComposerAttachment[] {
  return order.flatMap((reference): ComposerAttachment[] => {
    if (reference.kind === "image") {
      const image = images.find((candidate) => candidate.id === reference.id);
      return image ? [{ kind: "image", value: image }] : [];
    }
    if (reference.kind === "video") {
      const video = videos.find(candidate => candidate.id === reference.id);
      return video ? [{kind: "video", value: video}] : [];
    }
    const file = files.find((candidate) => candidate.id === reference.id);
    return file ? [{ kind: "file", value: file }] : [];
  });
}

export function attachmentExtension(name: string): string {
  const dot = name.lastIndexOf(".");
  return (dot < 0 ? "" : name.slice(dot + 1).toUpperCase()).slice(0, 4) || "FILE";
}

/** A file's size as the sent file card writes it ("692 B", "1.7 KiB", "24 KiB", "3.1 MiB"), so a
 *  file reads the same waiting in the composer and once it is in the transcript. */
export function attachmentByteLabel(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

/** The kind a file is called by, in words, as the sent file card calls it ("Text", "CSV",
 *  "PDF"), never its raw media type: a person reads "Text · 692 B", not "text/plain · 692 B".
 *  One naming for pending and sent files (lib/conversation/fileTypes.ts). */
export function attachmentKindLabel(fileName: string, mediaType = ""): string {
  return friendlyFileType(fileName, mediaType);
}

/** A file's name cut in its middle when it is long, so both its start and its kind survive
 *  ("export-…-v1.csv"). The whole name stays in the tile's title. */
export function attachmentDisplayName(name: string, longest = 26): string {
  const characters = Array.from(name);
  if (characters.length <= longest) return name;
  const dot = name.lastIndexOf(".");
  const tailLength = Math.min(Math.max(dot < 0 ? 0 : characters.length - Array.from(name.slice(0, dot)).length, 0) + 4, Math.floor(longest / 2));
  const headLength = longest - tailLength - 1;
  return `${characters.slice(0, headLength).join("")}…${characters.slice(characters.length - tailLength).join("")}`;
}
