import type { ColorRepresentation, Vector3Tuple } from "three";

export type PhysicsBehaviorType = "drift" | "collision";

export type AssetDescriptor =
  | {
      kind: "primitive";
      primitive: "sphere" | "box" | "capsule";
      color: ColorRepresentation;
      scale?: Vector3Tuple;
    }
  | {
      kind: "model";
      path: string;
      scale?: Vector3Tuple;
      fallback?: {
        primitive: "sphere" | "box" | "capsule";
        color: ColorRepresentation;
      };
    };

export interface DriftPhysicsConfig {
  type: "drift";
  velocity: Vector3Tuple;
  bobAmplitude?: number;
  bobFrequency?: number;
  spin?: Vector3Tuple;
  wrapBounds?: Vector3Tuple;
}

export interface CollisionPhysicsConfig {
  type: "collision";
  velocity: Vector3Tuple;
  radius: number;
  bounds: Vector3Tuple;
  spin?: Vector3Tuple;
}

export type PhysicsConfig = DriftPhysicsConfig | CollisionPhysicsConfig;

export interface SceneEntityConfig {
  id: string;
  label?: string;
  asset: AssetDescriptor;
  position: Vector3Tuple;
  rotation?: Vector3Tuple;
  physics: PhysicsConfig;
}

export interface SkyRoomEnvironment {
  type: "sky-room";
  size: Vector3Tuple;
  color: ColorRepresentation;
  accentColor: ColorRepresentation;
}

export interface SpaceBoxEnvironment {
  type: "space-box";
  size: Vector3Tuple;
  color: ColorRepresentation;
  starColor: ColorRepresentation;
  starCount: number;
}

export interface ModelEnvironment {
  type: "model";
  asset: AssetDescriptor;
  position?: Vector3Tuple;
  rotation?: Vector3Tuple;
}

export type EnvironmentConfig = SkyRoomEnvironment | SpaceBoxEnvironment | ModelEnvironment;

export interface CameraConfig {
  position: Vector3Tuple;
  lookAt: Vector3Tuple;
  fov?: number;
}

export interface RenderConfig {
  background: ColorRepresentation;
  ambientLight?: number;
  directionalLight?: number;
}

export interface ExportConfig {
  fps: number;
  width?: number;
  height?: number;
  durationSeconds: number;
  outputName: string;
  audioPath?: string;
  audioVolume?: number;
}

export interface SceneManifest {
  id: string;
  title: string;
  description: string;
  environment: EnvironmentConfig;
  camera: CameraConfig;
  render: RenderConfig;
  export: ExportConfig;
  entities: SceneEntityConfig[];
}
