import type { PendingVideo } from "../../../lib/conversation/videoUploads/contracts";
import type { PendingConversationImage } from "../../../lib/conversation/pendingImages";
import type { PendingConversationFile } from "../../../lib/conversation/pendingFiles";

export type ComposerAttachmentReference = Readonly<{ kind: "image" | "file" | "video"; id: number }>;
export type ComposerAttachment =
  | { kind: "image"; value: PendingConversationImage }
  | { kind: "file"; value: PendingConversationFile }
  | { kind: "video"; value: PendingVideo };

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

const TEXT_EXTENSIONS = new Set(["txt", "text", "log", "csv", "tsv", "json", "jsonl", "md", "markdown"]);

/** The kind a file is called by, in words, as the sent file card calls it ("Text", "PDF"),
 *  never its raw media type: a person reads "Text · 692 B", not "text/plain · 692 B". */
export function attachmentKindLabel(fileName: string, mediaType = ""): string {
  const dot = fileName.lastIndexOf(".");
  const extension = dot < 0 ? "" : fileName.slice(dot + 1).toLowerCase();
  const type = mediaType.toLowerCase();
  if (extension === "pdf" || type === "application/pdf") return "PDF";
  if (TEXT_EXTENSIONS.has(extension) || type.startsWith("text/") || type === "application/json") return "Text";
  if (type.startsWith("image/")) return "Image";
  if (type.startsWith("video/")) return "Video";
  if (type.startsWith("audio/")) return "Audio";
  return "File";
}
