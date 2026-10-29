# Autonomous 360 Video Generator

An educational reference implementation of an autonomous pipeline that plans,
builds, and renders low-poly 360-degree videos. The final notebook coordinates
local language models, Blender, procedural music, scene memory, a Three.js
runtime, and an FFmpeg-based MP4 export.

The project is designed for a WSL/Linux Python environment with Blender running
either locally or on Windows through a compatible Blender MCP socket add-on.

## Pipeline

1. Test the Blender MCP connection.
2. Query ChromaDB for previous scenes.
3. Ask Ollama to plan a novel scene.
4. Generate Blender Python and export GLB assets.
5. Generate procedural MIDI/WAV music.
6. Assemble a scene manifest.
7. Render Three.js frames through Puppeteer.
8. Encode the frames and audio as an MP4 with FFmpeg.
9. Generate optional social-post copy and store it in ChromaDB.

The orchestration entry point is
[`notebooks/video-generator-pipeline.ipynb`](notebooks/video-generator-pipeline.ipynb).

## Repository layout

```text
autonomous-360-video-generator/
├── notebooks/                 Final orchestration notebook
├── scripts/                   Python pipeline and Node video exporter
├── src/                       Three.js runtime and physics engine
├── public/                    Manifests and generated runtime assets
├── package.json               Node/Vite/Puppeteer dependencies
├── requirements.txt           Notebook and Python dependencies
└── .env.example               Configuration reference
```

Generated GLBs, audio, frames, videos, posts, and ChromaDB data are intentionally
ignored by Git.

## Prerequisites

- Python 3.11 or 3.12
- Node.js 18 or newer and npm
- FFmpeg
- FluidSynth and a General MIDI soundfont
- Ollama with `llama3.2:latest` and `qwen2.5-coder:7b`
- Blender 3.x or 4.x with an MCP socket add-on capable of executing Blender
  Python through the `execute_code` message format

On Ubuntu/WSL, install the system media tools with:

```bash
sudo apt update
sudo apt install ffmpeg fluidsynth fluid-soundfont-gm
```

Pull the local models:

```bash
ollama pull llama3.2:latest
ollama pull qwen2.5-coder:7b
```

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
npm install
```

Check the local software and Python packages:

```bash
python scripts/check_environment.py
npm run build
```

## Configuration

The notebook reads its connection settings from environment variables. Start
with `.env.example`, but export the variables in the shell that launches
Jupyter; the notebook does not load `.env` automatically.

For services on the same Linux machine:

```bash
export OLLAMA_BASE_URL=http://127.0.0.1:11434
export A360VG_MCP_HOST=127.0.0.1
export A360VG_MCP_PORT=9876
```

When Ollama and Blender run on Windows while Jupyter runs in WSL, the pipeline
automatically reads the Windows host address from WSL's default route. The
detected address can be inspected with:

```bash
ip route | awk '/default/ {print $3}'
```

Normally no override is required. If auto-detection does not match your setup,
use that value in `OLLAMA_BASE_URL` and `A360VG_MCP_HOST`. Ensure Ollama and the
Blender MCP add-on listen on an interface reachable from WSL.

## Test the renderer without AI or Blender

The included smoke-test manifest uses only Three.js primitives:

```bash
npm run export:scene -- --manifest /manifests/renderer-smoke-test.json
```

The resulting video should be written to `output/renderer-smoke-test.mp4`.

## Run the complete notebook

Start Jupyter from the repository root so the notebook can locate `scripts/`:

```bash
source .venv/bin/activate
jupyter lab
```

Open `notebooks/video-generator-pipeline.ipynb` and run the cells in order.
Before a first test, consider changing the configuration cell to a short,
lower-resolution render:

```python
RENDER_DURATION_SECS = 5
RENDER_WIDTH = 1280
RENDER_HEIGHT = 640
```

The production defaults are 120 seconds at 3840x1920 and can require substantial
time and disk space because every frame is captured as a PNG before FFmpeg runs.

## Output

- `public/assets/environments/`: generated environment GLBs
- `public/assets/generated/`: generated entity GLBs
- `public/assets/audio/`: procedural MIDI and WAV files
- `public/manifests/`: generated scene manifests
- `public/posts/`: generated post copy
- `output/frames/`: temporary PNG render frames
- `output/*.mp4`: final videos
- `.video-generator-memory/`: local scene and post memory

## License

The source code is available under the Apache License 2.0. See `LICENSE` and
`NOTICE`. Third-party tools, models, add-ons, and dependencies are governed by
their own licenses.
