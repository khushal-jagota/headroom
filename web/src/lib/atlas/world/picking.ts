// What the pointer is over.
//
// Every object that can be picked up carries a tag. Picking casts a ray, walks up
// from whatever it struck to the first tagged ancestor, and answers with that tag —
// so the mesh of a worker's arm answers "this worker", and the shallows answer
// water, which is the world's way of saying "nothing".

import * as THREE from "three";
import { TAP } from "./tuning";

export type PickTag =
  | { kind: "water" }
  | { kind: "island"; projectId: string }
  | { kind: "item"; itemId: string }
  | { kind: "overseer"; itemId: string }
  | { kind: "ticket"; ticketId: string }
  | { kind: "stele"; ticketId: string };

export class PickIndex {
  private readonly tags = new WeakMap<THREE.Object3D, PickTag>();

  tag<T extends THREE.Object3D>(object: T, tag: PickTag): T {
    this.tags.set(object, tag);
    return object;
  }

  at(object: THREE.Object3D): PickTag | null {
    let node: THREE.Object3D | null = object;
    while (node) {
      const tag = this.tags.get(node);
      if (tag) return tag;
      node = node.parent;
    }
    return null;
  }
}

export type PickerOptions = {
  canvas: HTMLCanvasElement;
  camera: THREE.Camera;
  root: THREE.Object3D;
  index: PickIndex;
  onTap: (tag: PickTag | null) => void;
};

export type Picker = {
  // Water is the absence of anything: it answers null, like empty sky.
  at: (clientX: number, clientY: number) => PickTag | null;
  dispose: () => void;
};

export function createPicker(options: PickerOptions): Picker {
  const { canvas, camera, root, index, onTap } = options;
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();

  const at = (clientX: number, clientY: number): PickTag | null => {
    const rect = canvas.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return null;
    pointer.set(
      ((clientX - rect.left) / rect.width) * 2 - 1,
      -((clientY - rect.top) / rect.height) * 2 + 1
    );
    raycaster.setFromCamera(pointer, camera);
    for (const hit of raycaster.intersectObjects(root.children, true)) {
      const tag = index.at(hit.object);
      if (!tag) continue;
      return tag.kind === "water" ? null : tag;
    }
    return null;
  };

  let downAt: { x: number; y: number; at: number } | null = null;

  const onPointerDown = (event: PointerEvent): void => {
    downAt = { x: event.clientX, y: event.clientY, at: performance.now() };
  };

  const onPointerUp = (event: PointerEvent): void => {
    if (!downAt) return;
    const moved = Math.hypot(event.clientX - downAt.x, event.clientY - downAt.y);
    const heldMs = performance.now() - downAt.at;
    downAt = null;
    // it was a drag or a long press, not a tap
    if (moved > TAP.moveTolerance || heldMs > TAP.holdMs) return;
    onTap(at(event.clientX, event.clientY));
  };

  const coarse =
    typeof window.matchMedia === "function" && window.matchMedia("(pointer: coarse)").matches;
  let lastHover = 0;
  const onPointerMove = (event: PointerEvent): void => {
    const now = performance.now();
    if (now - lastHover < TAP.hoverThrottleMs) return;
    lastHover = now;
    canvas.style.cursor = at(event.clientX, event.clientY) ? "pointer" : "default";
  };

  canvas.addEventListener("pointerdown", onPointerDown);
  canvas.addEventListener("pointerup", onPointerUp);
  // desktop: show a pointer over anything readable
  if (!coarse) canvas.addEventListener("pointermove", onPointerMove);

  return {
    at,
    dispose(): void {
      canvas.removeEventListener("pointerdown", onPointerDown);
      canvas.removeEventListener("pointerup", onPointerUp);
      canvas.removeEventListener("pointermove", onPointerMove);
      canvas.style.cursor = "";
    }
  };
}
