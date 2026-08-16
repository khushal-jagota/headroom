// A Sprint Item is also an overseer: a marble statue of the Item itself.
//
// Categorically not a living worker — a figure on a pedestal beside its standard
// at the pad's edge. The statue carries the Item's state so it reads from any
// distance, where workers are not drawn at all:
//   - a slate beacon burns above it when the crew needs the human (slate means
//     that and nothing else),
//   - the statue is gilded when every work stands finished,
//   - fire and smoke rise around the structure when a work is in trouble.

import * as THREE from "three";
import type { AtlasItem, AtlasTicket } from "../model";
import { AtlasKit } from "./materials";
import { PickIndex } from "./picking";

export type PlumeMote = {
  sprite: THREE.Sprite;
  base: number;
  step: number;
  index: number;
  scale: number;
};

export type OverseerFigure = {
  group: THREE.Group;
  orb: THREE.Sprite;
  flag: THREE.Mesh;
  finial: THREE.Mesh;
  statueMaterial: THREE.MeshStandardMaterial;
  goldMaterial: THREE.MeshStandardMaterial;
  statueParts: THREE.Mesh[];
  // a crew at work flies the standard; a live supervisor conversation pulses the finial
  active: boolean;
  supervisorActive: boolean;
  gilded: boolean;
};

export function makeOverseer(
  kit: AtlasKit,
  index: PickIndex,
  itemId: string,
  accent: string
): OverseerFigure {
  const g = new THREE.Group();
  const statueMaterial = kit.mat("#eae4d2", { roughness: 0.6 });
  const goldMaterial = kit.mat("#c9a84c", { roughness: 0.45, metalness: 0.25 });

  // pedestal: base, die with the project's band, cap
  g.add(kit.box(1.1, 0.28, 1.1, kit.M.marbleOld, 0, 0.14, 0));
  g.add(kit.box(0.78, 0.95, 0.78, kit.M.marble, 0, 0.75, 0));
  g.add(kit.box(0.82, 0.1, 0.82, kit.mat(accent), 0, 0.42, 0));
  g.add(kit.box(0.95, 0.14, 0.95, kit.M.marble, 0, 1.28, 0));

  // the statue: contrapposto, one arm raised toward the work
  const figure = new THREE.Group();
  figure.position.y = 1.35;
  const legL = kit.box(0.09, 0.42, 0.11, statueMaterial, -0.08, 0.21, 0);
  const legR = kit.box(0.09, 0.4, 0.11, statueMaterial, 0.09, 0.2, 0.05);
  legR.rotation.x = -0.12;
  const lower = kit.box(0.3, 0.3, 0.19, statueMaterial, 0, 0.5, 0);
  const torso = kit.box(0.27, 0.34, 0.17, statueMaterial, 0, 0.8, 0);
  const drape = kit.box(0.1, 0.62, 0.2, statueMaterial, -0.11, 0.52, 0.01);
  const head = new THREE.Mesh(new THREE.SphereGeometry(0.1, 10, 8), statueMaterial);
  head.position.y = 1.06;
  head.castShadow = true;
  const armRaised = kit.box(0.07, 0.4, 0.08, statueMaterial, 0.21, 1.02, 0.1);
  armRaised.rotation.set(-0.9, 0, -0.75);
  const armLow = kit.box(0.07, 0.34, 0.08, statueMaterial, -0.19, 0.68, 0);
  armLow.rotation.z = 0.35;
  figure.add(legL, legR, lower, torso, drape, head, armRaised, armLow);
  g.add(figure);

  // the standard: the Item's pennant flying beside the statue
  const standard = new THREE.Group();
  standard.position.set(0.95, 0, 0.25);
  standard.add(kit.cyl(0.035, 0.045, 2.6, 6, kit.M.woodDark, 0, 1.3, 0));
  const flag = new THREE.Mesh(
    new THREE.PlaneGeometry(0.72, 0.34),
    kit.mat(accent, { side: THREE.DoubleSide })
  );
  flag.position.set(0.38, 2.35, 0);
  flag.castShadow = true;
  standard.add(flag);
  const finial = new THREE.Mesh(new THREE.SphereGeometry(0.05, 8, 6), goldMaterial);
  finial.position.y = 2.65;
  standard.add(finial);
  g.add(standard);

  // the slate beacon above the statue, for needs-you, visible from far out
  const orb = new THREE.Sprite(
    new THREE.SpriteMaterial({
      map: kit.tex.slateOrb,
      transparent: true,
      depthTest: false,
      opacity: 0.95
    })
  );
  orb.scale.setScalar(0.9);
  orb.position.y = 3.3;
  orb.visible = false;
  orb.renderOrder = 12;
  g.add(orb);

  const hit = new THREE.Mesh(
    new THREE.CylinderGeometry(0.9, 0.9, 3.2, 8),
    new THREE.MeshBasicMaterial({ visible: false })
  );
  hit.position.y = 1.5;
  g.add(hit);

  g.scale.setScalar(1.3);
  index.tag(g, { kind: "overseer", itemId });

  return {
    group: g,
    orb,
    flag,
    finial,
    statueMaterial,
    goldMaterial,
    statueParts: [legL, legR, lower, torso, drape, head, armRaised, armLow],
    active: false,
    supervisorActive: false,
    gilded: false
  };
}

// Ordinary work announces itself: a thin column of pale quarry-dust rising off the
// structure while any of the Item's workers is genuinely at work. It is everything
// the trouble fire is not — no flame, no light, lighter than air and lighter in
// colour — so the two never read as each other, and from across the water a working
// island visibly breathes. Sized to the pad, like the fire, for the same reason.
export function makeWorkPlume(
  kit: AtlasKit,
  padR: number,
  buildH: number
): { group: THREE.Group; motes: PlumeMote[] } {
  const group = new THREE.Group();
  const motes: PlumeMote[] = [];
  const s = Math.max(1, padR / 5.5);
  for (let i = 0; i < 4; i++) {
    const sprite = new THREE.Sprite(
      new THREE.SpriteMaterial({
        map: kit.tex.smoke,
        transparent: true,
        opacity: 0.16,
        color: "#efe6cd",
        depthWrite: false
      })
    );
    sprite.scale.setScalar((0.7 + i * 0.4) * s);
    sprite.position.set((i % 2 ? 0.25 : -0.2) * s, buildH + 0.4 + i * 0.8 * s, 0);
    group.add(sprite);
    motes.push({ sprite, base: buildH + 0.4, step: 0.8 * s, index: i, scale: s });
  }
  return { group, motes };
}

// Trouble breaks the aesthetic: fire and smoke around the Item's structure. Sized
// to the pad, because it must read from across the water, where the pads are grand
// and a human-scale brazier disappears.
export function makeTroubleFire(
  kit: AtlasKit,
  padR: number
): { group: THREE.Group; flames: { mesh: THREE.Mesh; x: number }[]; flameLights: THREE.PointLight[] } {
  const group = new THREE.Group();
  const flames: { mesh: THREE.Mesh; x: number }[] = [];
  const flameLights: THREE.PointLight[] = [];
  const s = Math.max(1, padR / 4.5);
  for (const a of [0.4, 2.3, 4.3]) {
    const x = Math.cos(a) * padR * 0.55;
    const z = Math.sin(a) * padR * 0.55;
    group.add(kit.cyl(0.16 * s, 0.24 * s, 0.4 * s, 8, kit.M.cliff, x, 0.2 * s, z));
    const flame = new THREE.Mesh(
      new THREE.ConeGeometry(0.17 * s, 0.6 * s, 6),
      new THREE.MeshBasicMaterial({ color: "#ff9d4d" })
    );
    flame.position.set(x, 0.68 * s, z);
    group.add(flame);
    flames.push({ mesh: flame, x });
    const glow = new THREE.PointLight("#ff8a3d", 1.1 * s, 6 * s);
    glow.position.set(x, 0.8 * s, z);
    group.add(glow);
    flameLights.push(glow);
    // a rising column of smoke, the far-range half of the signal
    for (let i = 0; i < 3; i++) {
      const smoke = new THREE.Sprite(
        new THREE.SpriteMaterial({ map: kit.tex.smoke, transparent: true, opacity: 0.42 - i * 0.1 })
      );
      smoke.scale.setScalar((0.9 + i * 0.55) * s);
      smoke.position.set(x + i * 0.12 * s, (1.5 + i * 0.85) * s, z);
      group.add(smoke);
    }
  }
  return { group, flames, flameLights };
}

export function readOverseerState(
  figure: OverseerFigure,
  item: AtlasItem,
  liveTickets: AtlasTicket[]
): { anyNeeds: boolean; anyTrouble: boolean; anyWorking: boolean } {
  const anyNeeds =
    liveTickets.some((ticket) => ticket.needsMe && !ticket.trouble) || item.supervisorNeedsMe;
  const anyTrouble = liveTickets.some((ticket) => ticket.trouble);
  const anyWorking = liveTickets.some((ticket) => ticket.working && !ticket.trouble);
  figure.orb.visible = anyNeeds;
  figure.active = anyWorking;
  figure.supervisorActive = item.supervisorWorking;
  const wantGold = item.allDone;
  if (figure.gilded !== wantGold) {
    figure.gilded = wantGold;
    const material = wantGold ? figure.goldMaterial : figure.statueMaterial;
    for (const part of figure.statueParts) part.material = material;
  }
  return { anyNeeds, anyTrouble, anyWorking };
}

// Per-frame life: the beacon breathes and holds its apparent size from far out
// (like the bubbles do), the pennant sways. The statue itself is stone and does not
// move.
const _worldAt = new THREE.Vector3();

export function animateOverseer(
  figure: OverseerFigure,
  time: number,
  camera: THREE.Camera
): void {
  // a working crew's pennant streams; an idle one barely stirs
  const amp = figure.active ? 0.42 : 0.1;
  const rate = figure.active ? 3.1 : 1.4;
  figure.flag.rotation.y =
    Math.sin(time * rate + figure.group.position.x) * amp +
    (figure.active ? Math.sin(time * 7.3 + figure.group.position.x) * 0.07 : 0);
  // the finial marks the supervisor's own live conversation with a slow pulse
  figure.finial.scale.setScalar(
    figure.supervisorActive ? 1 + Math.sin(time * 2.4) * 0.35 : 1
  );
  if (figure.orb.visible) {
    figure.group.getWorldPosition(_worldAt);
    const d = camera.position.distanceTo(_worldAt);
    const far = Math.max(1, d / 26);
    const breathe = 1 + Math.sin(time * 2.1) * 0.1;
    figure.orb.scale.setScalar(0.9 * far * breathe);
    figure.orb.position.y = 3.3 + Math.sin(time * 1.3) * 0.1;
    figure.orb.material.opacity = 0.8 + Math.sin(time * 2.1) * 0.15;
  }
}
