import { Euler, Vector3 } from "three";
import type { Object3D } from "three";
import type { CollisionPhysicsConfig, DriftPhysicsConfig, SceneEntityConfig } from "./types";

export interface RuntimeEntity {
  config: SceneEntityConfig;
  object: Object3D;
  velocity: Vector3;
  seed: number;
}

function hashSeed(id: string): number {
  return [...id].reduce((total, char) => total + char.charCodeAt(0), 0);
}

function applyDrift(entity: RuntimeEntity, deltaSeconds: number, elapsedSeconds: number) {
  const config = entity.config.physics as DriftPhysicsConfig;
  entity.object.position.addScaledVector(entity.velocity, deltaSeconds);

  const bobAmplitude = config.bobAmplitude ?? 0;
  const bobFrequency = config.bobFrequency ?? 0;
  if (bobAmplitude > 0 && bobFrequency > 0) {
    entity.object.position.y +=
      Math.sin(elapsedSeconds * bobFrequency + entity.seed * 0.1) * bobAmplitude * deltaSeconds;
  }

  if (config.spin) {
    entity.object.rotation.x += config.spin[0] * deltaSeconds;
    entity.object.rotation.y += config.spin[1] * deltaSeconds;
    entity.object.rotation.z += config.spin[2] * deltaSeconds;
  }

  if (config.wrapBounds) {
    const [boundX, boundY, boundZ] = config.wrapBounds;
    // When an entity wraps along its travel axis, randomize the perpendicular
    // axes within ~80% of their bounds so each pass appears at a fresh position.
    const randIn = (b: number) => (Math.random() * 1.6 - 0.8) * b;

    if (entity.object.position.x > boundX) {
      entity.object.position.x = -boundX;
      entity.object.position.y = randIn(boundY);
      entity.object.position.z = randIn(boundZ);
    } else if (entity.object.position.x < -boundX) {
      entity.object.position.x = boundX;
      entity.object.position.y = randIn(boundY);
      entity.object.position.z = randIn(boundZ);
    }

    if (entity.object.position.y > boundY) {
      entity.object.position.y = -boundY;
      entity.object.position.x = randIn(boundX);
      entity.object.position.z = randIn(boundZ);
    } else if (entity.object.position.y < -boundY) {
      entity.object.position.y = boundY;
      entity.object.position.x = randIn(boundX);
      entity.object.position.z = randIn(boundZ);
    }

    if (entity.object.position.z > boundZ) {
      entity.object.position.z = -boundZ;
      entity.object.position.x = randIn(boundX);
      entity.object.position.y = randIn(boundY);
    } else if (entity.object.position.z < -boundZ) {
      entity.object.position.z = boundZ;
      entity.object.position.x = randIn(boundX);
      entity.object.position.y = randIn(boundY);
    }
  }
}

function applyCollisions(entities: RuntimeEntity[], deltaSeconds: number) {
  for (let index = 0; index < entities.length; index += 1) {
    const entity = entities[index];
    const config = entity.config.physics as CollisionPhysicsConfig;

    entity.object.position.addScaledVector(entity.velocity, deltaSeconds);

    if (config.spin) {
      entity.object.rotation.x += config.spin[0] * deltaSeconds;
      entity.object.rotation.y += config.spin[1] * deltaSeconds;
      entity.object.rotation.z += config.spin[2] * deltaSeconds;
    }

    const [boundX, boundY, boundZ] = config.bounds;

    if (Math.abs(entity.object.position.x) > boundX) {
      entity.velocity.x *= -1;
      entity.object.position.x = Math.sign(entity.object.position.x) * boundX;
    }

    if (Math.abs(entity.object.position.y) > boundY) {
      entity.velocity.y *= -1;
      entity.object.position.y = Math.sign(entity.object.position.y) * boundY;
    }

    if (Math.abs(entity.object.position.z) > boundZ) {
      entity.velocity.z *= -1;
      entity.object.position.z = Math.sign(entity.object.position.z) * boundZ;
    }
  }

  for (let left = 0; left < entities.length; left += 1) {
    const a = entities[left];
    const physicsA = a.config.physics as CollisionPhysicsConfig;

    for (let right = left + 1; right < entities.length; right += 1) {
      const b = entities[right];
      const physicsB = b.config.physics as CollisionPhysicsConfig;
      const minDistance = physicsA.radius + physicsB.radius;
      const offset = new Vector3().subVectors(a.object.position, b.object.position);
      const distance = offset.length();

      if (distance === 0 || distance > minDistance) {
        continue;
      }

      const normal = offset.normalize();
      const relativeVelocity = new Vector3().subVectors(a.velocity, b.velocity);
      const separatingSpeed = relativeVelocity.dot(normal);

      if (separatingSpeed > 0) {
        continue;
      }

      const impulse = normal.clone().multiplyScalar(-separatingSpeed);
      a.velocity.addScaledVector(impulse, 0.9);
      b.velocity.addScaledVector(impulse, -0.9);

      const correction = normal.multiplyScalar((minDistance - distance) * 0.52);
      a.object.position.add(correction);
      b.object.position.sub(correction);
    }
  }
}

export class PhysicsSystem {
  private readonly entities: RuntimeEntity[];

  constructor(entities: RuntimeEntity[]) {
    this.entities = entities.map((entity) => ({
      ...entity,
      velocity: entity.velocity.clone(),
      seed: hashSeed(entity.config.id)
    }));
  }

  update(deltaSeconds: number, elapsedSeconds: number) {
    const collisionEntities = this.entities.filter(
      (entity) => entity.config.physics.type === "collision"
    );

    for (const entity of this.entities) {
      if (entity.config.physics.type === "drift") {
        applyDrift(entity, deltaSeconds, elapsedSeconds);
      }
    }

    if (collisionEntities.length > 0) {
      applyCollisions(collisionEntities, deltaSeconds);
    }
  }

  static createVelocity(config: SceneEntityConfig) {
    return new Vector3(...config.physics.velocity);
  }

  static createRotation(config: SceneEntityConfig) {
    const rotation = config.rotation ?? [0, 0, 0];
    return new Euler(...rotation);
  }
}
