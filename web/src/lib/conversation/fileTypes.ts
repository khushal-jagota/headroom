/** What a file is called by a person ("CSV", "PDF", "PNG"), never its raw media type: a
 *  card reads "CSV · 1.7 KiB", not "text/csv · 1.7 KiB". The same name is used while a file
 *  waits in the composer and once it is sent. */
const NAMES_BY_EXTENSION: Record<string, string> = {
  csv: "CSV", tsv: "TSV", json: "JSON", jsonl: "JSON Lines", ndjson: "JSON Lines",
  md: "Markdown", markdown: "Markdown", txt: "Text", text: "Text", log: "Log",
  pdf: "PDF", html: "HTML", htm: "HTML", xml: "XML", yaml: "YAML", yml: "YAML", toml: "TOML",
  zip: "ZIP", gz: "Gzip", tar: "Tar",
  png: "PNG", jpg: "JPEG", jpeg: "JPEG", gif: "GIF", webp: "WebP", svg: "SVG", avif: "AVIF", heic: "HEIC", bmp: "BMP",
  mp4: "MP4", mov: "MOV", webm: "WebM", m4v: "MP4",
  mp3: "MP3", wav: "WAV", m4a: "M4A", ogg: "Ogg", flac: "FLAC", opus: "Opus", aac: "AAC",
  ts: "TypeScript", js: "JavaScript", go: "Go", py: "Python", swift: "Swift", rs: "Rust", sh: "Shell", sql: "SQL",
  diff: "Diff", patch: "Diff", docx: "Word", xlsx: "Excel", pptx: "PowerPoint", key: "Keynote",
};

function extensionOf(fileName: string): string {
  const dot = fileName.lastIndexOf(".");
  return dot <= 0 || dot === fileName.length - 1 ? "" : fileName.slice(dot + 1).toLowerCase();
}

export function friendlyFileType(fileName: string, mediaType = ""): string {
  const named = NAMES_BY_EXTENSION[extensionOf(fileName)];
  if (named) return named;
  const type = mediaType.toLowerCase().split(";")[0]!.trim();
  if (type === "application/pdf") return "PDF";
  if (type === "application/json") return "JSON";
  if (type === "text/csv") return "CSV";
  if (type === "text/markdown") return "Markdown";
  if (type.startsWith("text/")) return "Text";
  if (type.startsWith("image/")) return "Image";
  if (type.startsWith("video/")) return "Video";
  if (type.startsWith("audio/")) return "Audio";
  return "File";
}

/** Files a person can read as text in the browser, so the sent card offers Preview. */
export function fileOpensAsText(fileName: string, mediaType = ""): boolean {
  const kind = friendlyFileType(fileName, mediaType);
  return ["CSV", "TSV", "JSON", "JSON Lines", "Markdown", "Text", "Log", "PDF", "YAML", "TOML", "XML", "Diff"].includes(kind)
    || mediaType.toLowerCase().startsWith("text/");
}
