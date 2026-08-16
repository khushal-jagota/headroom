// The boundary between the world and the app.
//
// The world is three.js and knows nothing about Svelte, queries, or writes. The
// screen is Svelte and knows nothing about geometry. These are the only shapes
// that cross, so either side can be worked on without opening the other.

import type { AtlasWorld } from "./model";

// What the human has picked up out of the world. Each kind raises a different
// real screen over it.
export type AtlasSelection =
  | { kind: "ticket"; id: string }
  | { kind: "item"; id: string }
  | { kind: "overseer"; id: string }
  | { kind: "stele"; id: string };

// A stop on the review walk: the world travels to the worker, and the panel shows
// that proposal rather than whatever the queue would have chosen for itself.
export type AtlasWalkStop = {
  ticketId: string;
  field: string | null;
  index: number;
  total: number;
};

export type AtlasSceneOptions = {
  canvas: HTMLCanvasElement;
  // Where the world puts its speech bubbles and its nameplates. The scene owns
  // the canvas; this element is the layer above it for anything that must be
  // real text rather than a drawn texture.
  onSelect: (selection: AtlasSelection | null) => void;
  // The world asks the screen to travel somewhere as part of the review walk.
  reducedMotion: boolean;
};

// What the screen holds after the world starts. Every method is safe to call
// before the first world arrives.
export type AtlasScene = {
  // A fresh reading of the board. The scene diffs it against what it holds and
  // speaks the differences.
  show: (world: AtlasWorld) => void;
  // Put the selection ring somewhere, travelling there if it is not already in
  // view. Passing null clears the ring and lets the idle orbit resume.
  select: (selection: AtlasSelection | null, options?: { travel?: boolean }) => void;
  // The ATLAS wordmark: back to the whole archipelago, from anywhere.
  home: () => void;
  dispose: () => void;
};
