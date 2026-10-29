import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import puppeteer from "puppeteer";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const rootDir = path.resolve(__dirname, "..");
const outputDir = path.join(rootDir, "output");
const framesDir = path.join(outputDir, "frames");
const localBrowserLibDir = path.join(rootDir, "vendor", "browser-libs", "usr", "lib", "x86_64-linux-gnu");

function parseArgs(argv) {
  const args = {
    manifest: "/manifests/balloon-drift.json",
    width: 1280,
    height: 720
  };

  for (let index = 0; index < argv.length; index += 1) {
    const part = argv[index];

    if (part === "--manifest") {
      args.manifest = argv[index + 1];
      index += 1;
    } else if (part === "--width") {
      args.width = Number(argv[index + 1]);
      index += 1;
    } else if (part === "--height") {
      args.height = Number(argv[index + 1]);
      index += 1;
    }
  }

  return args;
}

function waitForServer(url, timeoutMs = 30_000) {
  const start = Date.now();

  return new Promise((resolve, reject) => {
    const probe = async () => {
      try {
        const response = await fetch(url);
        if (response.ok) {
          resolve();
          return;
        }
      } catch {
        // Server is still booting.
      }

      if (Date.now() - start > timeoutMs) {
        reject(new Error(`Timed out waiting for preview server at ${url}`));
        return;
      }

      setTimeout(probe, 500);
    };

    probe();
  });
}

async function clearFramesDirectory() {
  await fs.rm(framesDir, { recursive: true, force: true });
  await fs.mkdir(framesDir, { recursive: true });
}

async function readManifest(manifestPath) {
  const normalizedPath = manifestPath.startsWith("/")
    ? manifestPath.slice(1)
    : manifestPath;
  const filePath = path.join(rootDir, "public", normalizedPath);
  const manifestContents = await fs.readFile(filePath, "utf8");

  return JSON.parse(manifestContents);
}

function publicPathToFilePath(publicPath) {
  const normalizedPath = publicPath.startsWith("/")
    ? publicPath.slice(1)
    : publicPath;

  return path.join(rootDir, "public", normalizedPath);
}

function runCommand(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: rootDir,
      stdio: ["ignore", "pipe", "pipe"],
      ...options
    });

    let stderr = "";

    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });

    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) {
        resolve(child);
        return;
      }

      reject(new Error(`${command} exited with code ${code}\n${stderr}`));
    });
  });
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const browserEnv = { ...process.env };
  const manifestConfig = await readManifest(args.manifest);
  const viewportWidth = manifestConfig.export.width ?? args.width;
  const viewportHeight = manifestConfig.export.height ?? args.height;

  try {
    await fs.access(localBrowserLibDir);
    browserEnv.LD_LIBRARY_PATH = browserEnv.LD_LIBRARY_PATH
      ? `${localBrowserLibDir}:${browserEnv.LD_LIBRARY_PATH}`
      : localBrowserLibDir;
  } catch {
    // No local browser libs were provisioned.
  }

  await fs.mkdir(outputDir, { recursive: true });
  await clearFramesDirectory();

  await runCommand("npm", ["run", "build"]);

  const preview = spawn("npm", ["run", "preview", "--", "--host", "127.0.0.1", "--port", "4173"], {
    cwd: rootDir,
    stdio: ["ignore", "pipe", "pipe"]
  });

  try {
    await waitForServer("http://127.0.0.1:4173/");

    const browser = await puppeteer.launch({
      headless: true,
      defaultViewport: {
        width: viewportWidth,
        height: viewportHeight
      },
      env: browserEnv
    });

    try {
      const page = await browser.newPage();
      const url = `http://127.0.0.1:4173/?manifest=${encodeURIComponent(args.manifest)}&export=1`;
      await page.goto(url, { waitUntil: "networkidle0" });
      await page.waitForFunction(() => Boolean(window.__relaxingRetroExport?.ready));

      const manifest = await page.evaluate(() => window.__relaxingRetroExport?.manifest);

      if (!manifest) {
        throw new Error("Export manifest was not exposed by the runtime");
      }

      const totalFrames = manifest.export.fps * manifest.export.durationSeconds;
      const deltaSeconds = 1 / manifest.export.fps;

      for (let frame = 0; frame < totalFrames; frame += 1) {
        await page.evaluate((delta) => {
          window.__relaxingRetroExport?.renderFrame(delta);
        }, deltaSeconds);

        const framePath = path.join(framesDir, `frame-${String(frame).padStart(5, "0")}.png`);
        await page.screenshot({ path: framePath });
      }

      const outputPath = path.join(outputDir, manifest.export.outputName);
      const frameInputPath = path.join(framesDir, "frame-%05d.png");
      const ffmpegArgs = [
        "-y",
        "-framerate",
        String(manifest.export.fps),
        "-i",
        frameInputPath
      ];

      if (manifest.export.audioPath) {
        ffmpegArgs.push("-i", publicPathToFilePath(manifest.export.audioPath));

        if (typeof manifest.export.audioVolume === "number") {
          ffmpegArgs.push("-filter:a", `volume=${manifest.export.audioVolume}`);
        }
      }

      ffmpegArgs.push(
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        ...(manifest.export.audioPath ? ["-c:a", "aac", "-shortest"] : []),
        outputPath
      );

      await runCommand("ffmpeg", ffmpegArgs);

      console.log(`Exported ${outputPath}`);
    } finally {
      await browser.close();
    }
  } finally {
    preview.kill("SIGTERM");
  }
}

main()
  .then(() => process.exit(0))
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
