// The sky tracks the real clock.
//
// A handful of keyframes through the day — background, haze, sun strength and
// colour, ambient level, stars — read at the current hour and interpolated. The
// sun and moon ride an arc from them, so late afternoon really is late afternoon
// and midnight is dark enough to want the beacons.

import * as THREE from "three";
import { AtlasKit } from "./materials";
import { mulberry32 } from "./random";
import { DAY_KEYS, SKY } from "./tuning";

export type DayLight = {
  bg: THREE.Color;
  fog: THREE.Color;
  sunC: THREE.Color;
  sun: number;
  hemi: number;
  stars: number;
};

export function dayAt(hour: number): DayLight {
  let a = DAY_KEYS[0];
  let b = DAY_KEYS[DAY_KEYS.length - 1];
  for (let i = 0; i < DAY_KEYS.length - 1; i++) {
    if (hour >= DAY_KEYS[i].h && hour <= DAY_KEYS[i + 1].h) {
      a = DAY_KEYS[i];
      b = DAY_KEYS[i + 1];
      break;
    }
  }
  const t = b.h === a.h ? 0 : (hour - a.h) / (b.h - a.h);
  const col = (x: string, y: string): THREE.Color =>
    new THREE.Color(x).lerp(new THREE.Color(y), t);
  return {
    bg: col(a.bg, b.bg),
    fog: col(a.fog, b.fog),
    sunC: col(a.sunC, b.sunC),
    sun: a.sun + (b.sun - a.sun) * t,
    hemi: a.hemi + (b.hemi - a.hemi) * t,
    stars: a.stars + (b.stars - a.stars) * t
  };
}

export function nowHour(): number {
  const d = new Date();
  return d.getHours() + d.getMinutes() / 60 + d.getSeconds() / 3600;
}

export type Sky = {
  hemi: THREE.HemisphereLight;
  sun: THREE.DirectionalLight;
  moon: THREE.DirectionalLight;
  stars: THREE.Points;
  sunDisc: THREE.Mesh;
  moonDisc: THREE.Mesh;
};

export function createSky(scene: THREE.Scene, kit: AtlasKit): Sky {
  const hemi = new THREE.HemisphereLight("#cfe0ec", "#8a7a60", 0.9);
  scene.add(hemi);

  const sun = new THREE.DirectionalLight("#fff6e6", 2.4);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  sun.shadow.camera.left = -45;
  sun.shadow.camera.right = 45;
  sun.shadow.camera.top = 45;
  sun.shadow.camera.bottom = -45;
  sun.shadow.camera.near = 1;
  sun.shadow.camera.far = 170;
  sun.shadow.bias = -0.0004;
  scene.add(sun, sun.target);

  const moon = new THREE.DirectionalLight("#a9c0e8", 0.0);
  scene.add(moon, moon.target);

  const count = SKY.starCount;
  const positions = new Float32Array(count * 3);
  const rng = mulberry32(SKY.starSeed);
  for (let i = 0; i < count; i++) {
    const a = rng() * Math.PI * 2;
    const e = Math.asin(rng() * 0.95);
    const r = SKY.starRadius;
    positions[i * 3] = Math.cos(a) * Math.cos(e) * r;
    positions[i * 3 + 1] = Math.sin(e) * r * 0.9 + 4;
    positions[i * 3 + 2] = Math.sin(a) * Math.cos(e) * r;
  }
  const starGeometry = new THREE.BufferGeometry();
  starGeometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  const starMaterial = new THREE.PointsMaterial({
    color: "#e8ecf4",
    size: 1.6,
    sizeAttenuation: false,
    transparent: true,
    opacity: 0,
    fog: false
  });
  const stars = new THREE.Points(starGeometry, starMaterial);
  scene.add(stars);

  const sunDisc = new THREE.Mesh(
    new THREE.SphereGeometry(2.6, 12, 12),
    new THREE.MeshBasicMaterial({ color: "#fff3da", fog: false })
  );
  const moonDisc = new THREE.Mesh(
    new THREE.SphereGeometry(1.8, 12, 12),
    new THREE.MeshBasicMaterial({ color: "#dfe6f2", fog: false })
  );
  scene.add(sunDisc, moonDisc);

  // the kit knows nothing it did not make: hand it the pieces built here
  kit.own(starGeometry);
  kit.own(starMaterial);

  return { hemi, sun, moon, stars, sunDisc, moonDisc };
}

export function applyDay(sky: Sky, scene: THREE.Scene, fog: THREE.Fog, hour: number): DayLight {
  const key = dayAt(hour);
  scene.background = key.bg;
  fog.color.copy(key.fog);
  sky.hemi.intensity = key.hemi;
  (sky.stars.material as THREE.PointsMaterial).opacity = key.stars * 0.9;

  const dayFrac = Math.min(1, Math.max(0, (hour - 6) / 14));
  const sunA = Math.PI * 1.05 - dayFrac * Math.PI * 1.1;
  const sunE = Math.max(0.06, Math.sin(Math.PI * dayFrac));
  const sr = SKY.orbitRadius;
  sky.sun.position.set(
    Math.cos(sunA) * sr,
    sunE * SKY.sunHeight + SKY.sunBase,
    Math.sin(sunA) * sr * 0.6 + 10
  );
  sky.sun.intensity = key.sun;
  sky.sun.color.copy(key.sunC);
  sky.sunDisc.position.copy(sky.sun.position).multiplyScalar(SKY.sunDiscDistance);
  sky.sunDisc.visible = key.sun > 0.05;

  const nightFrac = ((hour < 6 ? hour + 24 : hour) - 20) / 10;
  const night = Math.min(1, Math.max(0, nightFrac));
  const moonA = Math.PI * 1.05 - night * Math.PI * 1.1;
  const moonE = Math.max(0.1, Math.sin(Math.PI * night));
  sky.moon.position.set(
    Math.cos(moonA) * sr,
    moonE * SKY.moonHeight + SKY.moonBase,
    Math.sin(moonA) * sr * 0.6 + 10
  );
  sky.moon.intensity = key.sun > 0.05 ? 0 : SKY.moonIntensity;
  sky.moonDisc.position.copy(sky.moon.position).multiplyScalar(SKY.moonDiscDistance);
  sky.moonDisc.visible = sky.moon.intensity > 0;

  return key;
}
