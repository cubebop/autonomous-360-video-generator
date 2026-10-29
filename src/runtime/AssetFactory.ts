import {
  BoxGeometry,
  CapsuleGeometry,
  Color,
  Mesh,
  MeshStandardMaterial,
  Object3D as ThreeObject3D,
  Object3D,
  SphereGeometry
} from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import type { AssetDescriptor } from "./types";

const gltfLoader = new GLTFLoader();

function createPrimitive(asset: Extract<AssetDescriptor, { kind: "primitive" }>): Object3D {
  let mesh: Mesh;

  if (asset.primitive === "sphere") {
    mesh = new Mesh(
      new SphereGeometry(0.8, 32, 24),
      new MeshStandardMaterial({ color: asset.color })
    );
  } else if (asset.primitive === "capsule") {
    mesh = new Mesh(
      new CapsuleGeometry(0.45, 1.2, 8, 16),
      new MeshStandardMaterial({ color: asset.color })
    );
  } else {
    mesh = new Mesh(
      new BoxGeometry(1.4, 1.4, 1.4),
      new MeshStandardMaterial({ color: asset.color })
    );
  }

  mesh.castShadow = true;
  mesh.receiveShadow = true;

  if (asset.scale) {
    mesh.scale.set(...asset.scale);
  }

  return mesh;
}

function createFallbackModel(
  asset: Extract<AssetDescriptor, { kind: "model" }>
): Object3D {
  const fallback = asset.fallback ?? { primitive: "box", color: new Color("#ff7b7b") };

  return createPrimitive({
    kind: "primitive",
    primitive: fallback.primitive,
    color: fallback.color,
    scale: asset.scale
  });
}

export async function createAssetObject(asset: AssetDescriptor): Promise<Object3D> {
  if (asset.kind === "primitive") {
    return createPrimitive(asset);
  }

  try {
    const gltf = await gltfLoader.loadAsync(asset.path);
    const root = gltf.scene;

    if (asset.scale) {
      root.scale.set(...asset.scale);
    }

    root.traverse((child: ThreeObject3D) => {
      if ("castShadow" in child) {
        child.castShadow = true;
      }

      if ("receiveShadow" in child) {
        child.receiveShadow = true;
      }
    });

    return root;
  } catch (error) {
    console.warn(`Falling back for missing model: ${asset.path}`, error);
    return createFallbackModel(asset);
  }
}
