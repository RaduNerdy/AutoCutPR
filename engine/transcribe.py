"""Transcribe an episode audio file to word-level timestamps JSON.

Usage: python transcribe.py "path/to/audio.wav"
Output: cache/<audio stem>.words.json (project cache dir).
Skips work if the cache file already exists (delete it to force a re-run).

Tries CUDA first (using pip-installed NVIDIA cuBLAS/cuDNN wheels), falls back
to CPU int8 automatically — e.g. when Premiere/AE are holding the 4GB of VRAM.
"""
import glob
import json
import os
import site
import sys
import time
from pathlib import Path

# Windows consoles default to cp1252, which can't print Romanian diacritics
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
MODEL = "large-v3-turbo"


def cache_path_for(audio_path: Path) -> Path:
    return CACHE_DIR / f"{audio_path.stem}.words.json"


def add_nvidia_dll_dirs():
    """Public alias: diarization needs the same CUDA libraries."""
    _add_nvidia_dll_dirs()


def _add_nvidia_dll_dirs():
    """CTranslate2 needs cuBLAS/cuDNN DLLs; expose the pip-installed NVIDIA wheels."""
    for sp in site.getsitepackages() + [site.getusersitepackages()]:
        for d in glob.glob(os.path.join(sp, "nvidia", "*", "bin")) + glob.glob(
            os.path.join(sp, "nvidia", "*", "lib")
        ):
            os.add_dll_directory(d)
            os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]


def _run(model, audio_path: Path):
    segments, info = model.transcribe(
        str(audio_path),
        language="ro",
        word_timestamps=True,
        vad_filter=True,
        beam_size=5,
    )
    words = []
    seg_list = []
    for seg in segments:
        seg_list.append({"start": seg.start, "end": seg.end, "text": seg.text})
        for w in seg.words or []:
            words.append({"start": w.start, "end": w.end, "word": w.word})
        # progress heartbeat for background/panel runs
        print(f"[{seg.end:8.1f}s] {seg.text.strip()[:80]}", flush=True)
    return seg_list, words, info


def transcribe(audio_path: Path) -> Path:
    out_path = cache_path_for(audio_path)
    if out_path.exists():
        print(f"[cache] {out_path} already exists, skipping")
        return out_path

    from faster_whisper import WhisperModel

    CACHE_DIR.mkdir(exist_ok=True)
    _add_nvidia_dll_dirs()
    t0 = time.time()
    try:
        print("[model] trying CUDA (int8_float16)", flush=True)
        model = WhisperModel(MODEL, device="cuda", compute_type="int8_float16")
        seg_list, words, info = _run(model, audio_path)
        device = "cuda"
    except Exception as e:
        print(f"[model] CUDA failed ({type(e).__name__}: {e}), falling back to CPU", flush=True)
        model = WhisperModel(MODEL, device="cpu", compute_type="int8")
        seg_list, words, info = _run(model, audio_path)
        device = "cpu"

    payload = json.dumps(
        {
            "audio": str(audio_path),
            "duration": info.duration,
            "language": info.language,
            "device": device,
            "segments": seg_list,
            "words": words,
        },
        ensure_ascii=False,
    )
    # force the transcript to disk before the model is released
    with out_path.open("w", encoding="utf-8") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    print(f"[done] {len(words)} words on {device} in {time.time() - t0:.0f}s -> {out_path}")
    return out_path


if __name__ == "__main__":
    transcribe(Path(sys.argv[1]))
    sys.stdout.flush()
    # Skip interpreter shutdown: releasing a CUDA model can crash the process
    # (0xC0000409) after the transcript is already safely written.
    os._exit(0)
