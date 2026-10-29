"""Autonomous 360 Video Generator procedural ambient music generator.

Pure algorithmic, copyright-safe: music theory constructs only, no songs.

Install dependencies:
    pip install -r requirements-music.txt
    sudo apt install fluidsynth fluid-soundfont-gm

Default WSL soundfont path:
    /usr/share/sounds/sf2/FluidR3_GM.sf2
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from midiutil import MIDIFile
from mingus.core.scales import Aeolian, Dorian, Ionian, Lydian, Phrygian

SOUNDFONT = "/usr/share/sounds/sf2/FluidR3_GM.sf2"
SAMPLE_RATE = 44100

# Master volume offset added to every instrument's velocity (MIDI 0-127).
# Positive = louder overall, negative = quieter. Each instrument keeps its
# relative balance from MOOD_MAP — this just shifts the whole mix.
MASTER_VOLUME_OFFSET = 25


def _clamp_velocity(v: int) -> int:
    return max(1, min(127, int(v)))

# General MIDI patch numbers are 0-indexed.
PATCH_PAD = 88
PATCH_STRINGS = 48
PATCH_VIBES = 11
PATCH_LEAD = 80   # Lead 1 Square — retro synth single-note melody

NOTE_MAP = {
    "C": 0,
    "C#": 1,
    "Db": 1,
    "D": 2,
    "D#": 3,
    "Eb": 3,
    "E": 4,
    "Fb": 4,
    "F": 5,
    "E#": 5,
    "F#": 6,
    "Gb": 6,
    "G": 7,
    "G#": 8,
    "Ab": 8,
    "A": 9,
    "A#": 10,
    "Bb": 10,
    "B": 11,
    "Cb": 11,
    "B#": 0,
}


def note_to_midi(note_name: str, octave: int = 4) -> int:
    """Convert a mingus note name plus octave into a MIDI note number."""
    base = NOTE_MAP.get(note_name)

    if base is None:
        raise ValueError(f"Unknown note name: {note_name!r}")

    return (octave + 1) * 12 + base


def scale_midi_notes(scale_obj: Any, octave: int = 4) -> list[int]:
    """Return MIDI note numbers for one octave of a mingus scale object."""
    return [note_to_midi(note_name, octave) for note_name in scale_obj.ascending()[:-1]]


def fade_note(
    midi_file: MIDIFile,
    track: int,
    channel: int,
    note: int,
    start_beat: float,
    duration: float,
    start_volume: int,
    segments: int = 6,
    end_volume_ratio: float = 0.15,
) -> None:
    """Write a single note as `segments` equal slices with linearly decreasing velocity.

    Simulates a fade-out envelope: starts at `start_volume`, ends at
    `start_volume * end_volume_ratio`.  Segments shorter than 0.05 beats are
    skipped to avoid MIDI jitter on very short notes.
    """
    seg_dur = duration / segments
    if seg_dur < 0.05:
        # Note too short to fade meaningfully — write as-is
        midi_file.addNote(track, channel, note, start_beat, duration, start_volume)
        return
    for i in range(segments):
        t = i / (segments - 1) if segments > 1 else 0.0
        vol = int(start_volume * (1.0 - t * (1.0 - end_volume_ratio)))
        vol = max(10, vol)
        midi_file.addNote(track, channel, note, start_beat + i * seg_dur, seg_dur, vol)


def triad_midi(root_note: str, scale_notes_midi: list[int]) -> list[int]:
    """Build a diatonic triad from the scale using root, third, and fifth."""
    extended = scale_notes_midi + [note + 12 for note in scale_notes_midi]

    try:
        index = scale_notes_midi.index(note_to_midi(root_note, 4))
    except ValueError:
        index = 0

    return [extended[index], extended[index + 2], extended[index + 4]]


MOOD_MAP = {
    "dreamy": {
        "scale_class": Lydian,
        "root": "C",
        "octave": 4,
        "tempo": 48,
        "patch_pad": PATCH_PAD,
        "patch_arp": PATCH_VIBES,
        "volume_pad": 72,
        "volume_arp": 58,
        "manifest_volume": 0.75,
        "progression": [0, 3, 4, 2, 0, 5, 3, 0],
    },
    "mysterious": {
        "scale_class": Phrygian,
        "root": "A",
        "octave": 3,
        "tempo": 44,
        "patch_pad": PATCH_STRINGS,
        "patch_arp": PATCH_PAD,
        "volume_pad": 65,
        "volume_arp": 50,
        "manifest_volume": 0.65,
        "progression": [0, 1, 0, 5, 0, 3, 1, 0],
    },
    "dark": {
        "scale_class": Dorian,
        "root": "D",
        "octave": 3,
        "tempo": 46,
        "patch_pad": PATCH_STRINGS,
        "patch_arp": PATCH_VIBES,
        "volume_pad": 68,
        "volume_arp": 52,
        "manifest_volume": 0.68,
        "progression": [0, 2, 3, 0, 5, 3, 2, 0],
    },
    "cold": {
        "scale_class": Aeolian,
        "root": "E",
        "octave": 3,
        "tempo": 42,
        "patch_pad": PATCH_PAD,
        "patch_arp": PATCH_STRINGS,
        "volume_pad": 62,
        "volume_arp": 48,
        "manifest_volume": 0.70,
        "progression": [0, 5, 3, 0, 2, 5, 0, 3],
    },
    "peaceful": {
        "scale_class": Ionian,
        "root": "F",
        "octave": 4,
        "tempo": 50,
        "patch_pad": PATCH_PAD,
        "patch_arp": PATCH_VIBES,
        "volume_pad": 70,
        "volume_arp": 55,
        "manifest_volume": 0.72,
        "progression": [0, 3, 4, 0, 5, 3, 4, 0],
    },
}
MOOD_MAP["default"] = MOOD_MAP["dreamy"]


class MusicAgent:
    """Generates procedural ambient WAV files for Autonomous 360 Video Generator scenes."""

    def __init__(
        self,
        output_dir: str = "public/assets/audio",
        soundfont: str = SOUNDFONT,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.soundfont = soundfont

    def generate(
        self,
        scene_id: str,
        mood: str = "dreamy",
        duration_seconds: int = 1800,
        seed: int | None = None,
    ) -> dict[str, Any]:
        """Generate ambient MIDI and, when FluidSynth is available, a WAV file."""
        rng = random.Random(seed)
        settings = MOOD_MAP.get(mood.lower(), MOOD_MAP["default"])

        midi_path = self.output_dir / f"{scene_id}.mid"
        wav_path = self.output_dir / f"{scene_id}.wav"

        self._build_midi(midi_path, settings, duration_seconds, rng)
        wav_rendered = self._render_wav(midi_path, wav_path)

        return {
            "scene_id": scene_id,
            "mood": mood,
            "midiPath": str(midi_path),
            "audioPath": f"/assets/audio/{scene_id}.wav",
            "audioVolume": settings["manifest_volume"],
            "duration_seconds": duration_seconds,
            "generated_at": datetime.now().isoformat(),
            "wavRendered": wav_rendered,
        }

    def _build_midi(
        self,
        path: Path,
        settings: dict[str, Any],
        duration_seconds: int,
        rng: random.Random,
    ) -> None:
        """Build a two-track MIDI file: sustained pad chords plus sparse arpeggios."""
        tempo = settings["tempo"]
        beats_total = int((duration_seconds / 60.0) * tempo)
        is_preview = duration_seconds <= 30
        # Wide continuous range so some triads are quick stabs, others long holds
        pad_dur_min = 0.75 if is_preview else 3.0
        pad_dur_max = 5.0  if is_preview else 14.0
        silence_choices = [0.25, 0.5, 0.75, 1.0] if is_preview else [2.0, 2.0, 3.0, 4.0, 6.0]
        arpeggio_probability = 0.95 if is_preview else 0.60
        arpeggio_start_offset = 0.25 if is_preview else 1.0
        arpeggio_note_duration = 0.9 if is_preview else 2.0
        midi = MIDIFile(3)

        midi.addTrackName(0, 0, "Pad")
        midi.addTempo(0, 0, tempo)
        midi.addProgramChange(0, 0, 0, settings["patch_pad"])

        midi.addTrackName(1, 0, "Arpeggio")
        midi.addTempo(1, 0, tempo)
        midi.addProgramChange(1, 1, 0, settings["patch_arp"])

        midi.addTrackName(2, 0, "Lead")
        midi.addTempo(2, 0, tempo)
        midi.addProgramChange(2, 2, 0, PATCH_LEAD)

        scale_obj = settings["scale_class"](settings["root"])
        scale_low = scale_midi_notes(scale_obj, settings["octave"])
        scale_notes = scale_obj.ascending()[:-1]
        progression = settings["progression"]

        beat = 0.0
        progression_index = 0

        while beat < beats_total:
            degree = progression[progression_index % len(progression)]
            progression_index += 1

            root_name = scale_notes[degree % len(scale_notes)]
            triad = triad_midi(root_name, scale_low)

            remaining_beats = beats_total - beat
            pad_duration = min(rng.uniform(pad_dur_min, pad_dur_max), remaining_beats)
            volume_pad = _clamp_velocity(settings["volume_pad"] + MASTER_VOLUME_OFFSET + rng.randint(-6, 6))

            for midi_note in triad:
                if pad_duration > 0:
                    midi.addNote(0, 0, midi_note, beat, pad_duration, volume_pad)

            if rng.random() < arpeggio_probability:
                arp_notes_high = [note + 12 for note in triad + [triad[0] + 12]]
                arp_start = beat + arpeggio_start_offset
                arp_gap = rng.uniform(0.25, 0.4) if is_preview else rng.uniform(0.4, 0.7)
                volume_arp = _clamp_velocity(settings["volume_arp"] + MASTER_VOLUME_OFFSET + rng.randint(-5, 5))

                for index, midi_note in enumerate(arp_notes_high):
                    arp_beat = arp_start + index * arp_gap
                    if arp_beat < beats_total:
                        note_duration = min(arpeggio_note_duration, beats_total - arp_beat)
                        midi.addNote(1, 1, midi_note, arp_beat, note_duration, volume_arp)

            # --- Lead: starts in the last quarter of the triad, lasts same duration ---
            lead_start = beat + pad_duration * 0.75
            if lead_start < beats_total:
                lead_scale  = scale_midi_notes(scale_obj, settings["octave"] + 1)
                volume_lead = _clamp_velocity(max(38, settings["volume_pad"] - 18) + MASTER_VOLUME_OFFSET + rng.randint(-5, 5))
                lead_dur    = min(pad_duration, beats_total - lead_start)
                divisions   = rng.choice([1, 2, 3, 4])
                note_dur    = lead_dur / divisions
                for i in range(divisions):
                    note_beat = lead_start + i * note_dur
                    if note_beat < beats_total:
                        lead_note = rng.choice(lead_scale)
                        fade_note(midi, 2, 2, lead_note, note_beat,
                                  min(note_dur, beats_total - note_beat), volume_lead)

            # Advance: triad duration + lead note duration + silence before next triad
            beat += 2 * pad_duration + rng.choice(silence_choices)

        with path.open("wb") as midi_file:
            midi.writeFile(midi_file)

        print(f"MIDI written: {path}")

    def _render_wav(self, midi_path: Path, wav_path: Path) -> bool:
        """Render MIDI to WAV via FluidSynth. Returns False if rendering is unavailable."""
        if not os.path.exists(self.soundfont):
            print(f"Soundfont not found at {self.soundfont}")
            print("Install with: sudo apt install fluid-soundfont-gm")
            print(f"MIDI saved at {midi_path}. Render manually when ready.")
            return False

        command = [
            "fluidsynth",
            "-ni",
            self.soundfont,
            str(midi_path),
            "-F",
            str(wav_path),
            "-r",
            str(SAMPLE_RATE),
        ]

        try:
            result = subprocess.run(command, check=True, capture_output=True, text=True)
            if not wav_path.exists() or wav_path.stat().st_size == 0:
                print(f"FluidSynth ran but WAV missing or empty at {wav_path}")
                return False
            print(f"WAV rendered: {wav_path}")
            return True
        except subprocess.CalledProcessError as error:
            print(f"FluidSynth failed (exit {error.returncode}):\n{error.stderr}")
            return False
        except FileNotFoundError:
            print("fluidsynth not found. Install: sudo apt install fluidsynth")
            return False


def music_node(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph node that generates music and updates the scene manifest."""
    manifest = state.get("manifest", {})
    export = manifest.get("export", {})
    duration_seconds = state.get("duration_seconds", export.get("durationSeconds", 1800))

    agent = MusicAgent()
    result = agent.generate(
        scene_id=state["scene_id"],
        mood=state.get("mood", "dreamy"),
        duration_seconds=duration_seconds,
        seed=state.get("seed"),
    )

    state["audio_path"] = result["audioPath"] if result["wavRendered"] else None
    state["audio_volume"] = result["audioVolume"]

    if result["wavRendered"]:
        export["audioPath"] = result["audioPath"]
        export["audioVolume"] = result["audioVolume"]
        manifest["export"] = export
        state["manifest"] = manifest

    print(f"Music node complete: mood={result['mood']} wav_rendered={result['wavRendered']}")
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description="Autonomous 360 Video Generator Music Agent")
    parser.add_argument("--scene", default="test-scene", help="Scene ID")
    parser.add_argument(
        "--mood",
        default="dreamy",
        help="Mood: dreamy | mysterious | dark | cold | peaceful",
    )
    parser.add_argument("--duration", default=60, type=int, help="Duration in seconds")
    parser.add_argument("--output", default="public/assets/audio", help="Output directory")
    parser.add_argument("--seed", default=42, type=int, help="Random seed")
    args = parser.parse_args()

    print("Autonomous 360 Video Generator Music Agent")
    print(f"Scene:    {args.scene}")
    print(f"Mood:     {args.mood}")
    print(f"Duration: {args.duration}s")
    print(f"Output:   {args.output}\n")

    agent = MusicAgent(output_dir=args.output)
    result = agent.generate(
        scene_id=args.scene,
        mood=args.mood,
        duration_seconds=args.duration,
        seed=args.seed,
    )

    print("\nDone. Manifest audio block:")
    print(
        json.dumps(
            {
                "audioPath": result["audioPath"],
                "audioVolume": result["audioVolume"],
                "wavRendered": result["wavRendered"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
