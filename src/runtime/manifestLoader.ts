import type { SceneManifest } from "./types";

export async function loadManifest(
  search: URLSearchParams,
  fallbackManifest: SceneManifest
): Promise<SceneManifest> {
  const manifestPath = search.get("manifest");

  if (!manifestPath) {
    return fallbackManifest;
  }

  const response = await fetch(manifestPath);

  if (!response.ok) {
    throw new Error(`Unable to load manifest: ${manifestPath}`);
  }

  return (await response.json()) as SceneManifest;
}
