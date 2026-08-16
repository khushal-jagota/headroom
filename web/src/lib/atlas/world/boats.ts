// Small Aegean craft under sail among the islands — life on the water.
//
// Routes stay outside every island (the world extent already includes island
// radii; it may change between frames, so orbit radii are recomputed from it every
// step). Hulls are double-ended shells lofted from stations (sheer rising to bow
// and stern, rockered keel), with a gunwale cap, deck, curved stem and aphlaston
// posts, a painted bow eye, and a steering oar. Sails are planes with the billow
// baked into the vertices, hung from a yard with rigging lines; each boat flies an
// accent pennant. Wakes are foam sprites trailed analytically behind the stern.
// All geometry is built once.

import * as THREE from "three";
import { ACCENTS, C, mixHex } from "./palette";
import { AtlasKit } from "./materials";
import { SEA } from "./tuning";

const WATER_Y = SEA.y;

type BoatSpec = {
  len: number;
  halfBeam: number;
  depth: number;
  mastH: number;
  sailW?: number;
  sailH?: number;
  belly?: number;
  brace?: number;
  furled?: boolean;
  band: string;
  pennant: string;
  stripe?: string;
  dir: number;
  speed: number;
  phase: number;
  lift: number;
};

const SPECS: BoatSpec[] = [
  {
    len: 2.85,
    halfBeam: 0.42,
    depth: 0.27,
    mastH: 2.0,
    sailW: 1.5,
    sailH: 1.35,
    belly: 0.4,
    brace: 0.45,
    band: ACCENTS[0],
    pennant: ACCENTS[0],
    dir: 1,
    speed: 0.02,
    phase: 0.6,
    lift: 0.1
  },
  {
    len: 2.45,
    halfBeam: 0.38,
    depth: 0.24,
    mastH: 1.75,
    sailW: 1.25,
    sailH: 1.15,
    belly: 0.33,
    brace: -0.5,
    band: ACCENTS[1],
    pennant: ACCENTS[1],
    stripe: ACCENTS[2],
    dir: -1,
    speed: 0.016,
    phase: 2.9,
    lift: 0.09
  },
  {
    len: 2.0,
    halfBeam: 0.34,
    depth: 0.2,
    mastH: 1.25,
    furled: true,
    band: ACCENTS[3],
    pennant: ACCENTS[2],
    dir: 1,
    speed: 0.026,
    phase: 4.6,
    lift: 0.08
  }
];

type BoatMaterials = {
  hull: THREE.MeshStandardMaterial;
  rim: THREE.MeshStandardMaterial;
  deck: THREE.MeshStandardMaterial;
  rope: THREE.MeshStandardMaterial;
  cloth: THREE.MeshStandardMaterial;
  eyeWhite: THREE.MeshStandardMaterial;
  eyeDark: THREE.MeshStandardMaterial;
};

function boatMaterials(kit: AtlasKit): BoatMaterials {
  return {
    hull: kit.mat("#8d6e4a"),
    rim: kit.mat("#5c4831"),
    deck: kit.mat("#967a55", { roughness: 0.95 }),
    rope: kit.mat("#544636", { flatShading: false }),
    cloth: kit.mat("#e8e0cc", { side: THREE.DoubleSide, roughness: 1, flatShading: false }),
    eyeWhite: kit.mat(C.marble, { roughness: 0.6, flatShading: false }),
    eyeDark: kit.mat(C.crack, { flatShading: false })
  };
}

function stripedClothMaterial(kit: AtlasKit, accent: string): THREE.MeshStandardMaterial {
  const texture = kit.texture(8, 64, (g) => {
    g.fillStyle = "#e8e0cc";
    g.fillRect(0, 0, 8, 64);
    g.fillStyle = accent;
    g.globalAlpha = 0.55;
    g.fillRect(0, 12, 8, 7);
    g.fillRect(0, 30, 8, 7);
    g.fillRect(0, 48, 8, 7);
  });
  texture.colorSpace = THREE.SRGBColorSpace;
  return kit.mat("#ffffff", {
    map: texture,
    side: THREE.DoubleSide,
    roughness: 1,
    flatShading: false
  });
}

// ---------------- hull lofting ----------------

type Point = [number, number, number];

// Station curves for a double-ended hull. t: 0 stern .. 1 bow.
function stationOf(
  t: number,
  halfBeam: number,
  depth: number
): { w: number; sheer: number; keel: number } {
  const e = 2 * t - 1;
  const w = halfBeam * Math.pow(Math.max(Math.sin(Math.PI * t), 0), 0.72);
  const sheer = 0.13 + 0.11 * Math.pow(Math.abs(e), 2.0) * (e > 0 ? 1.3 : 1.05);
  const keel = -depth * Math.pow(Math.max(Math.sin(Math.PI * (0.06 + 0.88 * t)), 0.02), 0.9);
  return { w, sheer, keel };
}

function pushTri(arr: number[], a: Point, b: Point, c: Point): void {
  arr.push(a[0], a[1], a[2], b[0], b[1], b[2], c[0], c[1], c[2]);
}

function pushQuad(arr: number[], a: Point, b: Point, c: Point, d: Point): void {
  pushTri(arr, a, b, c);
  pushTri(arr, a, c, d);
}

function geoFrom(arr: number[]): THREE.BufferGeometry {
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(arr, 3));
  g.computeVertexNormals();
  return g;
}

// Shell: rings of NK points per station, port gunwale over keel to starboard.
function hullShellGeometry(len: number, halfBeam: number, depth: number): THREE.BufferGeometry {
  const NS = 11;
  const NK = 9;
  const rings: Point[][] = [];
  for (let i = 0; i < NS; i++) {
    const t = i / (NS - 1);
    const { w, sheer, keel } = stationOf(t, halfBeam, depth);
    const z = (t - 0.5) * len;
    const ring: Point[] = [];
    for (let k = 0; k < NK; k++) {
      const phi = (k / (NK - 1)) * Math.PI;
      const cs = Math.cos(phi);
      const x = w * Math.sign(cs) * Math.pow(Math.abs(cs), 0.78);
      const y = sheer + (keel - sheer) * Math.pow(Math.sin(phi), 1.15);
      ring.push([x, y, z]);
    }
    rings.push(ring);
  }
  const v: number[] = [];
  for (let i = 0; i < NS - 1; i++) {
    for (let k = 0; k < NK - 1; k++) {
      pushQuad(v, rings[i][k], rings[i][k + 1], rings[i + 1][k + 1], rings[i + 1][k]);
    }
  }
  return geoFrom(v);
}

// Deck: strip laid between the gunwales, slightly below the sheer line.
function deckGeometry(len: number, halfBeam: number, depth: number): THREE.BufferGeometry {
  const NS = 11;
  const v: number[] = [];
  let prev: Point[] | null = null;
  for (let i = 0; i < NS; i++) {
    const t = i / (NS - 1);
    const { w, sheer } = stationOf(t, halfBeam, depth);
    const z = (t - 0.5) * len;
    const y = sheer - 0.045;
    const cur: Point[] = [
      [-w * 0.96, y, z],
      [w * 0.96, y, z]
    ];
    if (prev) pushQuad(v, prev[0], prev[1], cur[1], cur[0]);
    prev = cur;
  }
  return geoFrom(v);
}

// Gunwale cap: flat rim strips riding just above the sheer, both sides.
function rimGeometry(len: number, halfBeam: number, depth: number): THREE.BufferGeometry {
  const NS = 11;
  const v: number[] = [];
  const dropped = (p: Point): Point => [p[0], p[1] - 0.05, p[2]];
  let prev: Point[] | null = null;
  for (let i = 0; i < NS; i++) {
    const t = i / (NS - 1);
    const { w, sheer } = stationOf(t, halfBeam, depth);
    const z = (t - 0.5) * len;
    const y = sheer + 0.014;
    const out = w * 1.03 + 0.015;
    const inn = Math.max(w - 0.055, 0);
    const cur: Point[] = [
      [-out, y, z],
      [-inn, y, z],
      [inn, y, z],
      [out, y, z]
    ];
    if (prev) {
      pushQuad(v, prev[0], prev[1], cur[1], cur[0]); /* port cap */
      pushQuad(v, cur[3], cur[2], prev[2], prev[3]); /* starboard cap */
      pushQuad(v, prev[0], cur[0], dropped(cur[0]), dropped(prev[0])); /* port outer lip */
      pushQuad(v, dropped(cur[3]), cur[3], prev[3], dropped(prev[3])); /* starboard outer lip */
    }
    prev = cur;
  }
  return geoFrom(v);
}

// Painted accent band along the topsides, offset just off the shell.
function bandGeometry(len: number, halfBeam: number, depth: number): THREE.BufferGeometry {
  const NS = 11;
  const v: number[] = [];
  let prevP: Point[] | null = null;
  let prevS: Point[] | null = null;
  for (let i = 0; i < NS; i++) {
    const t = i / (NS - 1);
    const { w, sheer } = stationOf(t, halfBeam, depth);
    const z = (t - 0.5) * len;
    const x = w * 0.995 + 0.012;
    const y0 = sheer - 0.1;
    const y1 = sheer - 0.045;
    const curP: Point[] = [
      [-x, y0, z],
      [-x, y1, z]
    ];
    const curS: Point[] = [
      [x, y0, z],
      [x, y1, z]
    ];
    if (prevP && prevS) {
      pushQuad(v, prevP[0], prevP[1], curP[1], curP[0]);
      pushQuad(v, curS[0], curS[1], prevS[1], prevS[0]);
    }
    prevP = curP;
    prevS = curS;
  }
  return geoFrom(v);
}

// ---------------- fittings ----------------

function rope(
  materials: BoatMaterials,
  ax: number,
  ay: number,
  az: number,
  bx: number,
  by: number,
  bz: number,
  r = 0.008
): THREE.Mesh {
  const a = new THREE.Vector3(ax, ay, az);
  const b = new THREE.Vector3(bx, by, bz);
  const d = b.clone().sub(a);
  const mesh = new THREE.Mesh(
    new THREE.CylinderGeometry(r, r, d.length(), 3),
    materials.rope
  );
  mesh.position.copy(a).addScaledVector(d, 0.5);
  mesh.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), d.normalize());
  return mesh;
}

function curvedPost(materials: BoatMaterials, points: Point[], r: number): THREE.Mesh {
  const curve = new THREE.CatmullRomCurve3(points.map((p) => new THREE.Vector3(...p)));
  return new THREE.Mesh(new THREE.TubeGeometry(curve, 8, r, 5, false), materials.rim);
}

function bowEye(
  materials: BoatMaterials,
  side: number,
  len: number,
  halfBeam: number,
  depth: number
): THREE.Group {
  const t = 0.86;
  const { w, sheer } = stationOf(t, halfBeam, depth);
  const g = new THREE.Group();
  const white = new THREE.Mesh(new THREE.CircleGeometry(0.052, 10), materials.eyeWhite);
  const pupil = new THREE.Mesh(new THREE.CircleGeometry(0.021, 8), materials.eyeDark);
  pupil.position.z = 0.006;
  g.add(white, pupil);
  g.position.set(side * (w * 0.92 + 0.012), sheer - 0.085, (t - 0.5) * len);
  const n = new THREE.Vector3(side, 0.28, 0.42).normalize();
  g.lookAt(g.position.clone().add(n));
  return g;
}

function steeringOar(
  materials: BoatMaterials,
  side: number,
  len: number,
  halfBeam: number,
  depth: number
): THREE.Group {
  const { sheer } = stationOf(0.12, halfBeam, depth);
  const g = new THREE.Group();
  const shaft = new THREE.Mesh(new THREE.CylinderGeometry(0.016, 0.014, 0.78, 5), materials.hull);
  shaft.position.y = -0.26;
  const blade = new THREE.Mesh(new THREE.BoxGeometry(0.03, 0.3, 0.09), materials.hull);
  blade.position.y = -0.62;
  g.add(shaft, blade);
  g.position.set(side * (halfBeam * 0.55), sheer + 0.02, -len / 2 + 0.32);
  g.rotation.z = side * 0.38;
  g.rotation.x = -0.14;
  return g;
}

function sailGeometry(w: number, h: number, belly: number): THREE.BufferGeometry {
  const g = new THREE.PlaneGeometry(w, h, 8, 8);
  const p = g.attributes.position;
  for (let i = 0; i < p.count; i++) {
    const u = Math.min(Math.max(p.getX(i) / w + 0.5, 0), 1);
    /* 1 at head (yard), 0 at foot */
    const v = Math.min(Math.max(p.getY(i) / h + 0.5, 0), 1);
    const drop = Math.pow(1 - v, 0.65);
    p.setZ(i, belly * Math.sin(Math.PI * u) * (0.12 + 0.88 * drop));
    p.setY(i, p.getY(i) + 0.07 * h * (1 - v) * Math.pow(Math.abs(2 * u - 1), 2.2));
  }
  /* hang from the head */
  g.translate(0, -h / 2, 0);
  g.computeVertexNormals();
  return g;
}

function pennantMesh(kit: AtlasKit, accent: string): THREE.Mesh {
  const g = new THREE.BufferGeometry();
  g.setAttribute(
    "position",
    new THREE.Float32BufferAttribute([0, 0, 0, 0, 0.095, 0, -0.32, 0.048, 0], 3)
  );
  g.computeVertexNormals();
  return new THREE.Mesh(g, kit.mat(accent, { side: THREE.DoubleSide, flatShading: false }));
}

// ---------------- one boat ----------------

type Boat = {
  group: THREE.Group;
  spec: BoatSpec;
  index: number;
  sail: THREE.Mesh | null;
  pennant: THREE.Mesh;
  wake: THREE.Sprite[];
};

function makeBoat(kit: AtlasKit, materials: BoatMaterials, spec: BoatSpec): Omit<Boat, "index" | "wake"> {
  const { len, halfBeam, depth, mastH } = spec;
  const g = new THREE.Group();

  const shell = new THREE.Mesh(hullShellGeometry(len, halfBeam, depth), materials.hull);
  const deck = new THREE.Mesh(deckGeometry(len, halfBeam, depth), materials.deck);
  const rim = new THREE.Mesh(rimGeometry(len, halfBeam, depth), materials.rim);
  const band = new THREE.Mesh(
    bandGeometry(len, halfBeam, depth),
    kit.mat(mixHex(spec.band, "#ffffff", 0.22), { roughness: 0.75 })
  );
  const keel = new THREE.Mesh(new THREE.BoxGeometry(0.04, 0.06, len * 0.58), materials.rim);
  keel.position.y = -depth - 0.01;
  g.add(shell, deck, rim, band, keel);

  /* stem (bow) and aphlaston (stern) posts */
  const bowSheer = stationOf(1, halfBeam, depth).sheer;
  const sternSheer = stationOf(0, halfBeam, depth).sheer;
  g.add(
    curvedPost(
      materials,
      [
        [0, -depth * 0.5, len / 2 - 0.18],
        [0, 0.02, len / 2 - 0.02],
        [0, bowSheer + 0.1, len / 2 + 0.03]
      ],
      0.028
    )
  );
  g.add(
    curvedPost(
      materials,
      [
        [0, -depth * 0.5, -len / 2 + 0.18],
        [0, 0.04, -len / 2 + 0.01],
        [0, sternSheer + 0.16, -len / 2 - 0.05],
        [0, sternSheer + 0.3, -len / 2 + 0.02],
        [0, sternSheer + 0.34, -len / 2 + 0.12]
      ],
      0.024
    )
  );

  g.add(
    bowEye(materials, 1, len, halfBeam, depth),
    bowEye(materials, -1, len, halfBeam, depth)
  );
  g.add(steeringOar(materials, 1, len, halfBeam, depth));

  /* mast, yard, sail, rigging */
  const mastZ = len * 0.06;
  g.add(kit.cyl(0.024, 0.032, mastH, 6, materials.hull, 0, mastH / 2 - 0.02, mastZ));
  const mastTop = mastH - 0.02;

  const rig = new THREE.Group();
  let sail: THREE.Mesh | null = null;
  if (!spec.furled) {
    const yardY = mastTop - 0.16;
    const yardLen = (spec.sailW ?? 1) * 1.18;
    /* yard braced round to the wind */
    const brace = spec.brace ?? 0.45;
    const tilt = 0.05;
    const yardGroup = new THREE.Group();
    yardGroup.position.set(0, yardY, mastZ);
    yardGroup.rotation.y = brace;
    const yard = new THREE.Mesh(
      new THREE.CylinderGeometry(0.02, 0.02, yardLen, 5),
      materials.rim
    );
    yard.rotation.z = Math.PI / 2 + tilt;
    sail = new THREE.Mesh(
      sailGeometry(spec.sailW ?? 1, spec.sailH ?? 1, spec.belly ?? 0.3),
      spec.stripe ? stripedClothMaterial(kit, spec.stripe) : materials.cloth
    );
    sail.position.set(0, -0.02, 0.04);
    sail.rotation.z = tilt;
    yardGroup.add(yard, sail);
    /* braces run from the (braced, tilted) yard tips down to the quarters */
    const hx = (Math.cos(tilt) * yardLen) / 2;
    const hy = (Math.sin(tilt) * yardLen) / 2;
    const tip = (sgn: number): Point => [
      sgn * hx * Math.cos(brace),
      yardY + sgn * hy,
      mastZ - sgn * hx * Math.sin(brace)
    ];
    const tp = tip(-1);
    const ts = tip(1);
    rig.add(
      /* forestay */
      rope(materials, 0, mastTop, mastZ, 0, bowSheer + 0.08, len / 2 - 0.02),
      rope(materials, tp[0], tp[1], tp[2], -halfBeam * 0.5, sternSheer + 0.02, -len / 2 + 0.4),
      rope(materials, ts[0], ts[1], ts[2], halfBeam * 0.5, sternSheer + 0.02, -len / 2 + 0.4)
    );
    rig.add(yardGroup);
  } else {
    /* furled: yard lowered fore-aft along the boat, sail rolled on it, oars out */
    const yardLen = len * 0.6;
    const stow = new THREE.Group();
    stow.position.set(0, mastH * 0.42, mastZ);
    /* along the boat, bow end a touch up */
    stow.rotation.x = Math.PI / 2 - 0.12;
    const yard = new THREE.Mesh(
      new THREE.CylinderGeometry(0.018, 0.018, yardLen, 5),
      materials.rim
    );
    const roll = new THREE.Mesh(
      new THREE.CylinderGeometry(0.042, 0.042, yardLen * 0.86, 6),
      materials.cloth
    );
    roll.position.set(0.02, 0, 0.03);
    stow.add(yard, roll);
    for (let i = 0; i < 3; i++) {
      const tie = new THREE.Mesh(new THREE.TorusGeometry(0.05, 0.007, 4, 8), materials.rope);
      tie.rotation.x = Math.PI / 2;
      tie.position.set(0.02, (i - 1) * yardLen * 0.3, 0.03);
      stow.add(tie);
    }
    rig.add(stow);
    rig.add(rope(materials, 0, mastTop, mastZ, 0, bowSheer + 0.06, len / 2 - 0.04));
    /* two oars per side, shipped at an easy angle */
    for (const side of [-1, 1]) {
      for (let k = 0; k < 2; k++) {
        const oar = new THREE.Group();
        const shaft = new THREE.Mesh(
          new THREE.CylinderGeometry(0.013, 0.012, 0.9, 5),
          materials.hull
        );
        shaft.position.y = -0.28;
        const blade = new THREE.Mesh(new THREE.BoxGeometry(0.025, 0.26, 0.07), materials.hull);
        blade.position.y = -0.66;
        oar.add(shaft, blade);
        const t = 0.42 + k * 0.22;
        const station = stationOf(t, halfBeam, depth);
        oar.position.set(side * (station.w * 0.9), station.sheer + 0.02, (t - 0.5) * len);
        oar.rotation.z = side * 0.94;
        oar.rotation.x = 0.12 - k * 0.2;
        rig.add(oar);
      }
    }
  }
  g.add(rig);

  const pennant = pennantMesh(kit, spec.pennant);
  pennant.position.set(0, mastTop + 0.01, mastZ);
  g.add(pennant);

  g.traverse((o) => {
    if ((o as THREE.Mesh).isMesh) {
      o.castShadow = true;
      o.receiveShadow = true;
    }
  });
  return { group: g, spec, sail, pennant };
}

// ---------------- wake ----------------

const WAKE_N = 8;

function makeWake(kit: AtlasKit, scaleBase: number): { group: THREE.Group; sprites: THREE.Sprite[] } {
  const group = new THREE.Group();
  const sprites: THREE.Sprite[] = [];
  /* j=0 is the stern churn, the rest trail */
  for (let j = 0; j <= WAKE_N; j++) {
    const sprite = new THREE.Sprite(
      new THREE.SpriteMaterial({
        map: kit.tex.foam,
        transparent: true,
        depthWrite: false,
        opacity: 0
      })
    );
    const s = scaleBase * (j === 0 ? 0.65 : 0.55 + j * 0.13);
    sprite.scale.set(s, s * 0.62, 1);
    group.add(sprite);
    sprites.push(sprite);
  }
  return { group, sprites };
}

// ---------------- the fleet ----------------

export type Fleet = { step: (time: number, worldExtent: number) => void };

export function createFleet(kit: AtlasKit, world: THREE.Object3D): Fleet {
  const materials = boatMaterials(kit);
  const boats: Boat[] = SPECS.map((spec, index) => {
    const built = makeBoat(kit, materials, spec);
    built.group.rotation.order = "YXZ";
    const wake = makeWake(kit, spec.halfBeam * 2.1);
    world.add(built.group, wake.group);
    return { ...built, index, wake: wake.sprites };
  });

  return {
    step(time: number, worldExtent: number): void {
      for (const boat of boats) {
        const s = boat.spec;
        const base = worldExtent + 7.5 + boat.index * 2.6;
        const a = s.dir * time * s.speed + s.phase;
        const rAt = (ang: number): number => base + 1.2 * Math.sin(2 * ang + s.phase);
        const r = rAt(a);
        const x = Math.cos(a) * r;
        const z = Math.sin(a) * r;
        const bob = 0.045 * Math.sin(time * 0.85 + s.phase * 1.7);
        boat.group.position.set(x, WATER_Y + s.lift + bob, z);
        /* heading from the velocity along the orbit */
        const da = s.dir;
        const vx = -Math.sin(a) * r * da;
        const vz = Math.cos(a) * r * da;
        boat.group.rotation.y = Math.atan2(vx, vz);
        /* pitch over swell */
        boat.group.rotation.x = 0.03 * Math.sin(time * 0.72 + s.phase + 1.3);
        /* heel into course */
        boat.group.rotation.z = 0.055 * s.dir + 0.025 * Math.sin(time * 1.05 + s.phase);
        /* sail breathing and pennant flutter */
        if (boat.sail) boat.sail.scale.z = 1 + 0.07 * Math.sin(time * 1.3 + s.phase);
        boat.pennant.rotation.y = 0.22 * Math.sin(time * 2.6 + s.phase * 2);
        /* wake: foam trailed along the path just sailed */
        const spacing = 0.22 + 0.05 * boat.index;
        for (let j = 0; j < boat.wake.length; j++) {
          /* arc back, radians */
          const back = (s.len * 0.55 + j * spacing) / base;
          const aj = a - s.dir * back;
          const rj = rAt(aj);
          const sway = 0.05 * j * Math.sin(j * 2.6 + s.phase * 3.1);
          boat.wake[j].position.set(
            Math.cos(aj) * rj + sway,
            WATER_Y + 0.03,
            Math.sin(aj) * rj - sway * 0.4
          );
          boat.wake[j].material.opacity =
            (j === 0 ? 0.45 : 0.36 * (1 - j / (WAKE_N + 1.5))) *
            (0.8 + 0.2 * Math.sin(time * 1.6 + j * 1.9 + s.phase));
        }
      }
    }
  };
}
