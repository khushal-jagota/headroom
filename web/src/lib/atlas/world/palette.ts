// The colours of the place.
//
// One file, so the whole archipelago can be re-tinted from here. Nothing in this
// module touches the scene; it is names for colours and the one operation that
// mixes two of them.

import * as THREE from "three";

export const C = {
  marble: "#efe9da",
  marbleOld: "#d6cfba",
  terracotta: "#bc6743",
  terracottaDeep: "#9d5334",
  stoneTerrace: "#b0a488",
  stoneCliff: "#84765f",
  sand: "#c9b98e",
  seaDeep: "#2f7186",
  seaShallow: "#57a0a8",
  foam: "#c8e2dc",
  cypress: "#3f5a3c",
  cypressDark: "#324832",
  olive: "#7d8f58",
  oliveTrunk: "#6e5a44",
  slate: "#9aadd2",
  skin: "#d9b48f",
  hair: "#4a3b30",
  wood: "#8a6f4d",
  woodDark: "#6d5639",
  crack: "#2c2620"
} as const;

// One accent per project, taken by its slot. A project keeps its colour for as
// long as it keeps its slot.
export const ACCENTS = ["#5b6b9e", "#a05a52", "#c2954a", "#58897d", "#8a6a9e", "#7f9463"];

export function mixHex(a: string, b: string, t: number): string {
  return "#" + new THREE.Color(a).lerp(new THREE.Color(b), t).getHexString();
}
