// The workshop: the materials, the two shapes everything is cut from, and the
// canvas textures the world draws for itself.
//
// One kit belongs to one scene. Every material and texture the world makes passes
// through it, so when the screen closes there is a single place that gives all of
// it back to the browser. Shared stock — the marble, the terracotta, the smoke —
// is owned by the kit and outlives any one object; everything else belongs to the
// object it was made for and is released with it.

import * as THREE from "three";
import { C } from "./palette";

type Releasable = { dispose: () => void };

type Drawable = THREE.Object3D & {
  geometry: THREE.BufferGeometry;
  material: THREE.Material | THREE.Material[];
};

function isDrawable(object: THREE.Object3D): object is Drawable {
  return "geometry" in object && "material" in object;
}

export type SharedMaterials = {
  marble: THREE.MeshStandardMaterial;
  marbleOld: THREE.MeshStandardMaterial;
  terracotta: THREE.MeshStandardMaterial;
  terracottaDeep: THREE.MeshStandardMaterial;
  terrace: THREE.MeshStandardMaterial;
  cliff: THREE.MeshStandardMaterial;
  sand: THREE.MeshStandardMaterial;
  cypress: THREE.MeshStandardMaterial;
  cypressDark: THREE.MeshStandardMaterial;
  olive: THREE.MeshStandardMaterial;
  trunk: THREE.MeshStandardMaterial;
  wood: THREE.MeshStandardMaterial;
  woodDark: THREE.MeshStandardMaterial;
  skin: THREE.MeshStandardMaterial;
  hair: THREE.MeshStandardMaterial;
  slate: THREE.MeshStandardMaterial;
  crack: THREE.MeshStandardMaterial;
};

export type SharedTextures = {
  smoke: THREE.CanvasTexture;
  spark: THREE.CanvasTexture;
  foam: THREE.CanvasTexture;
  slateOrb: THREE.CanvasTexture;
};

function drawnTexture(
  size: number,
  paint: (context: CanvasRenderingContext2D, canvas: HTMLCanvasElement) => void
): THREE.CanvasTexture {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const context = canvas.getContext("2d");
  if (context) paint(context, canvas);
  return new THREE.CanvasTexture(canvas);
}

function smokeTexture(): THREE.CanvasTexture {
  return drawnTexture(64, (g) => {
    const gradient = g.createRadialGradient(32, 32, 4, 32, 32, 30);
    gradient.addColorStop(0, "rgba(70,70,74,0.85)");
    gradient.addColorStop(1, "rgba(70,70,74,0)");
    g.fillStyle = gradient;
    g.fillRect(0, 0, 64, 64);
  });
}

function sparkTexture(): THREE.CanvasTexture {
  return drawnTexture(32, (g) => {
    g.strokeStyle = "rgba(255,255,250,0.95)";
    g.lineWidth = 3;
    g.beginPath();
    g.moveTo(16, 2);
    g.lineTo(16, 30);
    g.moveTo(2, 16);
    g.lineTo(30, 16);
    g.stroke();
  });
}

// foam-white radial glow, for wakes and water light
function foamTexture(): THREE.CanvasTexture {
  return drawnTexture(64, (g) => {
    const gradient = g.createRadialGradient(32, 32, 4, 32, 32, 30);
    gradient.addColorStop(0, "rgba(230,244,240,0.9)");
    gradient.addColorStop(1, "rgba(230,244,240,0)");
    g.fillStyle = gradient;
    g.fillRect(0, 0, 64, 64);
  });
}

// a slate beacon: the one mark for needing the human, readable from any range
function slateOrbTexture(): THREE.CanvasTexture {
  return drawnTexture(96, (g) => {
    const gradient = g.createRadialGradient(48, 48, 6, 48, 48, 46);
    gradient.addColorStop(0, "rgba(205,216,232,1)");
    gradient.addColorStop(0.35, "rgba(154,173,210,0.95)");
    gradient.addColorStop(1, "rgba(154,173,210,0)");
    g.fillStyle = gradient;
    g.fillRect(0, 0, 96, 96);
  });
}

export class AtlasKit {
  private readonly owned = new Set<Releasable>();
  readonly M: SharedMaterials;
  readonly tex: SharedTextures;

  constructor() {
    const shared = (material: THREE.MeshStandardMaterial): THREE.MeshStandardMaterial =>
      this.own(material);
    this.M = {
      marble: shared(this.mat(C.marble, { roughness: 0.7 })),
      marbleOld: shared(this.mat(C.marbleOld, { roughness: 0.8 })),
      terracotta: shared(this.mat(C.terracotta)),
      terracottaDeep: shared(this.mat(C.terracottaDeep)),
      terrace: shared(this.mat(C.stoneTerrace)),
      cliff: shared(this.mat(C.stoneCliff)),
      sand: shared(this.mat(C.sand)),
      cypress: shared(this.mat(C.cypress)),
      cypressDark: shared(this.mat(C.cypressDark)),
      olive: shared(this.mat(C.olive)),
      trunk: shared(this.mat(C.oliveTrunk)),
      wood: shared(this.mat(C.wood)),
      woodDark: shared(this.mat(C.woodDark)),
      skin: shared(this.mat(C.skin, { roughness: 0.75 })),
      hair: shared(this.mat(C.hair)),
      slate: shared(this.mat(C.slate, { roughness: 0.6 })),
      crack: shared(this.mat(C.crack, { roughness: 1 }))
    };
    this.tex = {
      smoke: this.own(smokeTexture()),
      spark: this.own(sparkTexture()),
      foam: this.own(foamTexture()),
      slateOrb: this.own(slateOrbTexture())
    };
  }

  // Stock the kit keeps: shared by many objects, released only with the scene.
  own<T extends Releasable>(thing: T): T {
    this.owned.add(thing);
    return thing;
  }

  mat(
    color: THREE.ColorRepresentation,
    options?: THREE.MeshStandardMaterialParameters
  ): THREE.MeshStandardMaterial {
    return new THREE.MeshStandardMaterial({
      color,
      roughness: 0.9,
      metalness: 0.0,
      flatShading: true,
      ...(options ?? {})
    });
  }

  box(
    w: number,
    h: number,
    d: number,
    material: THREE.Material,
    x = 0,
    y = 0,
    z = 0
  ): THREE.Mesh {
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), material);
    mesh.position.set(x, y, z);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    return mesh;
  }

  cyl(
    radiusTop: number,
    radiusBottom: number,
    h: number,
    segments: number,
    material: THREE.Material,
    x = 0,
    y = 0,
    z = 0
  ): THREE.Mesh {
    const mesh = new THREE.Mesh(
      new THREE.CylinderGeometry(radiusTop, radiusBottom, h, segments),
      material
    );
    mesh.position.set(x, y, z);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    return mesh;
  }

  // A canvas the world drew for itself: a carved name, a spoken line.
  texture(
    width: number,
    height: number,
    paint: (context: CanvasRenderingContext2D) => void
  ): THREE.CanvasTexture {
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d");
    if (context) paint(context);
    const texture = new THREE.CanvasTexture(canvas);
    texture.anisotropy = 4;
    return texture;
  }

  // Take an object out of the world and give back everything only it was using.
  release(object: THREE.Object3D): void {
    object.removeFromParent();
    object.traverse((node) => {
      if (!isDrawable(node)) return;
      if (node.geometry && !this.owned.has(node.geometry)) node.geometry.dispose();
      const materials = Array.isArray(node.material) ? node.material : [node.material];
      for (const material of materials) {
        if (!material || this.owned.has(material)) continue;
        const map = (material as THREE.MeshBasicMaterial).map;
        if (map && !this.owned.has(map)) map.dispose();
        material.dispose();
      }
    });
  }

  dispose(): void {
    for (const thing of this.owned) thing.dispose();
    this.owned.clear();
  }
}
