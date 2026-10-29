import {
  AmbientLight,
  Clock,
  Color,
  DirectionalLight,
  PerspectiveCamera,
  Scene,
  Vector3,
  WebGLRenderer
} from "three";
import { createAssetObject } from "./AssetFactory";
import { addEnvironment } from "./EnvironmentFactory";
import { PhysicsSystem, type RuntimeEntity } from "./PhysicsSystem";
import type { SceneManifest } from "./types";

export class SceneRuntime {
  private readonly host: HTMLDivElement;
  private readonly manifest: SceneManifest;
  private readonly renderer: WebGLRenderer;
  private readonly scene: Scene;
  private readonly camera: PerspectiveCamera;
  private readonly clock = new Clock();
  private physicsSystem: PhysicsSystem | null = null;
  private animationFrameId = 0;
  private readonly hud: HTMLDivElement;
  private elapsedSeconds = 0;
  private isInitialized = false;
  private isPlaying = false;

  constructor(host: HTMLDivElement, manifest: SceneManifest) {
    this.host = host;
    this.manifest = manifest;
    this.scene = new Scene();
    this.scene.background = new Color(manifest.render.background);

    this.camera = new PerspectiveCamera(
      manifest.camera.fov ?? 50,
      host.clientWidth / host.clientHeight,
      0.1,
      200
    );

    this.renderer = new WebGLRenderer({ antialias: true, alpha: false });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setSize(host.clientWidth, host.clientHeight);

    this.hud = document.createElement("div");
    this.hud.className = "hud";
    this.hud.textContent = `${manifest.title} | ${manifest.entities.length} entities | ${manifest.export.fps} fps target`;
  }

  async initialize() {
    if (this.isInitialized) {
      return;
    }

    this.host.append(this.renderer.domElement, this.hud);
    this.setupCamera();
    this.setupLights();
    await addEnvironment(this.scene, this.manifest.environment);
    await this.loadEntities();
    this.registerEvents();
    this.render();
    this.isInitialized = true;
  }

  async start() {
    await this.initialize();
    this.play();
  }

  play() {
    if (this.isPlaying) {
      return;
    }

    this.clock.start();
    this.isPlaying = true;
    this.animate();
  }

  private setupCamera() {
    this.camera.position.set(...this.manifest.camera.position);
    this.camera.lookAt(new Vector3(...this.manifest.camera.lookAt));
  }

  private setupLights() {
    this.scene.add(new AmbientLight(0xffffff, this.manifest.render.ambientLight ?? 1.4));

    const directionalLight = new DirectionalLight(
      0xffffff,
      this.manifest.render.directionalLight ?? 1.2
    );
    directionalLight.position.set(8, 12, 6);
    this.scene.add(directionalLight);
  }

  private async loadEntities() {
    const runtimeEntities: RuntimeEntity[] = [];

    for (const entityConfig of this.manifest.entities) {
      const object = await createAssetObject(entityConfig.asset);
      object.position.set(...entityConfig.position);
      object.rotation.copy(PhysicsSystem.createRotation(entityConfig));
      this.scene.add(object);

      runtimeEntities.push({
        config: entityConfig,
        object,
        velocity: PhysicsSystem.createVelocity(entityConfig),
        seed: 0
      });
    }

    this.physicsSystem = new PhysicsSystem(runtimeEntities);
  }

  private registerEvents() {
    window.addEventListener("resize", this.handleResize);
  }

  private readonly handleResize = () => {
    this.camera.aspect = this.host.clientWidth / this.host.clientHeight;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(this.host.clientWidth, this.host.clientHeight);
    this.render();
  };

  private animate = () => {
    if (!this.isPlaying) {
      return;
    }

    this.animationFrameId = window.requestAnimationFrame(this.animate);
    const deltaSeconds = this.clock.getDelta();
    this.step(deltaSeconds);
  };

  step(deltaSeconds: number) {
    this.elapsedSeconds += deltaSeconds;
    this.physicsSystem?.update(deltaSeconds, this.elapsedSeconds);
    this.render();
  }

  render() {
    this.renderer.render(this.scene, this.camera);
  }

  destroy() {
    this.isPlaying = false;
    window.cancelAnimationFrame(this.animationFrameId);
    window.removeEventListener("resize", this.handleResize);
    this.renderer.dispose();
  }
}
