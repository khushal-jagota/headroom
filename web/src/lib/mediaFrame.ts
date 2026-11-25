/** The box a picture or video takes in a message before it loads.
 *
 * With a known size, the box has the media's own shape at its final size. Without one, it
 * is one fixed frame that the media is fitted into. Either way the box is there at first
 * paint, and the media fades in inside it, so nothing around it moves.
 */

export type MediaSize = { width: number; height: number };

/** Largest side the server accepts. A larger one is not a real picture. */
const MEDIA_SIZE_LIMIT = 100_000;
const side = (value: unknown): value is number =>
  typeof value === "number" && Number.isInteger(value) && value > 0 && value <= MEDIA_SIZE_LIMIT;

/** The size a piece carries: two whole sides in range, or none. */
export function mediaSizeOf(piece: { width?: number; height?: number }): MediaSize | null {
  const { width, height } = piece;
  return side(width) && side(height) ? { width, height } : null;
}

/** The frame kind and the custom properties the shared CSS reads for a sized box. */
export function mediaFrame(size: MediaSize | null): { kind: "sized" | "fixed"; style: string | undefined } {
  return size
    ? { kind: "sized", style: `--media-w: ${size.width}px; --media-ratio: ${size.width} / ${size.height}` }
    : { kind: "fixed", style: undefined };
}

/** Hide a picture or video until it can be drawn, then let the CSS fade it in.
 *  An element that already loaded, or failed, shows at once. */
export function revealWhenLoaded(element: HTMLImageElement | HTMLVideoElement) {
  const ready = element instanceof HTMLImageElement
    ? () => element.complete
    : () => element.readyState >= HTMLMediaElement.HAVE_METADATA || element.error !== null;
  const show = () => { element.dataset.mediaLoaded = ""; };
  element.dataset.mediaReveal = "";
  if (ready()) show();
  const events = element instanceof HTMLImageElement ? ["load", "error"] : ["loadedmetadata", "loadeddata", "error"];
  for (const name of events) element.addEventListener(name, show);
  return {
    destroy() { for (const name of events) element.removeEventListener(name, show); }
  };
}

/** The width and height of a picture or video file, as this browser shows it. EXIF
 *  orientation and video rotation are already applied, so a phone portrait reads as
 *  portrait. Anything that cannot be read has no size. */
export async function measureLocalMedia(url: string, kind: "image" | "video"): Promise<MediaSize | null> {
  try {
    if (kind === "image") {
      // The size is known once the image loads; a full decode is not needed.
      const image = new Image();
      const loaded = new Promise<void>((resolve, reject) => {
        image.onload = () => resolve();
        image.onerror = () => reject(new Error("unreadable picture"));
      });
      image.src = url;
      await loaded;
      return mediaSizeOf({ width: image.naturalWidth, height: image.naturalHeight });
    }
    const video = document.createElement("video");
    video.preload = "metadata";
    video.muted = true;
    const loaded = new Promise<void>((resolve, reject) => {
      video.onloadedmetadata = () => resolve();
      video.onerror = () => reject(new Error("unreadable video"));
    });
    video.src = url;
    await loaded;
    const size = mediaSizeOf({ width: video.videoWidth, height: video.videoHeight });
    video.removeAttribute("src");
    video.load();
    return size;
  } catch {
    return null;
  }
}
