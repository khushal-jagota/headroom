// A project is an island.
//
// Land shape, ground colour, cliffs, vegetation and a signature mark all derive
// from the project's id, so the same project is the same place on every reload and
// can be recognised from across the water. Its accent rides the trim and the
// pennants. Its name stands carved on a gate stone at the shore, and hangs in the
// air above the island for anyone far enough out to need it.

import * as THREE from "three";
import type { AtlasProject } from "../model";
import { ACCENTS, C, mixHex } from "./palette";
import { AtlasKit } from "./materials";
import { hashCode, mulberry32 } from "./random";
import { PickIndex } from "./picking";

export type IslandTheme = {
  ground: readonly [string, string];
  cliff: string;
  pad: string;
  veg: string;
  shape: string;
  mark: string;
};

// Everything derives from the project id, so it is stable across reloads.
const THEMES = {
  grounds: [
    ["#c9b98e", "#b3ad74"],
    ["#9fae74", "#87995f"],
    ["#c7a179", "#b18a5c"],
    ["#b7b49a", "#98a077"]
  ] as const,
  cliffs: ["#84765f", "#6e6a66", "#8d6f58", "#7c7a70"],
  pads: ["#b0a488", "#a9a695", "#c0aa87", "#9c9788"],
  vegs: ["cypress", "olive", "mixed", "sparse"],
  shapes: ["round", "hex", "twoTier", "rocky"],
  marks: ["avenue", "stones", "jetty", "none"]
};

export function themeFor(project: AtlasProject): IslandTheme {
  const h = hashCode(project.id);
  const pick = <T>(list: readonly T[], salt: number): T => list[(h + salt * 13) % list.length];
  return {
    ground: pick(THEMES.grounds, 1),
    cliff: pick(THEMES.cliffs, 2),
    pad: pick(THEMES.pads, 3),
    veg: pick(THEMES.vegs, 4),
    shape: pick(THEMES.shapes, 5),
    mark: pick(THEMES.marks, 6)
  };
}

// ---------------- flora ----------------

export function cypressTree(kit: AtlasKit, scale: number): THREE.Group {
  const g = new THREE.Group();
  g.add(kit.cyl(0.05, 0.07, 0.3, 6, kit.M.trunk, 0, 0.15, 0));
  g.add(kit.cyl(0.02, 0.34, 1.5, 8, kit.M.cypress, 0, 1.0, 0));
  g.add(kit.cyl(0.0, 0.22, 0.9, 8, kit.M.cypressDark, 0, 1.9, 0));
  g.scale.setScalar(scale);
  return g;
}

export function oliveTree(kit: AtlasKit, scale: number): THREE.Group {
  const g = new THREE.Group();
  g.add(kit.cyl(0.06, 0.09, 0.5, 6, kit.M.trunk, 0, 0.25, 0));
  const crown = new THREE.Mesh(new THREE.IcosahedronGeometry(0.42, 0), kit.M.olive);
  crown.position.y = 0.72;
  crown.scale.y = 0.7;
  crown.castShadow = true;
  const bough = new THREE.Mesh(new THREE.IcosahedronGeometry(0.28, 0), kit.M.olive);
  bough.position.set(0.3, 0.58, 0.12);
  bough.castShadow = true;
  g.add(crown, bough);
  g.scale.setScalar(scale);
  return g;
}

// ---------------- carved lettering ----------------

function plaqueTexture(kit: AtlasKit, text: string): THREE.CanvasTexture {
  return kit.texture(512, 96, (g) => {
    g.clearRect(0, 0, 512, 96);
    g.font = "600 44px Iowan Old Style, Palatino, Georgia, serif";
    g.textAlign = "center";
    g.textBaseline = "middle";
    const label = text.toUpperCase().split("").join("  ");
    g.fillStyle = "rgba(58,52,40,0.85)";
    g.fillText(label, 256, 50);
    g.fillStyle = "rgba(255,252,240,0.35)";
    g.fillText(label, 256, 53);
  });
}

function nameTexture(kit: AtlasKit, text: string): THREE.CanvasTexture {
  return kit.texture(1024, 190, (g) => {
    g.font = "600 104px Iowan Old Style, Palatino, Georgia, serif";
    g.textAlign = "center";
    g.textBaseline = "middle";
    const label = text.toUpperCase().split("").join("  ");
    g.lineWidth = 10;
    g.strokeStyle = "rgba(20,26,38,0.55)";
    g.strokeText(label, 512, 95);
    g.fillStyle = "rgba(243,239,228,0.98)";
    g.fillText(label, 512, 95);
  });
}

export function pennantStandard(kit: AtlasKit, accent: string): THREE.Group {
  const g = new THREE.Group();
  g.add(kit.cyl(0.03, 0.04, 1.6, 6, kit.M.woodDark, 0, 0.8, 0));
  const flag = new THREE.Mesh(
    new THREE.PlaneGeometry(0.44, 0.22),
    kit.mat(accent, { roughness: 0.65, side: THREE.DoubleSide })
  );
  flag.position.set(0.235, 1.46, 0);
  flag.castShadow = true;
  g.add(flag);
  return g;
}

// ---------------- the ground, which only ever grows ----------------

export type IslandGround = {
  group: THREE.Group;
  foamA: THREE.Mesh;
  foamB: THREE.Mesh;
};

// The land and the water around it, at one radius. Everything here is a function of
// the radius alone, so when an island grows this is the whole of what is rebuilt —
// and the ground it replaces is released, never left behind. Its materials are its
// own, so releasing it takes nothing another part of the island is still using.
export function makeGround(
  kit: AtlasKit,
  index: PickIndex,
  project: AtlasProject,
  theme: IslandTheme,
  radius: number
): IslandGround {
  const g = new THREE.Group();
  const seg = theme.shape === "hex" ? 7 : 22;
  const groundM = kit.mat(theme.ground[0]);
  const cliffM = kit.mat(theme.cliff);

  // shoreline: beach slope into banded shallows, with living foam lines
  const beach = kit.cyl(
    radius + 0.25,
    radius + 1.5,
    1.15,
    seg,
    kit.mat(mixHex(theme.ground[0], "#e2d3ae", 0.5)),
    0,
    0.15,
    0
  );
  const wetSand = kit.cyl(
    radius + 1.45,
    radius + 2.1,
    0.5,
    seg,
    kit.mat(mixHex(theme.ground[0], "#8fa192", 0.55)),
    0,
    -0.62,
    0
  );
  const band1 = new THREE.Mesh(
    new THREE.CircleGeometry(radius + 3.6, 40),
    kit.mat("#6fb0b0", { roughness: 0.5 })
  );
  band1.rotation.x = -Math.PI / 2;
  band1.position.y = -0.855;
  const band2 = new THREE.Mesh(
    new THREE.CircleGeometry(radius + 6.2, 40),
    kit.mat(C.seaShallow, { roughness: 0.5 })
  );
  band2.rotation.x = -Math.PI / 2;
  band2.position.y = -0.875;
  const band3 = new THREE.Mesh(
    new THREE.CircleGeometry(radius + 9.5, 40),
    kit.mat("#3f8394", { roughness: 0.45 })
  );
  band3.rotation.x = -Math.PI / 2;
  band3.position.y = -0.89;
  const foamA = new THREE.Mesh(
    new THREE.RingGeometry(radius + 1.6, 0.18 + radius + 1.6, 44),
    kit.mat(C.foam, { roughness: 0.6, transparent: true, opacity: 0.85 })
  );
  foamA.rotation.x = -Math.PI / 2;
  foamA.position.y = -0.8;
  const foamB = new THREE.Mesh(
    new THREE.RingGeometry(radius + 2.5, 0.14 + radius + 2.5, 44),
    kit.mat(C.foam, { roughness: 0.6, transparent: true, opacity: 0.5 })
  );
  foamB.rotation.x = -Math.PI / 2;
  foamB.position.y = -0.84;
  // the land: an earthen brow over darker sea rock, tapering under water
  const brow = kit.cyl(
    radius + 0.28,
    radius - 0.2,
    0.8,
    seg,
    kit.mat(mixHex(theme.cliff, theme.ground[0], 0.4)),
    0,
    0.55,
    0
  );
  const rock = kit.cyl(radius - 0.18, radius - 1.6, 2.2, seg, cliffM, 0, -0.9, 0);
  const plat = kit.cyl(radius, radius + 0.28, 0.5, seg, groundM, 0, 0.78, 0);
  // the shallows and foam are sea, not ground: clicking them is a water click
  for (const wet of [wetSand, band1, band2, band3, foamA, foamB]) {
    index.tag(wet, { kind: "water" });
  }
  g.add(beach, wetSand, band1, band2, band3, foamA, foamB, brow, rock, plat);

  if (theme.shape === "twoTier") {
    g.add(
      kit.cyl(
        radius * 0.62,
        radius * 0.7,
        0.5,
        seg,
        groundM,
        -radius * 0.18,
        1.22,
        -radius * 0.2
      )
    );
  }
  if (theme.shape === "rocky") {
    const rr = mulberry32(hashCode(project.id) + 4);
    for (let i = 0; i < 4; i++) {
      const a = rr() * Math.PI * 2;
      const d = radius - 0.4 + rr() * 1.2;
      g.add(
        kit.cyl(
          0.5 + rr() * 0.7,
          0.8 + rr() * 0.8,
          1.6 + rr() * 1.4,
          7,
          cliffM,
          Math.cos(a) * d,
          0.4,
          Math.sin(a) * d
        )
      );
    }
  }

  return { group: g, foamA, foamB };
}

// ---------------- what grows on the ground ----------------

// Each growth draws its own trees, so the ones already standing are never drawn a
// second time and never move. The seed carries the generation, so the same growth
// of the same project grows the same wood twice.
const SCATTER_GENERATION_SALT = 9973;

// The scatter of one band of ground: ground patches through it, a treeline just
// inside the shore, and — on a sparse island — its stones. Called with fromRadius 0
// for the island's first drawing, and with the old shore afterwards, so only ground
// the human has never seen is planted.
export function makeScatter(
  kit: AtlasKit,
  project: AtlasProject,
  theme: IslandTheme,
  fromRadius: number,
  radius: number,
  generation: number
): THREE.Group {
  const g = new THREE.Group();
  const rng = mulberry32(hashCode(project.id) + 11 + generation * SCATTER_GENERATION_SALT);

  // ground patches lie evenly out from the middle: the new band takes its share
  const patchInner = Math.max(0, fromRadius - 1.4);
  const patchOuter = Math.max(patchInner, radius - 1.4);
  const patchCount = Math.round(
    (radius * 1.6 * (patchOuter - patchInner)) / Math.max(0.001, radius - 1.4)
  );
  for (let i = 0; i < patchCount; i++) {
    const a = rng() * Math.PI * 2;
    const r = patchInner + rng() * (patchOuter - patchInner);
    const patch = new THREE.Mesh(
      new THREE.CircleGeometry(0.7 + rng() * 1.7, 10),
      kit.mat(theme.ground[1], { roughness: 1 })
    );
    patch.rotation.x = -Math.PI / 2;
    patch.rotation.z = rng() * Math.PI;
    patch.scale.y = 0.55 + rng() * 0.4;
    patch.position.set(Math.cos(a) * r, 1.004 + rng() * 0.004, Math.sin(a) * r);
    patch.receiveShadow = true;
    g.add(patch);
  }

  // the treeline follows the shore: when the shore moves out a new one grows at it,
  // and the old one stays where it stands, an inland wood
  const treeCount = theme.veg === "sparse" ? 6 : Math.round(radius * 1.7);
  for (let i = 0; i < treeCount; i++) {
    const a = rng() * Math.PI * 2;
    const r = radius - 1.0 - rng() * 1.1;
    // leave the way to the gate stone clear
    if (Math.abs(((a - 0.62 + Math.PI) % (Math.PI * 2)) - Math.PI) < 0.5) continue;
    // nothing new is planted on ground that was already drawn
    if (r < fromRadius) continue;
    const tall =
      theme.veg === "cypress" ? rng() > 0.2 : theme.veg === "olive" ? rng() > 0.8 : rng() > 0.5;
    const tree = tall
      ? cypressTree(kit, 0.55 + rng() * 0.5)
      : oliveTree(kit, 0.65 + rng() * 0.4);
    tree.position.set(Math.cos(a) * r, 1.0, Math.sin(a) * r);
    tree.rotation.y = rng() * Math.PI;
    g.add(tree);
  }

  if (theme.veg === "sparse") {
    const stoneInner = Math.max(0, fromRadius - 1.2);
    const stoneOuter = Math.max(stoneInner, radius - 1.2);
    const stoneCount = Math.round(
      (7 * (stoneOuter - stoneInner)) / Math.max(0.001, radius - 1.2)
    );
    const stoneM = kit.mat(theme.cliff);
    for (let i = 0; i < stoneCount; i++) {
      const a = rng() * Math.PI * 2;
      const r = stoneInner + rng() * (stoneOuter - stoneInner);
      const stone = new THREE.Mesh(new THREE.IcosahedronGeometry(0.14 + rng() * 0.22, 0), stoneM);
      stone.position.set(Math.cos(a) * r, 1.05, Math.sin(a) * r);
      stone.castShadow = true;
      g.add(stone);
    }
  }

  return g;
}

// ---------------- the island itself ----------------

export type IslandParts = {
  group: THREE.Group;
  // the radius-dependent ground, held apart so growth can replace exactly it
  ground: THREE.Group;
  precinct: THREE.Group;
  theme: IslandTheme;
  accent: string;
  foamA: THREE.Mesh;
  foamB: THREE.Mesh;
  namePlate: THREE.Sprite;
};

// The whole island at the radius it is first seen at. The gate stone, the signature
// mark and the nameplate are drawn once here and never again: they stand where the
// human first saw them even after the shore has moved out past them.
export function makeIsland(
  kit: AtlasKit,
  index: PickIndex,
  project: AtlasProject,
  radius: number
): IslandParts {
  const theme = themeFor(project);
  const accent = ACCENTS[project.slot % ACCENTS.length];
  const g = new THREE.Group();

  const ground = makeGround(kit, index, project, theme, radius);
  g.add(ground.group);
  g.add(makeScatter(kit, project, theme, 0, radius, 0));

  // the island's signature mark
  if (theme.mark === "avenue") {
    for (let i = 0; i < 4; i++) {
      const left = cypressTree(kit, 0.7);
      left.position.set(-1.5, 1.0, radius - 0.7 - i * 1.1);
      const right = cypressTree(kit, 0.7);
      right.position.set(1.5, 1.0, radius - 0.7 - i * 1.1);
      g.add(left, right);
    }
  } else if (theme.mark === "stones") {
    const cliffM = kit.mat(theme.cliff);
    for (let i = 0; i < 5; i++) {
      const a = Math.PI * 0.15 + i * 0.28;
      const stone = kit.box(
        0.3,
        0.9 + (i % 2) * 0.3,
        0.22,
        cliffM,
        Math.cos(a) * (radius - 1.1),
        1.4,
        Math.sin(a) * (radius - 1.1)
      );
      stone.rotation.y = a;
      g.add(stone);
    }
  } else if (theme.mark === "jetty") {
    const jetty = new THREE.Group();
    jetty.position.set(0, 0, radius - 0.3);
    jetty.add(kit.box(1.0, 0.1, 3.4, kit.M.woodDark, 0, 0.95, 1.4));
    for (const dz of [0.4, 1.6, 2.8]) {
      jetty.add(kit.cyl(0.05, 0.05, 1.6, 5, kit.M.wood, -0.4, 0.2, dz));
      jetty.add(kit.cyl(0.05, 0.05, 1.6, 5, kit.M.wood, 0.4, 0.2, dz));
    }
    g.add(jetty);
  }

  // the project's name: a carved gate stone at the island's front
  const gate = new THREE.Group();
  gate.position.set(0, 1.0, radius - 1.0);
  gate.add(kit.box(2.9, 0.5, 0.35, kit.mat(theme.pad), 0, 0.25, 0));
  gate.add(kit.box(3.1, 0.12, 0.45, kit.M.marbleOld, 0, 0.56, 0));
  gate.add(kit.box(3.1, 0.07, 0.45, kit.mat(accent), 0, 0.64, 0));
  const plaque = new THREE.Mesh(
    new THREE.PlaneGeometry(2.7, 0.5),
    new THREE.MeshBasicMaterial({ map: plaqueTexture(kit, project.name), transparent: true })
  );
  plaque.position.set(0, 0.3, 0.19);
  gate.add(plaque);
  const standardLeft = pennantStandard(kit, accent);
  standardLeft.position.set(-1.75, 0, 0);
  const standardRight = pennantStandard(kit, accent);
  standardRight.position.set(1.75, 0, 0);
  gate.add(standardLeft, standardRight);
  g.add(gate);

  // the island's name, readable from far out across the water
  const namePlate = new THREE.Sprite(
    new THREE.SpriteMaterial({
      map: nameTexture(kit, project.name),
      transparent: true,
      opacity: 0,
      depthTest: false
    })
  );
  namePlate.scale.set(9, 1.67, 1);
  namePlate.position.y = 8.5;
  namePlate.renderOrder = 15;
  g.add(namePlate);

  const precinct = new THREE.Group();
  precinct.position.y = 0.99;
  g.add(precinct);

  // the island's own ground: clicking anywhere on it travels to this project
  index.tag(g, { kind: "island", projectId: project.id });

  return {
    group: g,
    ground: ground.group,
    precinct,
    theme,
    accent,
    foamA: ground.foamA,
    foamB: ground.foamB,
    namePlate
  };
}
