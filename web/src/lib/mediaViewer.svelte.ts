/* The expanded view for an embedded picture or video: one item at a time, opened by a
 * press on the media and closed back to it. The viewer mounts itself on the first open,
 * so every host that draws a preview gets it without wiring one in. */
import { mount } from "svelte";
import MediaViewer from "../components/MediaViewer.svelte";

export type ViewedMedia = { href: string; label: string; kind: "image" | "video"; downloadName: string };

export const mediaViewer = $state<{ item: ViewedMedia | null }>({ item: null });

let opener: HTMLElement | null = null;
let mounted = false;

export function openMediaViewer(item: ViewedMedia, from: HTMLElement | null): void {
  opener = from;
  if (!mounted) {
    mounted = true;
    mount(MediaViewer, { target: document.body });
  }
  mediaViewer.item = item;
}

export function closeMediaViewer(): void {
  if (!mediaViewer.item) return;
  mediaViewer.item = null;
  const returnTo = opener;
  opener = null;
  if (returnTo?.isConnected) returnTo.focus();
}
