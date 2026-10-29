import {
  BackSide,
  BoxGeometry,
  BufferGeometry,
  Euler,
  Float32BufferAttribute,
  Material,
  Mesh,
  MeshBasicMaterial,
  Object3D,
  Points,
  PointsMaterial,
  Scene
} from "three";
import type { Texture } from "three";
import { createAssetObject } from "./AssetFactory";
import type { EnvironmentConfig } from "./types";

function addSkyRoom(scene: Scene, environment: Extract<EnvironmentConfig, { type: "sky-room" }>) {
  const room = new Mesh(
    new BoxGeometry(...environment.size),
    new MeshBasicMaterial({
      color: environment.color,
      side: BackSide
    })
  );

  const clouds = new Mesh(
    new BoxGeometry(
      environment.size[0] * 0.96,
      environment.size[1] * 0.96,
      environment.size[2] * 0.96
    ),
    new MeshBasicMaterial({
      color: environment.accentColor,
      side: BackSide,
      transparent: true,
      opacity: 0.12
    })
  );

  scene.add(room, clouds);
}

function addSpaceBox(scene: Scene, environment: Extract<EnvironmentConfig, { type: "space-box" }>) {
  const room = new Mesh(
    new BoxGeometry(...environment.size),
    new MeshBasicMaterial({
      color: environment.color,
      side: BackSide
    })
  );

  const positions: number[] = [];
  const [width, height, depth] = environment.size;

  for (let index = 0; index < environment.starCount; index += 1) {
    positions.push(
      (Math.random() - 0.5) * width * 0.95,
      (Math.random() - 0.5) * height * 0.95,
      (Math.random() - 0.5) * depth * 0.95
    );
  }

  const starsGeometry = new BufferGeometry();
  starsGeometry.setAttribute("position", new Float32BufferAttribute(positions, 3));

  const stars = new Points(
    starsGeometry,
    new PointsMaterial({
      color: environment.starColor,
      size: 0.18,
      sizeAttenuation: true
    })
  );

  scene.add(room, stars);
}

async function addModelEnvironment(
  scene: Scene,
  environment: Extract<EnvironmentConfig, { type: "model" }>
) {
  const object: Object3D = await createAssetObject(environment.asset);
  const position = environment.position ?? [0, 0, 0];
  const rotation = environment.rotation ?? [0, 0, 0];

  object.position.set(...position);
  object.rotation.copy(new Euler(...rotation));

  object.traverse((child) => {
    if (!("isMesh" in child) || !child.isMesh) {
      return;
    }

    const mesh = child as Mesh;
    const material = mesh.material as Material | Material[] | undefined;
    const sourceMaterial = Array.isArray(material) ? material[0] : material;

    const materialWithTextures = sourceMaterial as (Material & {
      map?: Texture | null;
      emissiveMap?: Texture | null;
    }) | undefined;
    const texture = materialWithTextures?.map ?? materialWithTextures?.emissiveMap ?? null;

    // Legacy path: only override when an actual texture is baked into the GLB
    // (e.g. the old sky_blue_room.glb). For untextured PBR environments — our
    // current procedural sky cube + checker floor — leave the material alone
    // so multi-primitive meshes render correctly from any camera angle.
    if (!texture) {
      return;
    }

    mesh.material = new MeshBasicMaterial({
      map: texture,
      color: 0xffffff,
      side: BackSide
    });
  });

  scene.add(object);
}

export async function addEnvironment(scene: Scene, environment: EnvironmentConfig) {
  if (environment.type === "sky-room") {
    addSkyRoom(scene, environment);
    return;
  }

  if (environment.type === "space-box") {
    addSpaceBox(scene, environment);
    return;
  }

  await addModelEnvironment(scene, environment);
}
