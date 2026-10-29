import "./style.css";
import { SceneRuntime } from "./runtime/SceneRuntime";
import { loadManifest } from "./runtime/manifestLoader";
import { asteroidCollisionScene, balloonDriftScene } from "./scenes/examples";
import type { SceneManifest } from "./runtime/types";

const app = document.querySelector<HTMLDivElement>("#app");

if (!app) {
  throw new Error("Missing #app root element");
}

const search = new URLSearchParams(window.location.search);

const fallbackManifest =
  (search.get("scene") ?? "balloons") === "asteroids"
    ? asteroidCollisionScene
    : balloonDriftScene;

declare global {
  interface Window {
    __relaxingRetroExport?: {
      manifest: SceneManifest;
      renderFrame: (deltaSeconds: number) => void;
      ready: boolean;
    };
  }
}

loadManifest(search, fallbackManifest)
  .then((manifest) => {
    const runtime = new SceneRuntime(app, manifest);
    const exportMode = search.get("export") === "1";

    if (exportMode) {
      document.body.classList.add("export-mode");
      return runtime.initialize().then(() => {
        window.__relaxingRetroExport = {
          manifest,
          renderFrame: (deltaSeconds: number) => runtime.step(deltaSeconds),
          ready: true
        };
      });
    }

    return runtime.start();
  })
  .catch((error) => {
    console.error("Failed to boot Autonomous 360 Video Generator runtime", error);
  });
