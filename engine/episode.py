"""Separate Full Episode worker. JSON job files are the panel/engine boundary."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from episode_timeline import (fingerprint, map_source, schedule, ticks, union,
                              validate_plan, validate_snapshot, source_maps, span)
from episode_shots import inspect_asset, media_key, scenes, propose_background, probe


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def speech(kind, media, cache):
    """Worker process uses the installation's existing speech runtime and isolated cache."""
    import transcribe
    transcribe.CACHE_DIR = Path(cache)
    transcribe.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if kind == "words":
        transcribe.transcribe(Path(media))
    else:
        import diarize
        diarize.CACHE_DIR = Path(cache)
        diarize.HOST_VOICE = Path(cache) / "unused_host_voice.npy"
        words_path = transcribe.cache_path_for(Path(media))
        words = read(words_path)["words"] if words_path.exists() else None
        while True:
            result = diarize.diarize(Path(media), words=words, budget_s=60)
            if not result.get("incomplete"):
                break
    sys.stdout.flush()
    os._exit(0)  # Existing CUDA runtime requires bypassing model teardown.


def run_speech(python, kind, media, cache, cancel):
    suffix = "words" if kind == "words" else "speakers"
    dest = Path(cache) / (Path(media).stem+f".{suffix}.json")
    if dest.exists():
        try:
            data = read(dest)
            if isinstance(data.get("words" if kind == "words" else "turns"), list):
                return data
        except (ValueError, AttributeError):
            pass
        dest.unlink()
    child = subprocess.Popen([python, str(Path(__file__).resolve()), "--speech", kind,
                              "--media", media, "--cache", str(cache)])
    try:
        while child.poll() is None:
            if Path(cancel).exists():
                raise InterruptedError("Analysis cancelled; completed cache chunks were retained.")
            time.sleep(.2)
        if child.returncode or not dest.exists():
            raise RuntimeError(f"{kind} analysis failed. See the worker log.")
    finally:
        if child.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"], capture_output=True)
            else:
                child.terminate()
            child.wait(timeout=15)
    return read(dest)


def analyze(job, root):
    snapshot, config = job["snapshot"], job["config"]
    validate_snapshot(snapshot)
    cache = Path(job["cache"])
    cancel = root / "cancel"
    paths = {c["path"] for track in ("v1", "v2") for c in snapshot[track]}
    result = {"mediaKeys": {p: media_key(p) for p in sorted(paths)}, "assets": {},
              "shots": [], "words": [], "turns": [], "speakers": []}
    metadata = {p: probe(p) for p in paths}
    for track in ("v1", "v2"):
        for clip in snapshot[track]:
            if int(clip["sourceIn"]) < 0 or int(clip["sourceOut"]) > int(metadata[clip["path"]]["duration"])+int(snapshot["timebase"]):
                raise ValueError(f"{track.upper()}: timeline references frames beyond the source media.")
    on = lambda k: config.get(k, False) and not config.get("overlaysOff", False)
    for kind in ("host", "sponsor", "subscribe"):
        if cancel.exists():
            raise InterruptedError("Analysis cancelled.")
        if on(kind) and (kind != "sponsor" or config["show"] == "CPI"):
            print(f"[assets] Inspecting {kind}", flush=True)
            result["assets"][kind] = inspect_asset(config["assets"][kind], root / (kind+".png"))
    needs_sponsor = on("sponsor") and config["show"] == "CPI"
    if on("host") or needs_sponsor:
        media = snapshot["mediaPath"]
        source_cache = cache / fingerprint(result["mediaKeys"][media])
        words = run_speech(job["speechPython"], "words", media, source_cache, cancel)
        records = [{**w, "path": media, "start": str(ticks(w["start"])), "end": str(ticks(w["end"]))}
                   for w in words["words"]]
        result["words"] = map_source(records, snapshot["v1"])
        if on("host"):
            speakers = run_speech(job["speechPython"], "speakers", media, source_cache, cancel)
            result["speakers"] = speakers["speakers"]
            result["turns"] = map_source([{**t, "path": media, "start": str(ticks(t["start"])),
                                           "end": str(ticks(t["end"]))} for t in speakers["turns"]], snapshot["v1"])
        ranges = union([(int(c["sourceIn"]), int(c["sourceOut"])) for c in snapshot["v1"]])
        source_shots = []
        for a, b in ranges:
            source_shots.extend(scenes(media, a, b, cache / "shots", cancel))
        result["sourceShots"] = source_shots
        result["shots"] = map_source(source_shots, snapshot["v1"])
    if cancel.exists():
        raise InterruptedError("Analysis cancelled.")
    write(root / "analysis.json", result)
    return {"ok": "Analysis ready. Label host shots and confirm the speaker, then create placements."}


def verify_media(analysis):
    for path, key in analysis["mediaKeys"].items():
        if media_key(path) != key:
            raise ValueError("Source media changed. Prepare again: " + path)
    for asset in analysis["assets"].values():
        if media_key(asset["path"]) != asset["key"]:
            raise ValueError("An animation changed. Prepare again: " + asset["path"])


def execute(action, job_path):
    root = Path(job_path).resolve().parent
    job = read(job_path)
    if action == "analyze":
        return analyze(job, root)
    analysis = read(root / "analysis.json")
    verify_media(analysis)
    if action == "propose":
        labelled = {s["id"]: s.get("host") for s in analysis["shots"]}
        source_shots = propose_background(analysis["sourceShots"], job.get("references", []), root / "cancel")
        analysis["shots"] = map_source(source_shots, job["snapshot"]["v1"])
        for s in analysis["shots"]:
            s["host"] = labelled.get(s["id"])
        write(root / "analysis.json", analysis)
        return {"ok": "Background proposals ready. Confirm each suggested host view."}
    if action == "plan":
        plan = schedule(job["snapshot"], job["config"], analysis)
        plan["mediaKeys"] = analysis["mediaKeys"]
        plan["assets"] = analysis["assets"]
        write(root / "plan.json", plan)
        return {"ok": f"{len(plan['events'])} placements ready for review."}
    if action == "validate":
        plan = read(root / "plan.json")
        if plan["config"] != job["config"] or plan["snapshot"] != job["snapshot"]:
            raise ValueError("Settings or snapshot changed. Recreate placements.")
        events = validate_plan(plan)
        for e in events:
            e["sources"] = source_maps(span(e), plan["snapshot"]["v2" if e["kind"] == "general" else "v1"])
        # Build payload includes only enabled, validated events; retain review state on disk.
        payload = {**plan, "events": events, "validated": True}
        write(root / "build.json", payload)
        return {"ok": "Reviewed placements and media fingerprints validated."}
    raise ValueError("Unknown action")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("action", nargs="?", choices=["analyze", "propose", "plan", "validate"])
    parser.add_argument("job", nargs="?")
    parser.add_argument("--speech", choices=["words", "speakers"])
    parser.add_argument("--media")
    parser.add_argument("--cache")
    args = parser.parse_args()
    if args.speech:
        speech(args.speech, args.media, args.cache)
    try:
        result = execute(args.action, args.job)
        write(Path(args.job).parent / "worker-result.json", result)
        print(result["ok"], flush=True)
    except Exception as exc:
        result = {"error": str(exc), "cancelled": isinstance(exc, InterruptedError)}
        if args.job:
            write(Path(args.job).parent / "worker-result.json", result)
        print(str(exc), file=sys.stderr, flush=True)
        sys.exit(2)
