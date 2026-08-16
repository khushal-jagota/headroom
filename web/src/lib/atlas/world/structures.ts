// A Sprint Item is a structure, and its physical state is the Item's progress.
//
// What gets built follows what the Item's people do — construction is not
// universal. A coding crew raises a temple, a planning crew a council exedra, a
// personal Item a tended garden. Half-built means half-built: columns missing,
// scaffolding up, dressed stones stacked and waiting beside the marked ground.

import * as THREE from "three";
import { AtlasKit } from "./materials";
import { mulberry32 } from "./random";

export const ARCHETYPES = ["temple", "tholos", "stoa", "altar", "workshop"];

export const TYPE_ARCHETYPE: Record<string, string> = {
  coding: "temple",
  general: "stoa",
  debugging: "workshop",
  exploration: "lighthouse",
  product_design: "tholos",
  new_worker: "statuary",
  initiative_planning: "exedra",
  "planning-day": "exedra",
  "planning-midday-check": "exedra",
  "planning-sprint": "exedra",
  personal: "garden"
};

export function archetypeFor(dominantType: string, slot: number): string {
  return TYPE_ARCHETYPE[dominantType] || ARCHETYPES[slot % ARCHETYPES.length];
}

// Anything that burns: the altar's fire, the lighthouse lamp, a trouble brazier.
export type Flame = { mesh: THREE.Mesh; x: number };

export type Built = {
  group: THREE.Group;
  flames: Flame[];
  flameLights: THREE.PointLight[];
};

function colShaft(kit: AtlasKit, h: number, r: number): THREE.Group {
  const g = new THREE.Group();
  g.add(kit.cyl(r, r * 1.12, h, 10, kit.M.marble, 0, h / 2, 0));
  g.add(kit.box(r * 2.6, 0.07, r * 2.6, kit.M.marble, 0, h + 0.035, 0));
  g.add(kit.cyl(r * 1.35, r * 1.15, 0.07, 10, kit.M.marble, 0, h - 0.02, 0));
  return g;
}

// a small all-marble figure: the subject of the statuary
export function marbleFigure(kit: AtlasKit): THREE.Group {
  const g = new THREE.Group();
  g.add(kit.box(0.14, 0.2, 0.09, kit.M.marble, 0, 0.26, 0));
  g.add(kit.box(0.15, 0.09, 0.1, kit.M.marble, 0, 0.12, 0));
  const head = new THREE.Mesh(new THREE.SphereGeometry(0.06, 8, 6), kit.M.marble);
  head.position.y = 0.42;
  head.castShadow = true;
  g.add(head);
  g.add(kit.box(0.04, 0.16, 0.045, kit.M.marble, -0.09, 0.28, 0));
  g.add(kit.box(0.04, 0.16, 0.045, kit.M.marble, 0.09, 0.28, 0));
  return g;
}

function scaffold(kit: AtlasKit, w: number, h: number, d: number): THREE.Group {
  const g = new THREE.Group();
  const px = w / 2 + 0.1;
  const pz = d / 2 + 0.14;
  for (const sx of [-px, px]) g.add(kit.box(0.045, h, 0.045, kit.M.wood, sx, h / 2, pz));
  g.add(kit.box(0.045, h * 0.8, 0.045, kit.M.wood, -px, h * 0.4, -pz));
  g.add(kit.box(0.045, h * 0.8, 0.045, kit.M.wood, px, h * 0.4, -pz));
  for (const y of [h * 0.5, h * 0.92]) {
    g.add(kit.box(w + 0.3, 0.04, 0.045, kit.M.woodDark, 0, y, pz));
    g.add(kit.box(0.045, 0.04, d + 0.34, kit.M.woodDark, -px, y, 0));
    g.add(kit.box(0.045, 0.04, d + 0.34, kit.M.woodDark, px, y, 0));
  }
  g.add(kit.box(w * 0.6, 0.045, 0.24, kit.M.wood, w * 0.1, h * 0.92, pz));
  return g;
}

const SCAFFOLD_DIMS: Record<string, [number, number, number]> = {
  temple: [2.9, 1.9, 2.1],
  tholos: [2.6, 1.7, 2.6],
  stoa: [3.4, 1.7, 1.6],
  altar: [1.9, 1.3, 1.9],
  workshop: [2.1, 1.3, 1.7],
  lighthouse: [1.5, 2.2, 1.5],
  statuary: [2.8, 1.6, 1.6]
};

export function buildStructure(kit: AtlasKit, kind: string, progress: number): Built {
  const g = new THREE.Group();
  const flames: Flame[] = [];
  const flameLights: THREE.PointLight[] = [];
  const p = Math.max(0, Math.min(1, progress));
  const under = p < 0.999;

  const burn = (mesh: THREE.Mesh, light: THREE.PointLight): void => {
    flames.push({ mesh, x: mesh.position.x });
    flameLights.push(light);
  };

  if (kind === "temple") {
    const W = 2.6;
    const D = 1.8;
    const colH = 1.05;
    g.add(kit.box(W + 0.7, 0.14, D + 0.7, kit.M.marbleOld, 0, 0.07, 0));
    g.add(kit.box(W + 0.35, 0.14, D + 0.35, kit.M.marble, 0, 0.21, 0));
    const spots: [number, number][] = [];
    for (const sx of [-W / 2, 0, W / 2]) spots.push([sx, D / 2], [sx, -D / 2]);
    const nCols = Math.round(p * spots.length);
    spots.slice(0, nCols).forEach(([sx, sz]) => {
      const column = colShaft(kit, colH, 0.11);
      column.position.set(sx, 0.28, sz);
      g.add(column);
    });
    if (p > 0.75) g.add(kit.box(W + 0.5, 0.16, D + 0.5, kit.M.marble, 0, 0.28 + colH + 0.15, 0));
    if (!under) {
      // a triangular roof prism: the cylinder axis is the ridge line, so the
      // radial axes carry the height and the depth — never the length
      const prism = new THREE.Mesh(
        new THREE.CylinderGeometry(0.16, 0.16, W + 0.7, 3, 1),
        kit.M.terracotta
      );
      prism.rotation.z = Math.PI / 2;
      prism.scale.set(0.42 / 0.16, 1, (D + 0.8) / 0.32);
      prism.position.y = 0.28 + colH + 0.33;
      prism.castShadow = true;
      g.add(prism);
    }
  } else if (kind === "tholos") {
    const R = 1.05;
    const colH = 0.95;
    g.add(kit.cyl(R + 0.45, R + 0.6, 0.14, 18, kit.M.marbleOld, 0, 0.07, 0));
    g.add(kit.cyl(R + 0.25, R + 0.4, 0.14, 18, kit.M.marble, 0, 0.21, 0));
    const n = 7;
    const nCols = Math.round(p * n);
    for (let i = 0; i < nCols; i++) {
      const a = (i / n) * Math.PI * 2;
      const column = colShaft(kit, colH, 0.09);
      column.position.set(Math.cos(a) * R, 0.28, Math.sin(a) * R);
      g.add(column);
    }
    if (p > 0.8) {
      g.add(kit.cyl(R + 0.22, R + 0.22, 0.12, 18, kit.M.marble, 0, 0.28 + colH + 0.12, 0));
    }
    if (!under) {
      g.add(kit.cyl(0.02, R + 0.34, 0.6, 18, kit.M.terracotta, 0, 0.28 + colH + 0.48, 0));
    }
  } else if (kind === "stoa") {
    const W = 3.2;
    const D = 1.3;
    const colH = 1.0;
    g.add(kit.box(W + 0.4, 0.14, D + 0.4, kit.M.marbleOld, 0, 0.07, 0));
    const wallP = Math.min(1, p / 0.45);
    if (wallP > 0.05) {
      g.add(
        kit.box(
          W * wallP,
          1.15,
          0.16,
          kit.M.marbleOld,
          -(W - W * wallP) / 2,
          0.14 + 0.575,
          -D / 2
        )
      );
    }
    const nCols = Math.max(0, Math.round(((p - 0.35) / 0.65) * 5));
    for (let i = 0; i < Math.min(5, nCols); i++) {
      const column = colShaft(kit, colH, 0.09);
      column.position.set(-W / 2 + 0.3 + (i * (W - 0.6)) / 4, 0.14, D / 2 - 0.1);
      g.add(column);
    }
    if (!under) {
      const roof = kit.box(W + 0.5, 0.12, D + 0.6, kit.M.terracotta, 0, 0.14 + 1.28, -0.06);
      roof.rotation.x = 0.16;
      g.add(roof);
    }
  } else if (kind === "altar") {
    g.add(kit.box(1.7, 0.16, 1.7, kit.M.marbleOld, 0, 0.08, 0));
    if (p > 0.25) g.add(kit.box(1.3, 0.16, 1.3, kit.M.marble, 0, 0.24, 0));
    if (p > 0.5) g.add(kit.box(0.95, 0.55, 0.8, kit.M.marble, 0, 0.6, 0));
    if (p > 0.8) {
      g.add(kit.box(1.1, 0.1, 0.95, kit.M.marble, 0, 0.92, 0));
      g.add(kit.box(0.12, 0.22, 0.95, kit.M.marble, -0.49, 1.06, 0));
      g.add(kit.box(0.12, 0.22, 0.95, kit.M.marble, 0.49, 1.06, 0));
    }
    if (!under) {
      const flame = new THREE.Mesh(
        new THREE.ConeGeometry(0.14, 0.34, 6),
        new THREE.MeshBasicMaterial({ color: "#ffc873" })
      );
      flame.position.y = 1.18;
      g.add(flame);
      const glow = new THREE.PointLight("#ffb361", 0.8, 4);
      glow.position.y = 1.3;
      g.add(glow);
      burn(flame, glow);
    }
  } else if (kind === "workshop") {
    const W = 1.9;
    const D = 1.5;
    g.add(kit.box(W + 0.3, 0.12, D + 0.3, kit.M.marbleOld, 0, 0.06, 0));
    const wallH = 0.85 * Math.min(1, p / 0.6 + 0.15);
    g.add(kit.box(W, wallH, 0.14, kit.M.marbleOld, 0, 0.12 + wallH / 2, -D / 2));
    g.add(kit.box(0.14, wallH, D, kit.M.marbleOld, -W / 2, 0.12 + wallH / 2, 0));
    g.add(kit.box(0.14, wallH, D, kit.M.marbleOld, W / 2, 0.12 + wallH / 2, 0));
    if (p > 0.55) {
      g.add(
        kit.box(W * 0.5, wallH * 0.8, 0.12, kit.M.marbleOld, W * 0.23, 0.12 + wallH * 0.4, D / 2)
      );
    }
    if (!under) {
      const roof = kit.box(
        W + 0.5,
        0.1,
        D * 0.75,
        kit.M.terracottaDeep,
        0,
        0.12 + wallH + 0.16,
        -D * 0.14
      );
      roof.rotation.x = 0.22;
      g.add(roof);
      g.add(kit.cyl(0.1, 0.14, 0.4, 6, kit.M.terracotta, W * 0.3, 0.2, D * 0.32));
      g.add(kit.cyl(0.09, 0.12, 0.34, 6, kit.M.terracotta, W * 0.1, 0.17, D * 0.38));
    }
  }

  if (kind === "lighthouse") {
    // an exploration item: a watchtower rising stage by stage
    g.add(kit.cyl(0.85, 1.0, 0.24, 10, kit.M.marbleOld, 0, 0.12, 0));
    const drums = Math.max(1, Math.round(p * 4));
    for (let i = 0; i < drums; i++) {
      g.add(
        kit.cyl(
          0.62 - i * 0.09,
          0.68 - i * 0.09,
          0.62,
          10,
          kit.M.marble,
          0,
          0.24 + 0.31 + i * 0.62,
          0
        )
      );
    }
    if (p > 0.8) {
      g.add(kit.cyl(0.5, 0.42, 0.12, 10, kit.M.marbleOld, 0, 0.24 + drums * 0.62 + 0.06, 0));
    }
    if (!under) {
      const lampY = 0.24 + 4 * 0.62 + 0.3;
      g.add(kit.cyl(0.34, 0.34, 0.3, 8, kit.M.marble, 0, lampY, 0));
      const lamp = new THREE.Mesh(
        new THREE.SphereGeometry(0.16, 8, 8),
        new THREE.MeshBasicMaterial({ color: "#ffd98e" })
      );
      lamp.position.y = lampY + 0.1;
      g.add(lamp);
      g.add(kit.cyl(0.4, 0.02, 0.28, 8, kit.M.terracotta, 0, lampY + 0.36, 0));
      const glow = new THREE.PointLight("#ffca70", 0.9, 7);
      glow.position.y = lampY + 0.1;
      g.add(glow);
      burn(lamp, glow);
    }
  } else if (kind === "exedra") {
    // a planning item: a council place — curved bench, speaker's stone, stelae
    const R = 1.35;
    g.add(kit.cyl(R + 0.5, R + 0.65, 0.12, 16, kit.M.marbleOld, 0, 0.06, 0));
    const segs = Math.max(1, Math.round(p * 7));
    for (let i = 0; i < segs; i++) {
      const a = Math.PI * 0.15 + (i / 7) * Math.PI * 0.7 + Math.PI;
      const bench = kit.box(
        0.52,
        0.3,
        0.26,
        kit.M.marble,
        Math.cos(a) * R,
        0.27,
        Math.sin(a) * R
      );
      bench.rotation.y = -a + Math.PI / 2;
      g.add(bench);
      if (p > 0.6) {
        const back = kit.box(
          0.52,
          0.3,
          0.08,
          kit.M.marble,
          Math.cos(a) * (R + 0.12),
          0.55,
          Math.sin(a) * (R + 0.12)
        );
        back.rotation.y = -a + Math.PI / 2;
        g.add(back);
      }
    }
    // the speaker's stone
    if (p > 0.4) g.add(kit.cyl(0.26, 0.34, 0.5, 8, kit.M.marbleOld, 0, 0.37, 0.25));
    if (!under) {
      const omphalos = new THREE.Mesh(new THREE.SphereGeometry(0.22, 8, 8), kit.M.marble);
      omphalos.position.set(0, 0.75, 0.25);
      omphalos.castShadow = true;
      g.add(omphalos);
    }
  } else if (kind === "statuary") {
    // a new-worker item: figures being carved into being
    g.add(kit.box(2.6, 0.14, 1.4, kit.M.marbleOld, 0, 0.07, 0));
    const n = Math.max(1, Math.round(p * 3));
    for (let i = 0; i < n; i++) {
      const x = -0.85 + i * 0.85;
      g.add(kit.box(0.4, 0.34, 0.4, kit.M.marble, x, 0.31, 0));
      const figure = marbleFigure(kit);
      figure.position.set(x, 0.48, 0);
      figure.scale.setScalar(0.9 + i * 0.05);
      g.add(figure);
    }
    if (!under) {
      g.add(kit.box(2.8, 0.1, 0.14, kit.M.marble, 0, 1.35, -0.62));
      g.add(kit.cyl(0.06, 0.075, 1.25, 6, kit.M.marble, -1.25, 0.72, -0.62));
      g.add(kit.cyl(0.06, 0.075, 1.25, 6, kit.M.marble, 1.25, 0.72, -0.62));
    }
  } else if (kind === "garden") {
    // a personal item: a tended garden under a pergola
    g.add(kit.box(2.4, 0.1, 1.8, kit.M.marbleOld, 0, 0.05, 0));
    const posts: [number, number][] = [
      [-1.05, -0.75],
      [1.05, -0.75],
      [-1.05, 0.75],
      [1.05, 0.75]
    ];
    posts.forEach(([px, pz]) => g.add(kit.cyl(0.05, 0.06, 1.15, 6, kit.M.wood, px, 0.67, pz)));
    if (p > 0.4) {
      g.add(kit.box(2.4, 0.07, 0.1, kit.M.woodDark, 0, 1.28, -0.75));
      g.add(kit.box(2.4, 0.07, 0.1, kit.M.woodDark, 0, 1.28, 0.75));
    }
    const pots = Math.max(1, Math.round(p * 4));
    for (let i = 0; i < pots; i++) {
      const px = -0.8 + (i % 2) * 1.6;
      const pz = i < 2 ? -0.35 : 0.35;
      g.add(kit.cyl(0.16, 0.12, 0.26, 8, kit.M.terracotta, px, 0.23, pz));
      const bush = new THREE.Mesh(new THREE.IcosahedronGeometry(0.17, 0), kit.M.olive);
      bush.position.set(px, 0.46, pz);
      bush.castShadow = true;
      g.add(bush);
    }
    if (!under) {
      for (let i = 0; i < 4; i++) {
        g.add(kit.box(0.5, 0.07, 0.12, kit.M.olive, -0.9 + i * 0.6, 1.34, -0.75 + (i % 2) * 1.5));
      }
    }
  }

  if (["lighthouse", "exedra", "statuary", "garden"].includes(kind)) {
    if (under && p > 0.34 && kind !== "exedra" && kind !== "garden") {
      const dims = SCAFFOLD_DIMS[kind];
      if (dims) g.add(scaffold(kit, dims[0], dims[1], dims[2]));
    }
    return { group: g, flames, flameLights };
  }
  if (under && p > 0.34) {
    const dims = SCAFFOLD_DIMS[kind];
    if (dims) g.add(scaffold(kit, dims[0], dims[1], dims[2]));
  } else if (under) {
    // early days: dressed stones stacked and waiting beside the marked ground
    const rng = mulberry32(97);
    for (let i = 0; i < 5; i++) {
      const stone = kit.box(
        0.28 + rng() * 0.14,
        0.2,
        0.24 + rng() * 0.12,
        kit.M.marbleOld,
        1.1 + rng() * 0.5,
        0.1 + (i > 2 ? 0.21 : 0),
        0.5 - i * 0.32
      );
      stone.rotation.y = rng() * 0.5;
      g.add(stone);
    }
  }
  return { group: g, flames, flameLights };
}
