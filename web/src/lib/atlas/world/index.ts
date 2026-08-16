// Atlas — an archipelago of the day's work, explorable.
//
// The scene grammar:
//   Project     -> its own island, with its accent colour on pennants, trim, and
//                  the tunics of its workers.
//   Sprint Item -> one pad on that island, holding one structure whose physical
//                  construction state is the Item's progress, with the Item's own
//                  overseer standing beside it.
//   Ticket      -> one worker on that pad. What the worker is doing is what the
//                  Ticket is doing. A finished Ticket becomes a stele.
//
// This file is the wiring: it holds the renderer, the loop, and the correspondence
// between a reading of the board and the things standing in the world. It fetches
// nothing and knows nothing about panels — data arrives through show(), and what
// the human picks up leaves through onSelect().

import * as THREE from "three";
import type { AtlasScene, AtlasSceneOptions, AtlasSelection } from "../contracts";
import {
  happeningsBetween,
  type AtlasItem,
  type AtlasProject,
  type AtlasWorld
} from "../model";
import { C } from "./palette";
import { AtlasKit } from "./materials";
import { applyDay, createSky, nowHour } from "./day";
import {
  islandLayout,
  makeIsland,
  placeIslands,
  type IslandLayout,
  type IslandTheme
} from "./island";
import { archetypeFor, buildStructure, type Flame } from "./structures";
import { ChipField, makeStele, makeWorker, poseWorker, type SteleFigure, type WorkerFigure } from "./workers";
import {
  animateOverseer,
  makeOverseer,
  makeTroubleFire,
  makeWorkPlume,
  readOverseerState,
  type OverseerFigure,
  type PlumeMote
} from "./overseer";
import { createFleet } from "./boats";
import { BubbleLayer, type BubbleAnchor } from "./bubbles";
import { createCameraRig } from "./camera";
import { createPicker, PickIndex, type PickTag } from "./picking";
import { ARRIVAL, FOG, LAYOUT, LOD, RING, SEA } from "./tuning";

type Fire = {
  group: THREE.Group;
  flames: Flame[];
  flameLights: THREE.PointLight[];
};

type Plume = { group: THREE.Group; motes: PlumeMote[] };

type ItemBuild = {
  item: AtlasItem;
  pad: THREE.Group;
  base: THREE.Group;
  padR: number;
  kind: string;
  progressKey: number;
  structure: THREE.Group | null;
  flames: Flame[];
  flameLights: THREE.PointLight[];
  buildH: number;
  workers: Map<string, WorkerFigure>;
  steles: Map<string, SteleFigure>;
  overseer: OverseerFigure | null;
  fire: Fire | null;
  plume: Plume | null;
};

type Island = {
  project: AtlasProject;
  group: THREE.Group;
  precinct: THREE.Group;
  accent: string;
  theme: IslandTheme;
  // the island's ground is drawn once, from the layout it had when it first
  // appeared: a project's island keeps its shape, and nothing shuffles under it
  lay: IslandLayout;
  foamA: THREE.Mesh;
  foamB: THREE.Mesh;
  namePlate: THREE.Sprite;
  items: Map<string, ItemBuild>;
  lodOn: boolean | null;
  lodFade: number;
};

export function createAtlasScene(options: AtlasSceneOptions): AtlasScene {
  const { canvas, onSelect, reducedMotion } = options;

  // ---------------- renderer, scene, kit ----------------

  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  const scene = new THREE.Scene();
  const fog = new THREE.Fog("#d8e5e6", FOG.minNear, FOG.minNear + FOG.depth);
  scene.fog = fog;

  const kit = new AtlasKit();
  const index = new PickIndex();
  const rig = createCameraRig(canvas, reducedMotion);
  const sky = createSky(scene, kit);

  const world = new THREE.Group();
  scene.add(world);

  const sea = new THREE.Mesh(
    new THREE.CircleGeometry(SEA.radius, 48),
    kit.mat(C.seaDeep, { roughness: 0.4 })
  );
  sea.rotation.x = -Math.PI / 2;
  sea.position.y = SEA.y;
  sea.receiveShadow = true;
  world.add(sea);

  const chips = new ChipField(kit, world);
  const bubbles = new BubbleLayer(kit);
  const fleet = createFleet(kit, world);

  const selectionRing = new THREE.Mesh(
    new THREE.RingGeometry(RING.inner, RING.outer, 28),
    new THREE.MeshBasicMaterial({
      color: C.slate,
      transparent: true,
      opacity: 0,
      side: THREE.DoubleSide
    })
  );
  selectionRing.rotation.x = -Math.PI / 2;
  world.add(selectionRing);
  const ringMaterial = selectionRing.material;

  // ---------------- what stands in the world ----------------

  const islands = new Map<string, Island>();
  let current: AtlasWorld | null = null;
  let worldExtent = 20;
  let framed = false;
  let selection: AtlasSelection | null = null;

  const _at = new THREE.Vector3();

  function releaseWorker(build: ItemBuild, figure: WorkerFigure): void {
    bubbles.forget(figure.group);
    kit.release(figure.group);
    build.workers.delete(figure.ticket.id);
  }

  function releaseStele(build: ItemBuild, figure: SteleFigure): void {
    bubbles.forget(figure.group);
    kit.release(figure.group);
    build.steles.delete(figure.ticket.id);
  }

  function releaseItem(build: ItemBuild): void {
    for (const figure of [...build.workers.values()]) releaseWorker(build, figure);
    for (const figure of [...build.steles.values()]) releaseStele(build, figure);
    kit.release(build.pad);
  }

  function releaseIsland(island: Island): void {
    for (const build of island.items.values()) releaseItem(build);
    island.items.clear();
    kit.release(island.group);
  }

  // ---------------- a reading of the board becomes a place ----------------

  function syncItem(island: Island, item: AtlasItem, slotIndex: number): void {
    const position =
      island.lay.positions[Math.min(slotIndex, island.lay.positions.length - 1)];
    const padR = island.lay.pads[Math.min(slotIndex, island.lay.pads.length - 1)];
    const kind = archetypeFor(item.dominantType, item.slot);
    const progressKey = Math.round(item.progress * 20) + (item.allDone ? 100 : 0);

    let build = island.items.get(item.id);
    if (!build) {
      // the item's own ground: a round terrace with the project's trim
      const pad = new THREE.Group();
      pad.position.set(position.x, 0, position.z);
      const padMaterial = kit.mat(island.theme.pad);
      pad.add(kit.cyl(padR, padR + 0.3, 0.5, 20, padMaterial, 0, 0.26, 0));
      pad.add(kit.cyl(padR + 0.12, padR + 0.12, 0.09, 20, kit.M.marbleOld, 0, 0.55, 0));
      const ring = new THREE.Mesh(
        new THREE.RingGeometry(padR - 0.22, padR - 0.06, 24),
        kit.mat(island.accent, { roughness: 0.8 })
      );
      ring.rotation.x = -Math.PI / 2;
      ring.position.y = 0.605;
      pad.add(ring);
      // the pad's ground is the way in: clicking it travels to this Item
      index.tag(pad, { kind: "item", itemId: item.id });
      island.precinct.add(pad);
      const base = new THREE.Group();
      base.position.set(0, 0.62, -padR * 0.18);
      pad.add(base);
      build = {
        item,
        pad,
        base,
        padR,
        kind,
        progressKey: -1,
        structure: null,
        flames: [],
        flameLights: [],
        buildH: 3,
        workers: new Map(),
        steles: new Map(),
        overseer: null,
        fire: null,
        plume: null
      };
      island.items.set(item.id, build);
    }
    build.item = item;
    build.padR = padR;

    if (build.progressKey !== progressKey || build.kind !== kind) {
      if (build.structure) kit.release(build.structure);
      build.kind = kind;
      const built = buildStructure(kit, kind, item.progress);
      // grand, but never past its own ground: the footprint stays on the pad
      const box = new THREE.Box3().setFromObject(built.group);
      const half = Math.max(box.max.x, -box.min.x, box.max.z, -box.min.z, 0.001);
      const fit = Math.min(LAYOUT.buildScale, (padR - LAYOUT.buildPadMargin) / half);
      built.group.scale.setScalar(fit);
      // where the structure's roofline stands
      build.buildH = box.max.y * fit;
      build.base.add(built.group);
      build.structure = built.group;
      build.flames = built.flames;
      build.flameLights = built.flameLights;
      build.progressKey = progressKey;
    }

    const liveTickets = item.tickets.filter((ticket) => !ticket.done);
    liveTickets.forEach((ticket, i) => {
      let figure = build.workers.get(ticket.id);
      if (!figure) {
        figure = makeWorker(kit, index, ticket, island.accent);
        build.pad.add(figure.group);
        build.workers.set(ticket.id, figure);
      }
      figure.ticket = ticket;
      // the crew stands spread across the front arc, human beside the grand work
      const a =
        (i - (liveTickets.length - 1) / 2) * ((1.35 / Math.max(1, liveTickets.length - 1)) * 2);
      const wr = padR * 0.58;
      figure.group.position.set(Math.sin(a) * wr, 0.62, padR * 0.3 + Math.cos(a) * wr * 0.55);
      figure.baseY = 0.62;
      figure.group.rotation.y = ticket.needsMe ? 0 : Math.PI + a * 0.5;
      figure.baseRot = figure.group.rotation.y;
    });
    for (const figure of [...build.workers.values()]) {
      if (!liveTickets.some((ticket) => ticket.id === figure.ticket.id)) {
        releaseWorker(build, figure);
      }
    }

    const doneTickets = item.tickets.filter((ticket) => ticket.done);
    doneTickets.forEach((ticket, i) => {
      let figure = build.steles.get(ticket.id);
      if (!figure) {
        figure = makeStele(kit, index, ticket);
        build.pad.add(figure.group);
        build.steles.set(ticket.id, figure);
      }
      figure.ticket = ticket;
      const sa = 0.85 + i * 0.14;
      figure.group.position.set(Math.cos(sa) * (padR - 0.6), 0.62, Math.sin(sa) * (padR - 0.6));
    });
    for (const figure of [...build.steles.values()]) {
      if (!doneTickets.some((ticket) => ticket.id === figure.ticket.id)) {
        releaseStele(build, figure);
      }
    }

    // the item's overseer, on a dais at the pad's front edge, facing the crew
    if (!build.overseer) {
      build.overseer = makeOverseer(kit, index, item.id, island.accent);
      build.pad.add(build.overseer.group);
    }
    build.overseer.group.position.set(-padR * 0.62, 0.62, padR * 0.62);
    build.overseer.group.rotation.y = Math.PI * 0.75;

    const state = readOverseerState(build.overseer, item, liveTickets);
    if (state.anyTrouble && !build.fire) {
      build.fire = makeTroubleFire(kit, padR);
      build.pad.add(build.fire.group);
    }
    if (build.fire) build.fire.group.visible = state.anyTrouble;
    // the work plume rises only while work truly runs, and never beside fire —
    // trouble owns the sky over a burning structure
    if (state.anyWorking && !state.anyTrouble && !build.plume) {
      build.plume = makeWorkPlume(kit, padR, build.buildH);
      build.base.add(build.plume.group);
    }
    if (build.plume) build.plume.group.visible = state.anyWorking && !state.anyTrouble;
  }

  function sync(reading: AtlasWorld): void {
    const lays = reading.projects.map((project) => islandLayout(project.items));
    const radii = lays.map((lay) => lay.radius);
    const { placed, extent } = placeIslands(radii);
    worldExtent = extent;
    // haze begins beyond the home framing, wherever that is for this world
    fog.near = Math.max(FOG.minNear, rig.frameDistance(extent) * FOG.nearFraction);
    fog.far = fog.near + FOG.depth;

    reading.projects.forEach((project, pi) => {
      let island = islands.get(project.id);
      if (!island) {
        const made = makeIsland(kit, index, project, lays[pi]);
        world.add(made.group);
        island = {
          project,
          group: made.group,
          precinct: made.precinct,
          accent: made.accent,
          theme: made.theme,
          lay: lays[pi],
          foamA: made.foamA,
          foamB: made.foamB,
          namePlate: made.namePlate,
          items: new Map(),
          lodOn: null,
          lodFade: 1
        };
        islands.set(project.id, island);
      }
      island.project = project;
      island.group.position.set(placed[pi].x, 0, placed[pi].z);
      island.group.rotation.y = placed[pi].rot;

      project.items.forEach((item, idx) => syncItem(island, item, idx));

      const alive = new Set(project.items.map((item) => item.id));
      for (const [id, build] of [...island.items]) {
        if (alive.has(id)) continue;
        releaseItem(build);
        island.items.delete(id);
      }
    });

    // a project that has left the board takes its island with it
    const aliveProjects = new Set(reading.projects.map((project) => project.id));
    for (const [id, island] of [...islands]) {
      if (aliveProjects.has(id)) continue;
      releaseIsland(island);
      islands.delete(id);
    }

    bubbles.flush(performance.now() / 1000, findAnchor);

    if (!framed && reading.projects.length) {
      framed = true;
      rig.frameWorld(extent);
    }
  }

  function findAnchor(ticketId: string): BubbleAnchor | null {
    for (const island of islands.values()) {
      for (const build of island.items.values()) {
        const worker = build.workers.get(ticketId);
        if (worker) return { object: worker.group, lift: 1.5 };
        const stele = build.steles.get(ticketId);
        if (stele) return { object: stele.group, lift: 0.9 };
      }
    }
    return null;
  }

  // ---------------- selection ----------------

  type Located = {
    ring: THREE.Object3D;
    travelTo: THREE.Object3D;
    lift: number;
    distance: number;
  };

  function locate(picked: AtlasSelection): Located | null {
    for (const island of islands.values()) {
      for (const build of island.items.values()) {
        if (picked.kind === "ticket") {
          const worker = build.workers.get(picked.id);
          if (worker) {
            return {
              ring: worker.group,
              travelTo: worker.group,
              lift: ARRIVAL.figureLift,
              distance: ARRIVAL.figureDistance
            };
          }
        } else if (picked.kind === "stele") {
          const stele = build.steles.get(picked.id);
          if (stele) {
            return {
              ring: stele.group,
              travelTo: stele.group,
              lift: ARRIVAL.figureLift,
              distance: ARRIVAL.figureDistance
            };
          }
        } else if (picked.kind === "overseer") {
          if (build.item.id === picked.id && build.overseer) {
            return {
              ring: build.overseer.group,
              travelTo: build.overseer.group,
              lift: ARRIVAL.overseerLift,
              distance: ARRIVAL.overseerDistance
            };
          }
        } else if (picked.kind === "item") {
          if (build.item.id === picked.id) {
            return {
              ring: build.base,
              travelTo: build.pad,
              lift: ARRIVAL.itemLift,
              distance: build.padR * ARRIVAL.itemPadFactor
            };
          }
        }
      }
    }
    return null;
  }

  // Travel to what was chosen, close enough that it genuinely fills the view.
  function putSelection(picked: AtlasSelection | null, travel: boolean): void {
    selection = picked;
    if (!picked) {
      ringMaterial.opacity = 0;
      return;
    }
    const found = locate(picked);
    // an id the world does not have: the intent is kept, and the ring waits
    if (!found || !travel) return;
    found.travelTo.getWorldPosition(_at);
    _at.y += found.lift;
    rig.flyTo(_at, found.distance);
  }

  function travelToIsland(projectId: string): void {
    const island = islands.get(projectId);
    if (!island) return;
    island.group.getWorldPosition(_at);
    _at.y += ARRIVAL.islandLift;
    rig.flyTo(_at, island.lay.radius * ARRIVAL.islandRadiusFactor);
  }

  function selectionOf(tag: PickTag): AtlasSelection | null {
    if (tag.kind === "ticket") return { kind: "ticket", id: tag.ticketId };
    if (tag.kind === "stele") return { kind: "stele", id: tag.ticketId };
    if (tag.kind === "overseer") return { kind: "overseer", id: tag.itemId };
    if (tag.kind === "item") return { kind: "item", id: tag.itemId };
    return null;
  }

  const picker = createPicker({
    canvas,
    camera: rig.camera,
    root: world,
    index,
    onTap(tag) {
      if (!tag) {
        // one click on water lets go of a reading; a second sails home
        if (selection) {
          putSelection(null, false);
          onSelect(null);
        } else {
          rig.flyHome(worldExtent);
        }
        return;
      }
      if (tag.kind === "island") {
        putSelection(null, false);
        onSelect(null);
        travelToIsland(tag.projectId);
        return;
      }
      const picked = selectionOf(tag);
      if (!picked) return;
      putSelection(picked, true);
      onSelect(picked);
    }
  });

  function stepSelectionRing(time: number): void {
    if (!selection) return;
    const found = locate(selection);
    if (!found) {
      // whatever was being read has left the world; the ring waits for it
      ringMaterial.opacity = 0;
      return;
    }
    found.ring.getWorldPosition(_at);
    selectionRing.position.set(_at.x, _at.y + 0.02, _at.z);
    ringMaterial.opacity = RING.pulseBase + Math.sin(time * RING.pulseRate) * RING.pulseSwing;
  }

  // ---------------- size ----------------

  const resize = (): void => {
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    if (width === 0 || height === 0) return;
    renderer.setSize(width, height, false);
    rig.setAspect(width / height);
  };
  const observer = new ResizeObserver(resize);
  observer.observe(canvas);
  resize();

  // ---------------- the loop ----------------

  let frame = 0;
  let last = performance.now();
  let running = true;

  function loop(nowMs: number): void {
    if (!running) return;
    frame = requestAnimationFrame(loop);
    const dt = Math.min(0.1, (nowMs - last) / 1000);
    last = nowMs;
    const time = nowMs / 1000;
    // reduced motion: the world holds still, and only the things that carry
    // meaning — the day, the states, a spoken line — still change
    const anim = reducedMotion ? 0 : time;

    rig.step(dt, time);
    const day = applyDay(sky, scene, fog, nowHour());
    const camera = rig.camera;

    for (const island of islands.values()) {
      island.group.getWorldPosition(_at);
      const d = camera.position.distanceTo(_at);
      // progressive reveal: from afar the overseers hold the scene; come closer
      // and each island's crew resolves into individual, working people
      const near = island.lay.radius * LOD.nearRadiusFactor + LOD.nearMargin;
      const want = d < (island.lodOn === null || island.lodOn ? near + LOD.hysteresis : near);
      island.lodOn = want;
      island.lodFade += ((want ? 1 : 0) - island.lodFade) * Math.min(1, dt * LOD.fadeRate);
      const show = island.lodFade > LOD.hideBelow;

      for (const build of island.items.values()) {
        for (const figure of build.workers.values()) {
          figure.group.visible = show;
          if (show) {
            figure.group.scale.setScalar(figure.scale * Math.max(0.0001, island.lodFade));
            poseWorker(figure, anim, chips);
          }
        }
        for (const stele of build.steles.values()) stele.group.visible = show;
        if (build.overseer) animateOverseer(build.overseer, anim, camera);

        for (const flame of build.flames) {
          flame.mesh.scale.y = 1 + Math.sin(anim * 9 + flame.x) * 0.2;
        }
        for (const light of build.flameLights) {
          light.intensity = (day.sun < 1 ? 1.2 : 0.4) + Math.sin(anim * 11) * 0.15;
        }
        if (build.fire && build.fire.group.visible) {
          for (const flame of build.fire.flames) {
            flame.mesh.scale.y = 1 + Math.sin(anim * 9 + flame.x) * 0.2;
          }
          for (const light of build.fire.flameLights) {
            light.intensity = (day.sun < 1 ? 1.2 : 0.4) + Math.sin(anim * 11) * 0.15;
          }
        }
        if (build.plume && build.plume.group.visible) {
          // the work plume: each mote rises its own step and softly re-forms
          for (const mote of build.plume.motes) {
            const c = (anim * 0.14 + mote.index * 0.25) % 1;
            mote.sprite.position.y = mote.base + (mote.index + c) * mote.step;
            mote.sprite.material.opacity =
              0.2 * Math.sin(Math.PI * Math.min(1, (mote.index + c) / 4)) + 0.04;
            mote.sprite.scale.setScalar((0.7 + (mote.index + c) * 0.4) * mote.scale);
          }
        }
      }

      // living shoreline
      const slot = island.project.slot;
      const pA = 1 + Math.sin(anim * 0.55 + slot) * 0.013;
      const pB = 1 + Math.sin(anim * 0.38 + 2 + slot) * 0.017;
      island.foamA.scale.set(pA, pA, 1);
      island.foamB.scale.set(pB, pB, 1);
      (island.foamA.material as THREE.MeshStandardMaterial).opacity =
        0.62 + Math.sin(anim * 0.55 + 1) * 0.2;
      (island.foamB.material as THREE.MeshStandardMaterial).opacity =
        0.4 + Math.sin(anim * 0.38 + 3) * 0.18;

      // the island's name surfaces when you are far enough to need it
      const target = Math.min(
        LOD.nameMaxOpacity,
        Math.max(0, (d - (near + LOD.nameFrom)) / LOD.nameSpan)
      );
      const plate = island.namePlate.material;
      plate.opacity += (target - plate.opacity) * Math.min(1, dt * LOD.nameFadeRate);
      const k = Math.max(1, d / LOD.nameScaleRange);
      island.namePlate.scale.set(9 * k, 1.67 * k, 1);
    }

    fleet.step(anim, worldExtent);
    if (!reducedMotion) chips.step(dt);
    bubbles.step(time, anim, camera);
    stepSelectionRing(time);
    renderer.render(scene, camera);
  }

  frame = requestAnimationFrame(loop);

  // ---------------- the contract ----------------

  return {
    show(reading: AtlasWorld): void {
      for (const happening of happeningsBetween(current, reading)) bubbles.say(happening);
      current = reading;
      sync(reading);
    },
    select(picked: AtlasSelection | null, selectOptions?: { travel?: boolean }): void {
      putSelection(picked, selectOptions?.travel ?? true);
    },
    home(): void {
      putSelection(null, false);
      rig.flyHome(worldExtent);
      // the drift is part of the default view — no waiting
      rig.resumeDriftNow();
    },
    dispose(): void {
      running = false;
      cancelAnimationFrame(frame);
      observer.disconnect();
      picker.dispose();
      rig.dispose();
      bubbles.dispose();
      chips.dispose();
      for (const island of islands.values()) island.items.clear();
      islands.clear();
      // every geometry, material and texture still standing in the scene, then
      // the shared stock the kit made for all of them, then the shadow maps the
      // lights hold on the card
      kit.release(scene);
      kit.dispose();
      sky.sun.dispose();
      sky.moon.dispose();
      sky.hemi.dispose();
      renderer.dispose();
      renderer.forceContextLoss();
    }
  };
}
