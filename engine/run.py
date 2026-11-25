"""Panel entry point: transcribe (cached, isolated) + match, write cuts JSON.

Usage:
    python run.py --audio "episode.wav" --reels-file "reels.txt" --out "cuts.json"

Transcription runs in a SEPARATE process on purpose: releasing a CUDA whisper
model can crash the host process during teardown (0xC0000409) even after the
transcript is safely written. Isolating it means such a crash cannot take the
matching stage with it — if the words JSON is valid, we carry on.

Prints progress while transcribing; on success the last line is
CUTS_JSON=<path> so the caller knows where the result landed.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import assets
import match
import music
import llm
import silence
import transcribe

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ENGINE_DIR = Path(__file__).resolve().parent
LOG_PATH = ENGINE_DIR.parent / "cache" / "last_run.log"


def log(msg: str):
    print(msg, flush=True)
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except OSError:
        pass


def load_transcript(path: Path) -> dict | None:
    """Return the transcript if it exists and is complete, else None."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log(f"[warn] transcript unreadable ({e}); will re-transcribe")
        return None
    if not data.get("words"):
        log("[warn] transcript has no words; will re-transcribe")
        return None
    return data


def run_transcription(audio: Path) -> dict:
    words_path = transcribe.cache_path_for(audio)
    cached = load_transcript(words_path)
    if cached:
        log(f"[cache] using existing transcript: {words_path}")
        return cached

    log(f"[transcribe] starting subprocess for {audio.name}")
    proc = subprocess.run(
        [sys.executable, str(ENGINE_DIR / "transcribe.py"), str(audio)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    for line in (proc.stdout or "").splitlines():
        log(line)
    if proc.stderr:
        log("[stderr] " + proc.stderr.strip()[:2000])

    data = load_transcript(words_path)
    if data is None:
        raise RuntimeError(
            f"Transcription failed (exit {proc.returncode}) and no usable transcript was written"
        )
    if proc.returncode != 0:
        # e.g. CUDA teardown crash after the JSON was already flushed to disk
        log(f"[warn] transcriber exited {proc.returncode} but the transcript is complete — continuing")
    return data


def waveform(audio: Path, args) -> list[dict] | None:
    """Cached silence scan; needed by snapping even when trimming is off."""
    if args.silence_mode not in ("hybrid", "db"):
        return None
    if args.silence == "off" and args.snap_back <= 0:
        return None
    log(f"[silence] scanning waveform at {args.noise_db}dB")
    sil = silence.detect_silence(audio, LOG_PATH.parent, args.noise_db)
    log(f"[silence] {len(sil)} silent stretches found")
    return sil


def prepare_episode(audio: Path, args):
    """Transcript, waveform and speakers. Diarization is resumable, so this
    reports what is left rather than blocking until it finishes."""
    state = {"transcript": False, "waveform": False, "speakers": None,
             "speakers_pending": 0}
    try:
        transcript = run_transcription(audio)
        state["transcript"] = True
        state["words"] = len(transcript["words"])
    except RuntimeError as e:
        log(f"[error] {e}")
        write_json(Path(args.out), state)
        sys.exit(3)

    sil = waveform(audio, args)
    state["waveform"] = sil is not None
    state["silences"] = len(sil or [])

    import diarize

    try:
        result = diarize.diarize(audio, args.speakers_count, transcript["words"])
    except Exception as e:
        # Write the reason out, so the panel reports what actually broke
        # instead of marking every step failed with no explanation.
        state["error"] = f"speaker detection: {type(e).__name__}: {e}"[:300]
        log(f"[error] {state['error']}")
        write_json(Path(args.out), state)
        sys.exit(4)
    if result.get("incomplete"):
        state["speakers_pending"] = result["remaining"]
        log(f"[prepare] {result['remaining']} speaker chunks still pending")
    else:
        state["speakers"] = {k: result[k] for k in
                             ("turns", "totals", "host", "host_detection", "speakers")}
        log(f"[prepare] speakers ready, host is {result['host']}")
    write_json(Path(args.out), state)


def set_host(audio: Path, host: int):
    """Record the editor's correction and remember the voice for next time."""
    import diarize
    import numpy as np

    path = diarize.cache_path_for(audio)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["host"] = host
    data["host_detection"] = {"host": host, "method": "manual", "confidence": None}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    embeds_file = LOG_PATH.parent / f"{audio.stem}.embeds.npy"
    if embeds_file.exists():
        vecs = np.load(embeds_file)
        order = sorted(data["speakers"])
        if host in order and order.index(host) < len(vecs):
            np.save(diarize.HOST_VOICE, vecs[order.index(host)])
            log("[sethost] voiceprint saved, later episodes will match it")
    log(f"[sethost] host is speaker {host}")


def run_discover(audio: Path, transcript: dict, args):
    import diarize
    import discover

    speakers = diarize.diarize(audio, args.speakers_count, transcript["words"])
    if speakers.get("incomplete"):
        log("[error] speaker detection is not finished; run Prepare again")
        sys.exit(4)

    env = music.load_env(Path(__file__).resolve().parent / ".env")
    cuts = discover.discover(
        transcript, speakers, env,
        min_count=args.min_count, max_count=args.max_count,
        min_len=args.min_len, max_len=args.max_len, target_len=args.target_len,
        guest_only=args.guest_only, host_max_s=args.host_max,
        allow_intertwined=args.allow_intertwined,
        silences=waveform(audio, args),
        silence_preset=None if args.silence == "off" else args.silence,
        silence_mode=args.silence_mode,
    )
    if not cuts:
        log("[error] no moments survived; check the log for model or match failures")
        sys.exit(5)

    out = Path(args.out)
    write_json(out, cuts)
    write_json(out.with_suffix(".assets.json"), {})
    match.print_report(transcript, cuts)
    for c in cuts:
        log(f"  REEL {c['reel']}: {c.get('summary', '')}")
    log(f"CUTS_JSON={out}")


def run_trailer(transcript: dict, args):
    import trailer as trailer_mod

    cuts = json.loads(Path(args.cuts).read_text(encoding="utf-8"))
    env = music.load_env(Path(__file__).resolve().parent / ".env")
    result = trailer_mod.suggest(transcript, cuts, env)
    if result.get("error"):
        log(f"[error] {result['error']}")
        sys.exit(6)
    write_json(Path(args.out), result)
    for c in result["candidates"]:
        log(f"  [{c['length']:4.1f}s] {c['summary']}")
    log(f"[trailer] {len(result['orders'])} orderings, {result['note']}")


def run_assets(audio: Path, transcript: dict, args):
    """Logo measurement and music beds for cuts that already exist."""
    out = Path(args.out)
    cuts = json.loads(out.read_text(encoding="utf-8"))
    extras = {}
    if args.logo:
        extras["logo"] = prepare_logo(args)
    if args.songs:
        prepare_music(args, cuts, transcript["words"])
    write_json(out, cuts)
    write_json(out.with_suffix(".assets.json"), extras)
    log(f"CUTS_JSON={out}")


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True)
    ap.add_argument("--reels-file", help="required for --mode analyze")
    ap.add_argument("--out", help="where results are written; not used by sethost")
    ap.add_argument(
        "--silence",
        default="moderate",
        choices=["conservative", "moderate", "aggressive", "off"],
        help="dead-air removal aggressiveness",
    )
    ap.add_argument(
        "--silence-mode",
        default="hybrid",
        choices=["hybrid", "db", "words"],
        help="hybrid: waveform boundaries vetoed by whisper (default); "
             "db: waveform only; words: whisper timings only",
    )
    ap.add_argument("--noise-db", type=float, default=silence.NOISE_DB)
    ap.add_argument(
        "--snap-back",
        type=float,
        default=silence.SNAP_MAX_BACK,
        help="how far a reel edge may move to reach the real start of the "
             "thought; 0 disables snapping",
    )
    ap.add_argument(
        "--no-protect-dramatic",
        action="store_true",
        help="also cut long pauses that follow a finished sentence",
    )
    # Defaults measured off a finished reel the editor signed off on:
    # the badge is centred, sits at 0.735 of frame height, and is 27% of frame
    # width. Sizing by width is resolution-independent, so it is the default
    # and --logo-scale only applies when width is explicitly disabled.
    ap.add_argument("--logo", help="path to the logo (alpha matte) image")
    ap.add_argument("--logo-scale", type=float, default=165.0)
    ap.add_argument("--logo-x", type=float, default=0.49,
                    help="0=left edge, 1=right edge")
    ap.add_argument("--logo-y", type=float, default=0.735,
                    help="0=top, 1=bottom; 0.735 sits just below a subtitle block")
    ap.add_argument("--logo-width-pct", type=float, default=27.0,
                    help="size the mark to this %% of frame width; "
                         "0 falls back to --logo-scale")
    ap.add_argument("--songs", help="folder of instrumental songs")
    ap.add_argument("--fade-in", type=float, default=music.FADE_IN_S)
    ap.add_argument("--fade-out", type=float, default=music.FADE_OUT_S)
    ap.add_argument("--peak-db", type=float, default=music.TARGET_PEAK_DB)
    ap.add_argument("--mode", default="analyze",
                    choices=["analyze", "prepare", "discover", "trailer",
                             "assets", "sethost"])
    ap.add_argument("--speakers-count", type=int, default=2)
    ap.add_argument("--host", type=int)
    ap.add_argument("--cuts", help="existing cuts.json, for trailer mode")
    ap.add_argument("--min-count", type=int, default=8)
    ap.add_argument("--max-count", type=int, default=12)
    ap.add_argument("--min-len", type=int, default=20)
    ap.add_argument("--max-len", type=int, default=90)
    ap.add_argument("--target-len", type=int, default=45)
    ap.add_argument("--host-max", type=float, default=5.0)
    ap.add_argument("--guest-only", action="store_true")
    ap.add_argument("--allow-intertwined", action="store_true")
    args = ap.parse_args()

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text("", encoding="utf-8")

    audio = Path(args.audio)
    if not audio.exists():
        # The transcript and waveform scan are cached per file stem, so a reel
        # list can still be re-run after the footage has been archived — which
        # is exactly when a corrected list tends to arrive.
        if not transcribe.cache_path_for(audio).exists():
            log(f"[error] audio not found and no cached transcript: {audio}")
            sys.exit(2)
        log(f"[cache] media missing — running from the cached transcript")

    if args.mode == "sethost":
        set_host(audio, args.host)
        return
    if args.mode == "prepare":
        prepare_episode(audio, args)
        return

    if args.mode in ("discover", "trailer"):
        llm.ModelClient(music.load_env(ENGINE_DIR / ".env")).preflight()

    try:
        transcript = run_transcription(audio)
    except RuntimeError as e:
        log(f"[error] {e}")
        sys.exit(3)

    if args.mode == "discover":
        run_discover(audio, transcript, args)
        return
    if args.mode == "trailer":
        run_trailer(transcript, args)
        return
    if args.mode == "assets":
        run_assets(audio, transcript, args)
        return

    reel_text = Path(args.reels_file).read_text(encoding="utf-8")
    preset = None if args.silence == "off" else args.silence

    silences = None
    # snapping needs the waveform too, even when silence removal is off
    if args.silence_mode in ("hybrid", "db") and (preset or args.snap_back > 0):
        log(f"[silence] scanning waveform at {args.noise_db}dB")
        silences = silence.detect_silence(audio, LOG_PATH.parent, args.noise_db)
        log(f"[silence] {len(silences)} silent stretches found")

    cuts = match.compute_cuts(
        reel_text, transcript, preset,
        silences=silences,
        silence_mode=args.silence_mode,
        protect_dramatic=not args.no_protect_dramatic,
        snap_back=args.snap_back,
    )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    extras = {}

    if args.logo:
        extras["logo"] = prepare_logo(args)

    if args.songs:
        prepare_music(args, cuts, transcript["words"])

    out.write_text(json.dumps(cuts, ensure_ascii=False, indent=2), encoding="utf-8")
    assets_path = out.with_suffix(".assets.json")
    assets_path.write_text(json.dumps(extras, ensure_ascii=False, indent=2), encoding="utf-8")
    match.print_report(transcript, cuts)
    log(f"CUTS_JSON={out}")


def reel_length(cut: dict) -> float:
    """Final length after dead air is removed — what the music must match."""
    return sum(s["out_s"] - s["in_s"] for s in cut["segments"])


def prepare_logo(args) -> dict:
    logo_path = Path(args.logo)
    if not logo_path.exists():
        log(f"[logo] not found: {logo_path}")
        return {"error": f"not found: {logo_path}"}
    try:
        info = assets.measure_logo(logo_path, LOG_PATH.parent)
        placement = assets.plan_placement(
            info,
            scale_pct=args.logo_scale,
            target_x=args.logo_x,
            target_y=args.logo_y,
            target_width_pct=args.logo_width_pct or None,
        )
    except Exception as e:
        log(f"[logo] measurement failed: {e}")
        return {"error": str(e)}
    log(f"[logo] {placement['summary']}")
    if not placement["fits"]:
        log("[logo] WARNING: at this scale the mark is wider/taller than the frame")
    return {"path": str(logo_path), "measured": info, "placement": placement}


def prepare_music(args, cuts: list[dict], words: list[dict]):
    folder = Path(args.songs)
    songs = music.scan_songs(folder)
    log(f"[music] {len(songs)} songs in {folder}")
    if not songs:
        for c in cuts:
            c["music"] = {"song": None, "reason": "no songs in folder", "bed": None}
        return

    env = music.load_env(Path(__file__).resolve().parent / ".env")
    reels = [
        {"reel": c["reel"], "text": music.reel_text(words, c["in_s"], c["out_s"])}
        for c in cuts
    ]
    picks = music.choose_songs(reels, songs, env)

    bed_dir = LOG_PATH.parent / "music"
    for c in cuts:
        pick = picks.get(c["reel"], {"song": None, "reason": "no pick"})
        c["music"] = {"song": pick.get("song"), "reason": pick.get("reason"), "bed": None}
        if not pick.get("song"):
            log(f"[music] REEL {c['reel']}: {pick.get('reason')}")
            continue
        song = Path(pick["song"])
        length = reel_length(c)
        bed = bed_dir / f"REEL_{c['reel']:02d}__{song.stem}.wav"
        result = music.render_bed(
            song, bed, length,
            fade_in=args.fade_in, fade_out=args.fade_out, target_peak=args.peak_db,
        )
        if result.get("path"):
            c["music"]["bed"] = result["path"]
            log(f"[music] REEL {c['reel']}: {song.name} "
                f"({length:.1f}s, {result.get('gain_db', 0):+.1f}dB) — {pick.get('reason','')}")
        else:
            c["music"]["error"] = result.get("error")
            log(f"[music] REEL {c['reel']}: render failed")


if __name__ == "__main__":
    try:
        main()
    except llm.ModelError as e:
        log(f"[error] {e}")
        sys.exit(7)
