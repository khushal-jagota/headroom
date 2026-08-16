// A Ticket is a worker, and what the worker is doing is what the Ticket is doing.
//
// Each trade has its own mini design and its own act: the mason at the block, the
// porter under a yoke, the hooded inspector with a lantern, the sculptor at a
// half-made statue, the scout with hat and spyglass, and a traveller with staff and
// satchel for any type the world does not know yet. Needs-you workers hail you
// under the slate banner; trouble sits smoking by a cracked stone; a finished
// Ticket becomes a stele on the pad's edge.

import * as THREE from "three";
import type { AtlasTicket } from "../model";
import { AtlasKit } from "./materials";
import { hashCode, mulberry32 } from "./random";
import { PickIndex } from "./picking";
import { marbleFigure } from "./structures";
import { CHIPS } from "./tuning";

export type WorkerFigure = {
  group: THREE.Group;
  ticket: AtlasTicket;
  legL: THREE.Mesh;
  legR: THREE.Mesh;
  torso: THREE.Mesh;
  skirt: THREE.Mesh;
  head: THREE.Mesh;
  hair: THREE.Mesh;
  armL: THREE.Group;
  armR: THREE.Group;
  banner: THREE.Group;
  block: THREE.Mesh;
  crackedBlock: THREE.Group;
  hammer: THREE.Group;
  scroll: THREE.Mesh;
  plumb: THREE.Group;
  cloth: THREE.Mesh;
  smoke: THREE.Sprite;
  spark: THREE.Sprite;
  // the two props an act reaches for by name
  hood: THREE.Mesh | null;
  statue: THREE.Group | null;
  phase: number;
  idleKind: number;
  scale: number;
  baseY: number;
  baseRot: number;
  lastCycle: number | null;
};

export function makeWorker(
  kit: AtlasKit,
  index: PickIndex,
  ticket: AtlasTicket,
  accent: string
): WorkerFigure {
  const rng = mulberry32(hashCode(ticket.id));
  const base = new THREE.Color(accent);
  const v = rng();
  if (v < 0.33) base.lerp(new THREE.Color("#f0ead8"), 0.35);
  else if (v > 0.66) base.lerp(new THREE.Color("#20242c"), 0.3);
  const tunic = kit.mat("#" + base.getHexString());
  const g = new THREE.Group();
  const scale = (0.92 + rng() * 0.18) * 1.38;

  const legL = kit.box(0.055, 0.2, 0.06, kit.M.skin, -0.05, 0.1, 0);
  const legR = kit.box(0.055, 0.2, 0.06, kit.M.skin, 0.05, 0.1, 0);
  const torso = kit.box(0.17, 0.24, 0.11, tunic, 0, 0.32, 0);
  const skirt = kit.box(0.19, 0.1, 0.13, tunic, 0, 0.21, 0);
  const head = new THREE.Mesh(new THREE.SphereGeometry(0.075, 10, 8), kit.M.skin);
  head.position.y = 0.51;
  head.castShadow = true;
  const hair = new THREE.Mesh(
    new THREE.SphereGeometry(0.078, 10, 8, 0, Math.PI * 2, 0, Math.PI * 0.55),
    kit.M.hair
  );
  hair.position.y = 0.515;

  const armL = new THREE.Group();
  armL.position.set(-0.105, 0.42, 0);
  armL.add(kit.box(0.045, 0.2, 0.05, kit.M.skin, 0, -0.09, 0));
  const armR = new THREE.Group();
  armR.position.set(0.105, 0.42, 0);
  armR.add(kit.box(0.045, 0.2, 0.05, kit.M.skin, 0, -0.09, 0));

  const hammer = new THREE.Group();
  hammer.add(kit.cyl(0.018, 0.018, 0.2, 6, kit.M.wood, 0, 0, 0));
  hammer.add(kit.box(0.095, 0.055, 0.055, kit.M.cliff, 0, 0.1, 0));
  hammer.position.set(0, -0.18, 0);
  hammer.rotation.x = Math.PI / 2;
  armR.add(hammer);
  const scroll = kit.cyl(0.035, 0.035, 0.2, 8, kit.M.marble, 0, -0.18, 0.03);
  scroll.rotation.z = Math.PI / 2;
  armL.add(scroll);
  const plumb = new THREE.Group();
  plumb.add(kit.cyl(0.007, 0.007, 0.26, 4, kit.M.woodDark, 0, -0.13, 0));
  plumb.add(kit.cyl(0.024, 0.006, 0.06, 6, kit.M.cliff, 0, -0.28, 0));
  plumb.position.set(0, -0.19, 0);
  armR.add(plumb);
  const cloth = kit.box(0.1, 0.024, 0.1, kit.mat("#ded6c2"), 0, -0.2, 0);
  armR.add(cloth);

  const banner = new THREE.Group();
  banner.add(kit.cyl(0.014, 0.014, 0.62, 5, kit.M.woodDark, 0, 0.31, 0));
  banner.add(kit.box(0.36, 0.2, 0.024, kit.M.slate, 0.19, 0.5, 0));
  banner.position.y = 0.62;
  banner.visible = false;

  // ---- the worker's trade gear: one mini design per worker type ----
  let hood: THREE.Mesh | null = null;
  let statue: THREE.Group | null = null;
  const type = ticket.workerType;
  if (type === "general") {
    // porter: a carrying yoke with two baskets
    const yoke = new THREE.Group();
    const beam = kit.cyl(0.018, 0.018, 0.62, 6, kit.M.wood, 0, 0, 0);
    beam.rotation.z = Math.PI / 2;
    yoke.add(beam);
    for (const sx of [-0.29, 0.29]) {
      yoke.add(kit.cyl(0.008, 0.008, 0.12, 4, kit.M.woodDark, sx, -0.07, 0));
      yoke.add(kit.cyl(0.09, 0.06, 0.1, 8, kit.M.wood, sx, -0.16, 0));
    }
    yoke.position.y = 0.47;
    g.add(yoke);
  } else if (type === "debugging") {
    // inspector: hooded, lantern in hand, a cracked slab under inspection
    hood = new THREE.Mesh(new THREE.ConeGeometry(0.1, 0.14, 8), tunic);
    hood.position.y = 0.56;
    hood.castShadow = true;
    g.add(hood);
    const lantern = new THREE.Group();
    lantern.add(kit.box(0.06, 0.08, 0.06, kit.M.woodDark, 0, 0, 0));
    const glow = new THREE.Mesh(
      new THREE.BoxGeometry(0.04, 0.05, 0.04),
      new THREE.MeshBasicMaterial({ color: "#ffd98e" })
    );
    lantern.add(glow);
    lantern.position.set(0, -0.2, 0.03);
    armL.add(lantern);
    const slab = new THREE.Group();
    const stone = kit.box(0.34, 0.16, 0.3, kit.M.marbleOld, 0, 0.08, 0.38);
    const crack = kit.box(0.36, 0.03, 0.04, kit.M.crack, 0, 0.12, 0.39);
    crack.rotation.z = -0.4;
    slab.add(stone, crack);
    g.add(slab);
  } else if (type === "new_worker") {
    // sculptor: a figure taking shape on a plinth
    statue = new THREE.Group();
    statue.add(kit.box(0.3, 0.22, 0.3, kit.M.marbleOld, 0, 0.11, 0));
    const figure = marbleFigure(kit);
    figure.position.y = 0.22;
    figure.scale.setScalar(0.85);
    statue.add(figure);
    statue.position.set(0, 0, 0.42);
    g.add(statue);
  } else if (type === "exploration") {
    // scout: wide hat, spyglass, a lookout rock
    const brim = kit.cyl(0.13, 0.13, 0.025, 10, kit.mat("#8a744f"), 0, 0.575, 0);
    const crown = kit.cyl(0.055, 0.06, 0.05, 8, kit.mat("#8a744f"), 0, 0.61, 0);
    g.add(brim, crown);
    const glass = kit.cyl(0.022, 0.028, 0.16, 6, kit.M.woodDark, 0, -0.19, 0.02);
    glass.rotation.x = Math.PI / 2;
    armR.add(glass);
    const rock = new THREE.Mesh(new THREE.IcosahedronGeometry(0.16, 0), kit.M.cliff);
    rock.position.set(0, 0.04, 0);
    rock.scale.y = 0.6;
    g.add(rock);
  } else if (type === "initiative_planning") {
    // architect: a drafting table with the great plan
    const table = new THREE.Group();
    const feet: [number, number][] = [
      [-0.16, -0.1],
      [0.16, -0.1],
      [-0.16, 0.1],
      [0.16, 0.1]
    ];
    for (const [px, pz] of feet) table.add(kit.cyl(0.015, 0.015, 0.3, 5, kit.M.wood, px, 0.15, pz));
    const top = kit.box(0.44, 0.03, 0.3, kit.M.woodDark, 0, 0.31, 0);
    top.rotation.x = -0.2;
    const plan = kit.box(0.38, 0.012, 0.24, kit.M.marble, 0, 0.335, 0);
    plan.rotation.x = -0.2;
    table.add(top, plan);
    table.position.set(0, 0, 0.38);
    g.add(table);
  } else if (type === "product_design") {
    // painter: easel and canvas
    const easel = new THREE.Group();
    easel.add(kit.cyl(0.014, 0.014, 0.62, 5, kit.M.wood, -0.12, 0.31, 0.03));
    easel.add(kit.cyl(0.014, 0.014, 0.62, 5, kit.M.wood, 0.12, 0.31, 0.03));
    easel.add(kit.cyl(0.014, 0.014, 0.6, 5, kit.M.wood, 0, 0.3, -0.1));
    const canvas = kit.box(0.34, 0.28, 0.015, kit.M.marble, 0, 0.42, 0.05);
    canvas.rotation.x = -0.12;
    easel.add(canvas);
    const daub1 = kit.box(0.08, 0.05, 0.01, kit.mat(accent), -0.06, 0.45, 0.062);
    const daub2 = kit.box(0.05, 0.07, 0.01, kit.M.terracotta, 0.07, 0.4, 0.062);
    daub1.rotation.x = -0.12;
    daub2.rotation.x = -0.12;
    easel.add(daub1, daub2);
    easel.position.set(0, 0, 0.4);
    g.add(easel);
    armR.add(kit.cyl(0.008, 0.008, 0.12, 4, kit.M.wood, 0, -0.19, 0.02));
  } else if (type === "planning-day") {
    // herald of the day: laurel and horn
    const wreath = new THREE.Mesh(new THREE.TorusGeometry(0.075, 0.018, 6, 12), kit.M.olive);
    wreath.rotation.x = Math.PI / 2 - 0.25;
    wreath.position.y = 0.555;
    g.add(wreath);
    const horn = new THREE.Mesh(
      new THREE.ConeGeometry(0.05, 0.2, 8),
      kit.mat("#b8a06a", { roughness: 0.5 })
    );
    horn.rotation.x = -Math.PI / 2 - 0.4;
    horn.position.set(0, -0.2, 0.06);
    armR.add(horn);
  } else if (type === "planning-midday-check") {
    // keeper of the noon: reads the sundial
    const dial = new THREE.Group();
    dial.add(kit.cyl(0.2, 0.24, 0.1, 10, kit.M.marbleOld, 0, 0.05, 0));
    dial.add(kit.cyl(0.18, 0.18, 0.03, 10, kit.M.marble, 0, 0.115, 0));
    const gnomon = kit.box(0.02, 0.12, 0.06, kit.M.marbleOld, 0, 0.19, -0.03);
    gnomon.rotation.x = 0.5;
    dial.add(gnomon);
    dial.position.set(0, 0, 0.36);
    g.add(dial);
  } else if (type === "planning-sprint") {
    // keeper of the span: the long scroll, read with both hands
    const wide = new THREE.Group();
    wide.add(kit.box(0.4, 0.16, 0.01, kit.M.marble, 0, 0, 0));
    const rollerLeft = kit.cyl(0.025, 0.025, 0.2, 6, kit.M.wood, -0.21, 0, 0);
    const rollerRight = kit.cyl(0.025, 0.025, 0.2, 6, kit.M.wood, 0.21, 0, 0);
    rollerLeft.rotation.x = Math.PI / 2;
    rollerRight.rotation.x = Math.PI / 2;
    wide.add(rollerLeft, rollerRight);
    wide.position.set(0, 0.42, 0.14);
    g.add(wide);
  } else if (type === "personal") {
    // tending their own garden: watering pot and a plant
    const pot = new THREE.Group();
    pot.add(kit.cyl(0.05, 0.04, 0.08, 8, kit.M.terracotta, 0, 0, 0));
    const spout = kit.cyl(0.012, 0.012, 0.09, 4, kit.M.terracotta, 0.06, 0.01, 0);
    spout.rotation.z = -0.9;
    pot.add(spout);
    pot.position.set(0, -0.2, 0.02);
    armR.add(pot);
    const plant = new THREE.Group();
    plant.add(kit.cyl(0.11, 0.08, 0.18, 8, kit.M.terracotta, 0, 0.09, 0));
    const bush = new THREE.Mesh(new THREE.IcosahedronGeometry(0.11, 0), kit.M.olive);
    bush.position.y = 0.26;
    bush.castShadow = true;
    plant.add(bush);
    plant.position.set(0, 0, 0.34);
    g.add(plant);
  } else if (type !== "coding") {
    // a trade the world does not know yet: the traveller, with staff and satchel
    const staff = kit.cyl(0.014, 0.016, 0.72, 5, kit.M.woodDark, 0.16, 0.36, 0.04);
    const satchel = kit.box(0.1, 0.12, 0.05, kit.mat("#8a744f"), -0.11, 0.3, 0.05);
    const strap = kit.box(0.02, 0.24, 0.02, kit.mat("#6d5a3e"), 0.02, 0.42, 0.062);
    strap.rotation.z = 0.7;
    g.add(staff, satchel, strap);
  }

  const block = kit.box(0.32, 0.3, 0.32, kit.M.marble, 0, 0.15, 0.34);
  const crackedBlock = new THREE.Group();
  const cracked = kit.box(0.34, 0.24, 0.3, kit.M.marbleOld, 0, 0.12, 0.34);
  cracked.rotation.z = 0.16;
  const crack = kit.box(0.36, 0.035, 0.045, kit.M.crack, 0, 0.15, 0.35);
  crack.rotation.z = -0.5;
  crackedBlock.add(cracked, crack);

  const smoke = new THREE.Sprite(
    new THREE.SpriteMaterial({ map: kit.tex.smoke, transparent: true, opacity: 0 })
  );
  smoke.scale.setScalar(0.34);
  smoke.position.set(0.1, 0.75, 0.2);
  const spark = new THREE.Sprite(
    new THREE.SpriteMaterial({ map: kit.tex.spark, transparent: true, opacity: 0 })
  );
  spark.scale.setScalar(0.14);
  spark.position.set(0, 0.42, 0.36);

  // generous invisible hit target so tapping a person is easy
  const hit = new THREE.Mesh(
    new THREE.CylinderGeometry(0.36, 0.36, 1.15, 8),
    new THREE.MeshBasicMaterial({ visible: false })
  );
  hit.position.y = 0.5;

  g.add(legL, legR, torso, skirt, head, hair, armL, armR, banner, block, crackedBlock, smoke, spark, hit);
  g.scale.setScalar(scale);
  index.tag(g, { kind: "ticket", ticketId: ticket.id });

  return {
    group: g,
    ticket,
    legL,
    legR,
    torso,
    skirt,
    head,
    hair,
    armL,
    armR,
    banner,
    block,
    crackedBlock,
    hammer,
    scroll,
    plumb,
    cloth,
    smoke,
    spark,
    hood,
    statue,
    phase: mulberry32(hashCode(ticket.id) + 5)() * Math.PI * 2,
    idleKind: Math.floor(mulberry32(hashCode(ticket.id) + 9)() * 2),
    scale,
    baseY: 0,
    baseRot: 0,
    lastCycle: null
  };
}

export type SteleFigure = { group: THREE.Group; ticket: AtlasTicket };

export function makeStele(
  kit: AtlasKit,
  index: PickIndex,
  ticket: AtlasTicket
): SteleFigure {
  const g = new THREE.Group();
  g.add(kit.box(0.16, 0.05, 0.12, kit.M.marbleOld, 0, 0.025, 0));
  g.add(kit.box(0.13, 0.24, 0.05, kit.M.marble, 0, 0.16, 0));
  const cap = kit.cyl(0.075, 0.075, 0.05, 8, kit.M.marble, 0, 0.29, 0);
  cap.rotation.x = Math.PI / 2;
  g.add(cap);
  const hit = new THREE.Mesh(
    new THREE.CylinderGeometry(0.2, 0.2, 0.45, 6),
    new THREE.MeshBasicMaterial({ visible: false })
  );
  hit.position.y = 0.2;
  g.add(hit);
  const rng = mulberry32(hashCode(ticket.id));
  g.rotation.y = (rng() - 0.5) * 0.5;
  // waist-height beside the grand pads: settled, present, never competing
  g.scale.setScalar(1.7);
  index.tag(g, { kind: "stele", ticketId: ticket.id });
  return { group: g, ticket };
}

// ---------------- stone chips ----------------

// The mason's block and the sculptor's figure throw chips on every stroke.
export class ChipField {
  private readonly chips: THREE.Mesh[] = [];
  private readonly velocities = new Map<THREE.Mesh, THREE.Vector3>();
  private readonly lives = new Map<THREE.Mesh, number>();
  private readonly geometry: THREE.TetrahedronGeometry;

  constructor(
    private readonly kit: AtlasKit,
    private readonly root: THREE.Object3D
  ) {
    this.geometry = kit.own(new THREE.TetrahedronGeometry(0.035));
  }

  spawn(at: THREE.Vector3): void {
    if (this.chips.length > CHIPS.max) return;
    const chip = new THREE.Mesh(this.geometry, this.kit.M.marble);
    chip.position.copy(at);
    this.velocities.set(
      chip,
      new THREE.Vector3(
        (Math.random() - 0.5) * CHIPS.spread,
        CHIPS.rise + Math.random(),
        (Math.random() - 0.5) * CHIPS.spread
      )
    );
    this.lives.set(chip, CHIPS.life);
    this.root.add(chip);
    this.chips.push(chip);
  }

  step(dt: number): void {
    for (let i = this.chips.length - 1; i >= 0; i--) {
      const chip = this.chips[i];
      const life = (this.lives.get(chip) ?? 0) - dt;
      if (life <= 0) {
        chip.removeFromParent();
        this.lives.delete(chip);
        this.velocities.delete(chip);
        this.chips.splice(i, 1);
        continue;
      }
      this.lives.set(chip, life);
      const velocity = this.velocities.get(chip);
      if (!velocity) continue;
      velocity.y -= CHIPS.gravity * dt;
      chip.position.addScaledVector(velocity, dt);
      chip.rotation.x += dt * 9;
      chip.rotation.z += dt * 7;
    }
  }

  // The chips share the kit's marble and one geometry, so there is nothing of
  // their own to give back beyond leaving the world.
  dispose(): void {
    for (const chip of this.chips) chip.removeFromParent();
    this.chips.length = 0;
    this.lives.clear();
    this.velocities.clear();
  }
}

// ---------------- the act ----------------

const _chipAt = new THREE.Vector3();

// Readable at a glance, eased like a game.
export function poseWorker(figure: WorkerFigure, time: number, chips: ChipField): void {
  const u = figure;
  const t = u.ticket;
  const ph = u.phase;
  u.armL.rotation.set(0, 0, 0.12);
  u.armR.rotation.set(0, 0, -0.12);
  u.legL.position.set(-0.05, 0.1, 0);
  u.legR.position.set(0.05, 0.1, 0);
  u.legL.rotation.x = 0;
  u.legR.rotation.x = 0;
  u.torso.position.y = 0.32;
  u.head.position.y = 0.51;
  u.hair.position.y = 0.515;
  u.torso.rotation.x = 0;
  u.head.rotation.x = 0;
  u.head.rotation.y = 0;
  u.group.position.y = u.baseY;
  u.banner.visible = false;
  u.block.visible = false;
  u.crackedBlock.visible = false;
  u.hammer.visible = false;
  u.scroll.visible = false;
  u.plumb.visible = false;
  u.cloth.visible = false;
  u.smoke.material.opacity = 0;
  u.spark.material.opacity = 0;
  u.torso.scale.y = 1 + Math.sin(time * 1.7 + ph) * 0.015;

  const active = t.working && !t.trouble;
  if (t.trouble) {
    u.crackedBlock.visible = true;
    u.legL.rotation.x = -1.4;
    u.legR.rotation.x = -1.4;
    u.legL.position.y = 0.05;
    u.legR.position.y = 0.05;
    u.torso.position.y = 0.22;
    u.torso.rotation.x = 0.28;
    u.head.position.y = 0.4;
    u.head.rotation.x = 0.5;
    u.hair.position.y = 0.41;
    u.armL.rotation.set(1.9, 0, 0.5);
    u.armR.rotation.set(1.9, 0, -0.5);
    u.smoke.material.opacity = 0.35 + Math.sin(time * 1.1 + ph) * 0.12;
    u.smoke.position.y = 0.72 + ((time * 0.18 + ph) % 0.5);
    return;
  }
  if (t.needsMe) {
    u.banner.visible = true;
    const wave = Math.sin(time * 3 + ph);
    u.armR.rotation.set(0, 0, -2.55 + wave * 0.22);
    u.armL.rotation.set(0, 0, 0.25);
    u.torso.rotation.x = -0.04;
    return;
  }

  // the trade decides the act: not everyone is building
  const type = t.workerType;
  if (type === "general") {
    // porter under the yoke, hands steadying the loads
    u.armL.rotation.set(-1.35, 0, 0.4);
    u.armR.rotation.set(-1.35, 0, -0.4);
    if (active) {
      const step = Math.sin(time * 2.8 + ph);
      u.legL.rotation.x = step * 0.55;
      u.legR.rotation.x = -step * 0.55;
      u.group.position.y = u.baseY + Math.abs(step) * 0.02;
    }
  } else if (type === "debugging") {
    // hooded inspector, lantern held over the cracked slab
    u.legL.rotation.x = -0.9;
    u.legR.rotation.x = -0.9;
    u.legL.position.y = 0.07;
    u.legR.position.y = 0.07;
    u.torso.position.y = 0.25;
    u.torso.rotation.x = 0.45;
    u.head.position.y = 0.43;
    u.hair.position.y = 0.435;
    if (u.hood) u.hood.position.y = 0.48;
    u.head.rotation.x = 0.5;
    const sway = active ? Math.sin(time * 1.6 + ph) * 0.25 : 0;
    u.armL.rotation.set(-1.2 + sway * 0.3, 0, 0.15 + sway * 0.3);
    u.armR.rotation.set(-0.3, 0, -0.2);
    u.head.rotation.y = active ? Math.sin(time * 0.9 + ph) * 0.35 : 0;
  } else if (type === "new_worker") {
    // sculptor: bringing a new figure out of the marble
    u.hammer.visible = true;
    u.head.rotation.x = 0.15;
    if (active) {
      const cycle = (time * 1.5 + ph) % 1;
      const lift = cycle < 0.7 ? cycle / 0.7 : 1 - (cycle - 0.7) / 0.3;
      u.armR.rotation.set(-1.0 - lift * 1.2, 0, -0.1);
      u.armL.rotation.set(-1.15, 0, 0.15);
      if (u.lastCycle != null && u.lastCycle > cycle && u.statue) {
        u.statue.getWorldPosition(_chipAt);
        _chipAt.y += 0.5;
        chips.spawn(_chipAt);
        chips.spawn(_chipAt);
      }
      u.lastCycle = cycle;
    } else {
      u.armR.rotation.set(-2.9, 0, -0.25);
      u.armL.rotation.set(0, 0, 0.2);
    }
  } else if (type === "exploration") {
    // scout on the rock, glass to the horizon
    u.group.position.y = u.baseY + 0.055;
    if (active) {
      u.armR.rotation.set(-1.7, 0, -0.08);
      u.head.rotation.x = -0.12;
      u.group.rotation.y = u.baseRot + Math.sin(time * 0.35 + ph) * 0.5;
    } else {
      u.armR.rotation.set(-0.25, 0, -0.2);
      u.head.rotation.y = Math.sin(time * 0.5 + ph) * 0.4;
    }
  } else if (type === "initiative_planning") {
    // architect bent over the great plan
    u.torso.rotation.x = 0.35;
    u.head.rotation.x = 0.45;
    const c = active ? time * 2.2 + ph : ph;
    u.armR.rotation.set(-1.2 + Math.sin(c) * 0.12, 0, Math.cos(c) * 0.18);
    u.armL.rotation.set(-1.0, 0, 0.25);
  } else if (type === "product_design") {
    // painter at the easel
    u.head.rotation.x = 0.1;
    if (active) {
      const dab = Math.sin(time * 4.2 + ph);
      u.armR.rotation.set(-1.25 + dab * 0.12, 0, -0.1 + Math.cos(time * 2.1 + ph) * 0.12);
      u.armL.rotation.set(-0.4, 0, 0.25);
    } else {
      // stepping back, considering
      u.armR.rotation.set(-0.9, 0, -0.15);
      u.head.rotation.x = -0.05;
    }
  } else if (type === "planning-day") {
    // herald of the day
    if (active) {
      u.armR.rotation.set(-2.35, 0, -0.12);
      u.head.rotation.x = -0.2;
    } else {
      u.armR.rotation.set(-0.3, 0, -0.25);
    }
  } else if (type === "planning-midday-check") {
    // reading the sundial
    u.torso.rotation.x = 0.35;
    u.head.rotation.x = 0.5;
    u.armR.rotation.set(-1.05 + (active ? Math.sin(time * 1.2 + ph) * 0.1 : 0), 0, -0.1);
    u.armL.rotation.set(-0.3, 0, 0.25);
  } else if (type === "planning-sprint") {
    // the long scroll, held wide
    u.armL.rotation.set(-1.3, 0, 0.35);
    u.armR.rotation.set(-1.3, 0, -0.35);
    u.head.rotation.x = 0.3;
    if (active) u.head.rotation.y = Math.sin(time * 0.8 + ph) * 0.3;
  } else if (type === "personal") {
    // tending the garden
    u.torso.rotation.x = 0.25;
    u.head.rotation.x = 0.4;
    const tilt = active ? 0.35 + Math.sin(time * 1.1 + ph) * 0.3 : 0.05;
    u.armR.rotation.set(-1.0 - tilt, 0, -0.15);
    u.armL.rotation.set(-0.2, 0, 0.25);
  } else if (type === "coding") {
    // mason at the block
    u.block.visible = true;
    u.hammer.visible = true;
    u.torso.rotation.x = 0.22;
    u.head.rotation.x = 0.32;
    if (active) {
      const cycle = (time * 1.6 + ph) % 1;
      const lift = cycle < 0.7 ? cycle / 0.7 : 1 - (cycle - 0.7) / 0.3;
      u.armR.rotation.set(-0.45 - lift * 1.75, 0, -0.05);
      u.armL.rotation.set(-0.95, 0, 0.15);
      if (u.lastCycle != null && u.lastCycle > cycle) {
        u.block.getWorldPosition(_chipAt);
        _chipAt.y += 0.16;
        chips.spawn(_chipAt);
        chips.spawn(_chipAt);
      }
      u.lastCycle = cycle;
    } else if (u.idleKind === 0) {
      u.armR.rotation.set(-2.9, 0, -0.25);
      u.armL.rotation.set(0, 0, 0.2);
    } else {
      u.hammer.visible = false;
      u.torso.rotation.x = -0.08;
      u.armR.rotation.set(-0.6, 0, -0.7);
      u.armL.rotation.set(-0.6, 0, 0.7);
      u.head.rotation.x = 0.05;
    }
  } else {
    // an unknown trade: the traveller, ready with staff and satchel
    u.armR.rotation.set(-0.3, 0, -0.5);
    u.armL.rotation.set(0, 0, 0.2);
    u.head.rotation.y = Math.sin(time * 0.6 + ph) * 0.3;
    if (active) u.group.position.y = u.baseY + Math.abs(Math.sin(time * 2.0 + ph)) * 0.012;
  }
}
