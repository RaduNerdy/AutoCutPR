"""Pick a song per reel and render a ready-to-drop music bed.

Selection asks the shared model client which song suits each reel, given
the reel's transcript and the song filenames — the names carry the mood
("mfc inspirational" = motivational, "gilgo" = deep/mysterious).

Levels are done here rather than in Premiere: ExtendScript's audio-gain API is
unreliable, but ffmpeg is exact. Each bed is peak-normalised to TARGET_PEAK_DB,
cut to the reel's length, and faded, so Premiere only has to place the file.
"""
import re
import subprocess
from pathlib import Path
import llm

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".wma"}
TARGET_PEAK_DB = -16.0
FADE_IN_S = 1.0
FADE_OUT_S = 1.5
MAX_REEL_CHARS = 450
# The cloud models are reasoning models and think proportionally to how many
# reels they are shown — ten at once buries the answer under thousands of
# thinking tokens and comes back empty. Small batches keep each call bounded.
REELS_PER_CALL = 4
MAX_TOKENS = 8000

MAXVOL_RE = re.compile(r"max_volume:\s*(-?[\d.]+) dB")


def load_env(env_path: Path) -> dict:
    """Minimal .env reader — avoids a python-dotenv dependency."""
    values = {}
    if not env_path.exists():
        return values
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def scan_songs(folder: Path) -> list[Path]:
    if not folder or not folder.exists():
        return []
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in AUDIO_EXTS)


def reel_text(words: list[dict], in_s: float, out_s: float) -> str:
    text = "".join(
        w["word"] for w in words if in_s <= w["start"] and w["end"] <= out_s
    ).strip()
    return text[:MAX_REEL_CHARS]


def choose_songs(reels: list[dict], songs: list[Path], env: dict) -> dict[int, dict]:
    """Ask the model to match a song to each reel. One call for the whole batch,
    so it can vary its choices instead of repeating a favourite."""
    if not songs:
        return {r["reel"]: {"song": None, "reason": "no songs found"} for r in reels}
    try:
        client = llm.ModelClient(env)
        client.preflight()
    except llm.ModelError as e:
        return {r["reel"]: {"song": None, "reason": str(e)}
                for r in reels}

    catalogue = "\n".join(f"- {p.name}" for p in songs)
    by_name = {p.name.lower(): p for p in songs}
    by_stem = {p.stem.lower(): p for p in songs}
    picks = {}

    usable = [r for r in reels if r.get("text")]
    for i in range(0, len(usable), REELS_PER_CALL):
        batch = usable[i:i + REELS_PER_CALL]
        reel_block = "\n\n".join(f"REEL {r['reel']}:\n{r['text']}" for r in batch)
        prompt = (
            "You are scoring instrumental music beds for short social clips from a "
            "Romanian podcast. The song filenames describe their mood.\n\n"
            f"SONGS:\n{catalogue}\n\n"
            f"CLIPS:\n{reel_block}\n\n"
            "For each reel pick the single best-fitting song. Vary your picks unless a "
            "repeat is clearly right. Think briefly. Reply with JSON only, no prose:\n"
            '{"picks":[{"reel":1,"song":"exact filename","reason":"under 12 words"}]}'
        )
        try:
            data = client.generate(prompt, "picks", temperature=0.3, max_tokens=MAX_TOKENS)
            if any(p["reel"] not in {r["reel"] for r in batch} for p in data["picks"]):
                raise llm.ModelError("Music model referenced a reel outside this batch.")
        except llm.ModelError as e:
            for r in batch:
                picks[r["reel"]] = {"song": None,
                                    "reason": str(e)}
            continue

        for item in data.get("picks", []):
            name = str(item.get("song", "")).strip().lower()
            match = by_name.get(name) or by_stem.get(Path(name).stem)
            if match:
                picks[int(item["reel"])] = {
                    "song": str(match),
                    "reason": str(item.get("reason", ""))[:120],
                }

    for r in reels:
        picks.setdefault(r["reel"], {"song": None, "reason": "model gave no usable pick"})
    return picks


def peak_dbfs(path: Path) -> float | None:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
         "-vn", "-sn", "-dn", "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    m = MAXVOL_RE.search(proc.stderr or "")
    return float(m.group(1)) if m else None


# Rate/channel conversion must live INSIDE the filtergraph. As an output option
# (-ar/-ac) it is applied after the graph, and ffmpeg's mono->stereo upmix drops
# 3dB — so volumedetect would measure one signal while a quieter one gets
# written. Both measurement and render share this prefix so the gain maths holds.
AFORMAT = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"


def _processed_peak(song: Path, duration: float) -> float | None:
    """Peak after looping, trimming and channel/rate conversion."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats",
         "-stream_loop", "-1", "-i", str(song),
         "-t", f"{duration:.3f}", "-vn", "-sn", "-dn",
         "-af", f"{AFORMAT},volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    m = MAXVOL_RE.search(proc.stderr or "")
    return float(m.group(1)) if m else None


def render_bed(
    song: Path,
    out_path: Path,
    duration: float,
    fade_in: float = FADE_IN_S,
    fade_out: float = FADE_OUT_S,
    target_peak: float = TARGET_PEAK_DB,
) -> dict:
    """Peak-normalised, length-matched, faded bed. Cached by output path."""
    if out_path.exists():
        return {"path": str(out_path), "cached": True}

    peak = _processed_peak(song, duration)
    gain = 0.0 if peak is None else target_peak - peak

    filters = [AFORMAT, f"volume={gain:.2f}dB"]
    if fade_in > 0:
        filters.append(f"afade=t=in:st=0:d={fade_in:.2f}")
    if fade_out > 0:
        start = max(0.0, duration - fade_out)
        filters.append(f"afade=t=out:st={start:.2f}:d={fade_out:.2f}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-nostats",
         "-stream_loop", "-1", "-i", str(song),   # loop if the song is short
         "-t", f"{duration:.3f}", "-vn", "-sn", "-dn",
         "-af", ",".join(filters), str(out_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0 or not out_path.exists():
        return {"path": None, "error": (proc.stderr or "")[-400:]}
    return {
        "path": str(out_path),
        "cached": False,
        "source_peak_db": peak,
        "gain_db": round(gain, 2),
    }
