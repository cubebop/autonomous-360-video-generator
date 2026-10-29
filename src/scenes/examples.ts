import type { SceneManifest } from "../runtime/types";

export const balloonDriftScene: SceneManifest = {
  id: "balloon-drift",
  title: "Balloon Drift",
  description: "A calm sky-room scene with balloons drifting across the volume.",
  environment: {
    type: "sky-room",
    size: [32, 18, 32],
    color: "#6eb8ff",
    accentColor: "#ffffff"
  },
  camera: {
    position: [0, 1, 14],
    lookAt: [0, 1, 0],
    fov: 45
  },
  render: {
    background: "#89c8ff",
    ambientLight: 1.6,
    directionalLight: 1.0
  },
  export: {
    fps: 30,
    durationSeconds: 20,
    outputName: "balloon-drift.mp4"
  },
  entities: [
    {
      id: "balloon-red",
      label: "Red Balloon",
      asset: {
        kind: "model",
        path: "/assets/balloons/red-balloon.glb",
        scale: [1.2, 1.2, 1.2],
        fallback: {
          primitive: "capsule",
          color: "#ef6f6c"
        }
      },
      position: [-10, 0, -3],
      physics: {
        type: "drift",
        velocity: [0.9, 0.08, 0.06],
        bobAmplitude: 0.6,
        bobFrequency: 1.2,
        spin: [0, 0.15, 0],
        wrapBounds: [14, 5, 10]
      }
    },
    {
      id: "balloon-gold",
      label: "Gold Balloon",
      asset: {
        kind: "model",
        path: "/assets/balloons/gold-balloon.glb",
        scale: [1.1, 1.1, 1.1],
        fallback: {
          primitive: "capsule",
          color: "#ffd166"
        }
      },
      position: [-2, 2, -5],
      physics: {
        type: "drift",
        velocity: [0.6, 0.03, 0.04],
        bobAmplitude: 0.35,
        bobFrequency: 1.8,
        spin: [0.03, 0.08, 0],
        wrapBounds: [14, 5, 10]
      }
    },
    {
      id: "balloon-teal",
      label: "Teal Balloon",
      asset: {
        kind: "primitive",
        primitive: "capsule",
        color: "#58c4c6",
        scale: [1.3, 1.3, 1.3]
      },
      position: [7, -1, 2],
      physics: {
        type: "drift",
        velocity: [0.45, 0.06, -0.05],
        bobAmplitude: 0.45,
        bobFrequency: 1.4,
        spin: [0.01, -0.12, 0],
        wrapBounds: [14, 5, 10]
      }
    }
  ]
};

export const asteroidCollisionScene: SceneManifest = {
  id: "asteroid-collision",
  title: "Asteroid Collision",
  description: "A space-box scene with simple asteroid collision motion.",
  environment: {
    type: "space-box",
    size: [36, 20, 36],
    color: "#040712",
    starColor: "#d8ecff",
    starCount: 450
  },
  camera: {
    position: [0, 0, 18],
    lookAt: [0, 0, 0],
    fov: 50
  },
  render: {
    background: "#02040a",
    ambientLight: 0.9,
    directionalLight: 1.3
  },
  export: {
    fps: 30,
    durationSeconds: 20,
    outputName: "asteroid-collision.mp4"
  },
  entities: [
    {
      id: "asteroid-1",
      asset: {
        kind: "model",
        path: "/assets/asteroids/asteroid-1.glb",
        scale: [1.4, 1.4, 1.4],
        fallback: {
          primitive: "sphere",
          color: "#8a7f7b"
        }
      },
      position: [-8, 1, 0],
      physics: {
        type: "collision",
        velocity: [1.5, 0.5, 0.8],
        radius: 1.5,
        bounds: [13, 7, 9],
        spin: [0.5, 0.3, 0.2]
      }
    },
    {
      id: "asteroid-2",
      asset: {
        kind: "primitive",
        primitive: "sphere",
        color: "#c19b73",
        scale: [1.8, 1.2, 1.4]
      },
      position: [6, -2, 1],
      physics: {
        type: "collision",
        velocity: [-1.1, 0.8, -0.6],
        radius: 1.35,
        bounds: [13, 7, 9],
        spin: [0.2, -0.4, 0.35]
      }
    },
    {
      id: "asteroid-3",
      asset: {
        kind: "primitive",
        primitive: "sphere",
        color: "#8f8f92",
        scale: [1.2, 1.6, 1.25]
      },
      position: [1, 3, -4],
      physics: {
        type: "collision",
        velocity: [0.9, -1.0, 0.7],
        radius: 1.25,
        bounds: [13, 7, 9],
        spin: [0.35, 0.22, -0.3]
      }
    }
  ]
};
