// Where the eye stands, and how it gets there.
//
// Every journey eases both where we look and how close we stand, so arriving means
// the thing genuinely fills the view. Left alone, the world drifts: the idle orbit
// runs at every scale, around whatever we last travelled to, a few breaths after
// arriving and later after a manual drag. Its angular speed rises as we come close,
// so the drift reads the same however near we stand.

import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { CAMERA } from "./tuning";

type Flight = { to: THREE.Vector3; dist: number };

export type CameraRig = {
  camera: THREE.PerspectiveCamera;
  controls: OrbitControls;
  frameDistance: (extent: number) => number;
  frameWorld: (extent: number) => void;
  flyTo: (target: THREE.Vector3, distance: number) => void;
  flyHome: (extent: number) => void;
  // the wordmark's way home: the drift is part of the default view, no waiting
  resumeDriftNow: () => void;
  setAspect: (aspect: number) => void;
  step: (dt: number, time: number) => void;
  dispose: () => void;
};

export function createCameraRig(
  canvas: HTMLCanvasElement,
  reducedMotion: boolean
): CameraRig {
  const camera = new THREE.PerspectiveCamera(CAMERA.fov, 1, CAMERA.near, CAMERA.far);
  camera.position.set(CAMERA.start.x, CAMERA.start.y, CAMERA.start.z);

  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = true;
  controls.dampingFactor = CAMERA.dampingFactor;
  controls.minDistance = CAMERA.minDistance;
  controls.maxDistance = CAMERA.maxDistance;
  controls.maxPolarAngle = CAMERA.maxPolarAngle;
  controls.minPolarAngle = CAMERA.minPolarAngle;
  controls.target.set(0, CAMERA.targetY, 0);
  controls.autoRotate = !reducedMotion;
  controls.autoRotateSpeed = 0.25;

  let lastInteract = -60;
  let flight: Flight | null = null;

  const onStart = (): void => {
    lastInteract = performance.now() / 1000;
    controls.autoRotate = false;
    flight = null;
  };
  controls.addEventListener("start", onStart);

  const frameDistance = (extent: number): number => {
    const vfov = (camera.fov * Math.PI) / 180;
    const hfov = 2 * Math.atan(Math.tan(vfov / 2) * camera.aspect);
    const d = ((extent + CAMERA.framePadding) / Math.tan(Math.min(vfov, hfov) / 2)) * 0.5;
    return Math.min(CAMERA.frameMax, Math.max(CAMERA.frameMin, d));
  };

  const flyTo = (target: THREE.Vector3, distance: number): void => {
    flight = {
      to: target.clone(),
      dist: Math.min(controls.maxDistance, Math.max(controls.minDistance, distance))
    };
    controls.autoRotate = false;
  };

  return {
    camera,
    controls,
    frameDistance,
    frameWorld(extent: number): void {
      const dist = frameDistance(extent);
      const az = CAMERA.frameAzimuth;
      const pol = CAMERA.framePolar;
      camera.position.set(
        Math.sin(pol) * Math.cos(az) * dist,
        Math.cos(pol) * dist + 1,
        Math.sin(pol) * Math.sin(az) * dist
      );
      controls.target.set(0, CAMERA.targetY, 0);
      controls.update();
    },
    flyTo,
    flyHome(extent: number): void {
      flyTo(new THREE.Vector3(0, CAMERA.targetY, 0), frameDistance(extent));
    },
    resumeDriftNow(): void {
      lastInteract = -1e9;
    },
    setAspect(aspect: number): void {
      camera.aspect = aspect;
      camera.updateProjectionMatrix();
    },
    step(dt: number, time: number): void {
      if (!reducedMotion && !flight && time - lastInteract > CAMERA.idleAfterInteraction) {
        controls.autoRotate = true;
      }
      const focus = camera.position.distanceTo(controls.target);
      controls.autoRotateSpeed =
        CAMERA.autoRotateSlow +
        CAMERA.autoRotateGain *
          Math.max(0, Math.min(1, (CAMERA.autoRotateNearDistance - focus) / CAMERA.autoRotateSpan));
      if (flight) {
        const k = 1 - Math.exp(-CAMERA.flightEase * dt);
        controls.target.lerp(flight.to, k);
        const dir = camera.position.clone().sub(controls.target);
        const len = dir.length();
        const nd = len + (flight.dist - len) * k;
        camera.position.copy(controls.target).add(dir.normalize().multiplyScalar(nd));
        if (
          controls.target.distanceTo(flight.to) < CAMERA.arriveTargetDistance &&
          Math.abs(nd - flight.dist) < CAMERA.arriveRangeDistance
        ) {
          flight = null;
          // the drift resumes a few breaths after arrival
          lastInteract = Math.min(
            lastInteract,
            time - (CAMERA.idleAfterInteraction - CAMERA.idleAfterArrival)
          );
        }
      }
      // dt keeps the drift rate independent of frame rate
      controls.update(dt);
    },
    dispose(): void {
      controls.removeEventListener("start", onStart);
      controls.dispose();
    }
  };
}
