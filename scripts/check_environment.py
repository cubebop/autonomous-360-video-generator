"""Report whether local prerequisites for the video pipeline are available."""

from __future__ import annotations

import importlib.util
import shutil
import sys


COMMANDS = ("node", "npm", "ffmpeg", "fluidsynth")
PYTHON_MODULES = ("chromadb", "midiutil", "mingus", "jupyterlab", "ipykernel")


def main() -> int:
    print(f"Python: {sys.version.split()[0]}")
    missing = False

    print("\nCommands:")
    for command in COMMANDS:
        path = shutil.which(command)
        print(f"  {'OK' if path else 'MISSING':7s} {command:12s} {path or ''}")
        missing = missing or path is None

    print("\nPython modules:")
    for module in PYTHON_MODULES:
        found = importlib.util.find_spec(module) is not None
        print(f"  {'OK' if found else 'MISSING':7s} {module}")
        missing = missing or not found

    if missing:
        print("\nOne or more prerequisites are missing. See README.md for installation steps.")
        return 1

    print("\nLocal command and Python-package checks passed.")
    print("Ollama and Blender MCP connectivity are tested from the notebook.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
