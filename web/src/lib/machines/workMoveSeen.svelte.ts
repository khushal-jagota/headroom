import { SvelteMap, SvelteSet } from "svelte/reactivity";

/** A finished move shows in its dialog until the person has seen it. The server keeps the history,
 * so this is only memory of what this page already showed. A reload starts idle. */
export const PAGE_STARTED_AT_MS = Date.now();
export const seenWorkMoves = new SvelteSet<string>();
/** The source machine of a move this page began. The server does not record it. */
export const workMoveSources = new SvelteMap<string, string>();
