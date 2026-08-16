// What the world says out loud.
//
// Real happenings only — a stage move, a needs-you, trouble, a finish — each drawn
// as a speech bubble over the figure it belongs to. The text arrives already
// written from the reading of the board; nothing here invents a line.

import * as THREE from "three";
import type { AtlasHappening } from "../model";
import { AtlasKit } from "./materials";
import { BUBBLE } from "./tuning";

type Tone = AtlasHappening["tone"];

const TONES: Record<Tone, [string, string]> = {
  stage: ["rgba(242,238,226,0.96)", "#3d372c"],
  done: ["rgba(224,236,218,0.96)", "#37543c"],
  needs: ["rgba(222,229,245,0.97)", "#3c4f80"],
  trouble: ["rgba(48,40,36,0.93)", "#f0e8dc"]
};

const FONT = "600 30px Iowan Old Style, Palatino, Georgia, serif";

function roundRectPath(
  g: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number
): void {
  g.beginPath();
  g.moveTo(x + r, y);
  g.arcTo(x + w, y, x + w, y + h, r);
  g.arcTo(x + w, y + h, x, y + h, r);
  g.arcTo(x, y + h, x, y, r);
  g.arcTo(x, y, x + w, y, r);
  g.closePath();
}

function bubbleSprite(
  kit: AtlasKit,
  measure: (text: string) => number,
  text: string,
  tone: Tone
): THREE.Sprite {
  const pad = 20;
  const line = text.length > 30 ? text.slice(0, 29) + "…" : text;
  const w = Math.max(90, Math.ceil(measure(line)) + pad * 2);
  const [bg, fg] = TONES[tone] ?? TONES.stage;
  const texture = kit.texture(w, 84, (g) => {
    g.fillStyle = bg;
    roundRectPath(g, 2, 2, w - 4, 58, 14);
    g.fill();
    g.strokeStyle = "rgba(90,82,66,0.55)";
    g.lineWidth = 2;
    roundRectPath(g, 2, 2, w - 4, 58, 14);
    g.stroke();
    g.beginPath();
    g.moveTo(w / 2 - 11, 59);
    g.lineTo(w / 2, 80);
    g.lineTo(w / 2 + 11, 59);
    g.closePath();
    g.fillStyle = bg;
    g.fill();
    g.font = FONT;
    g.fillStyle = fg;
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.fillText(line, w / 2, 31);
  });
  const sprite = new THREE.Sprite(
    new THREE.SpriteMaterial({ map: texture, transparent: true, depthTest: false })
  );
  const h = 0.5;
  sprite.scale.set(h * (w / 84), h, 1);
  sprite.renderOrder = 10;
  return sprite;
}

export type BubbleAnchor = { object: THREE.Object3D; lift: number };

type Bubble = {
  sprite: THREE.Sprite;
  material: THREE.SpriteMaterial;
  until: number;
  baseY: number;
  anchor: THREE.Object3D;
  width: number;
  height: number;
};

const _worldAt = new THREE.Vector3();

export class BubbleLayer {
  private readonly pending: AtlasHappening[] = [];
  private readonly live: Bubble[] = [];
  private readonly byAnchor = new Map<THREE.Object3D, Bubble>();
  private measurer: CanvasRenderingContext2D | null = null;

  constructor(private readonly kit: AtlasKit) {}

  private measure(text: string): number {
    if (!this.measurer) this.measurer = document.createElement("canvas").getContext("2d");
    if (!this.measurer) return 0;
    this.measurer.font = FONT;
    return this.measurer.measureText(text).width;
  }

  say(happening: AtlasHappening): void {
    this.pending.push(happening);
  }

  // Attach queued lines to their figures. A happening whose figure is not in the
  // world — a Ticket on an island that has not been built yet — is let go.
  flush(time: number, findAnchor: (ticketId: string) => BubbleAnchor | null): void {
    while (this.pending.length) {
      const happening = this.pending.shift();
      if (!happening) break;
      const anchor = findAnchor(happening.ticketId);
      if (!anchor) continue;
      const existing = this.byAnchor.get(anchor.object);
      if (existing) this.remove(existing);
      const sprite = bubbleSprite(
        this.kit,
        (text) => this.measure(text),
        happening.text,
        happening.tone
      );
      sprite.position.copy(anchor.object.position);
      sprite.position.y += anchor.lift;
      const parent = anchor.object.parent;
      if (!parent) {
        this.kit.release(sprite);
        continue;
      }
      parent.add(sprite);
      const bubble: Bubble = {
        sprite,
        material: sprite.material,
        until: time + BUBBLE.life,
        baseY: sprite.position.y,
        anchor: anchor.object,
        width: sprite.scale.x,
        height: sprite.scale.y
      };
      this.byAnchor.set(anchor.object, bubble);
      this.live.push(bubble);
    }
  }

  step(now: number, anim: number, camera: THREE.Camera): void {
    for (let i = this.live.length - 1; i >= 0; i--) {
      const bubble = this.live[i];
      const left = bubble.until - now;
      if (left <= 0) {
        this.remove(bubble);
        continue;
      }
      bubble.sprite.position.y =
        bubble.baseY + Math.sin(anim * BUBBLE.floatRate) * BUBBLE.floatAmplitude;
      bubble.material.opacity =
        Math.min(1, left / BUBBLE.fadeOut) *
        Math.min(1, (BUBBLE.life - left) / BUBBLE.fadeIn + 0.2);
      // keep bubbles readable from any distance: scale with range
      bubble.sprite.getWorldPosition(_worldAt);
      const k = Math.min(
        BUBBLE.rangeMax,
        Math.max(1, camera.position.distanceTo(_worldAt) / BUBBLE.rangeUnit)
      );
      bubble.sprite.scale.set(bubble.width * k, bubble.height * k, 1);
    }
  }

  // A figure that leaves the world takes its bubble with it.
  forget(object: THREE.Object3D): void {
    const bubble = this.byAnchor.get(object);
    if (bubble) this.remove(bubble);
  }

  private remove(bubble: Bubble): void {
    this.kit.release(bubble.sprite);
    if (this.byAnchor.get(bubble.anchor) === bubble) this.byAnchor.delete(bubble.anchor);
    const i = this.live.indexOf(bubble);
    if (i >= 0) this.live.splice(i, 1);
  }

  dispose(): void {
    for (const bubble of [...this.live]) this.remove(bubble);
    this.pending.length = 0;
    this.byAnchor.clear();
    this.measurer = null;
  }
}
