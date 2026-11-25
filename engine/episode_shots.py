"""Local media inspection, scene cache and non-biometric background proposals.

Video dependencies live in .episode-venv; the working speech environment is untouched.
"""
from fractions import Fraction
import json
import os
from pathlib import Path
import shutil
import subprocess

from episode_timeline import TPS, fingerprint, ticks

ALGORITHM = "adaptive-640-v1"


def media_key(path):
    p = Path(path).resolve(strict=True)
    st = p.stat()
    return {"path": os.path.normcase(str(p)), "size": st.st_size, "mtime": str(st.st_mtime_ns)}


def tool(name):
    result = shutil.which(name)
    if not result:
        raise RuntimeError(f"{name} is missing from PATH. Install FFmpeg and reopen Premiere.")
    return result


def run(args, **kwargs):
    p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=kwargs.pop("timeout", 120), **kwargs)
    if p.returncode:
        raise RuntimeError(p.stderr.decode("utf-8", "replace")[-1600:])
    return p.stdout


def probe(path):
    data = json.loads(run([tool("ffprobe"), "-v", "error", "-show_streams", "-show_format",
                           "-of", "json", str(path)]))
    videos = [s for s in data["streams"] if s["codec_type"] == "video"]
    if not videos:
        raise ValueError(f"No video stream: {path}")
    v = videos[0]
    raw_duration = v.get("duration", data.get("format", {}).get("duration"))
    if not raw_duration:
        raise ValueError(f"No finite video duration: {path}")
    rate = Fraction(v.get("avg_frame_rate", "0/1"))
    if not rate:
        raise ValueError(f"No video frame rate: {path}")
    return {"path": str(Path(path).resolve()), "key": media_key(path),
            "duration": str(ticks(raw_duration)), "width": v["width"], "height": v["height"],
            "fps": str(rate), "codec": v["codec_name"], "pixelFormat": v.get("pix_fmt"),
            "hasAudio": any(s["codec_type"] == "audio" for s in data["streams"])}


def inspect_asset(path, output):
    data = probe(path)
    alphas = []
    for fraction in (.1, .5, .9):
        # Format conversion alone would synthesize opaque alpha. Reject that explicitly.
        raw = run([tool("ffmpeg"), "-v", "error", "-ss", str(int(data["duration"])/TPS*fraction),
                   "-i", str(path), "-frames:v", "1", "-vf", "scale=160:-1,format=rgba",
                   "-f", "rawvideo", "pipe:1"])
        alphas.extend(raw[3::4])
    if not alphas or min(alphas) == 255 or max(alphas) == 0:
        raise ValueError(f"{Path(path).name}: useful transparency was not found. Choose an alpha video; opaque MP4 is not keyed automatically.")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    run([tool("ffmpeg"), "-v", "error", "-y", "-ss", str(int(data["duration"])/TPS*.5),
         "-i", str(path), "-frames:v", "1", "-vf", "scale=480:-1,format=rgba", str(output)])
    data["preview"] = str(output)
    data["alphaChecked"] = True
    return data


def scenes(path, start, end, cache, cancel):
    """Decode every frame of the used source interval, with spatial downscaling only."""
    import cv2
    from scenedetect import AdaptiveDetector
    cache = Path(cache)
    source_key = media_key(path)
    key = fingerprint({"media": source_key, "range": [start, end], "algorithm": ALGORITHM})
    directory = cache / key
    result = directory / "scenes.json"
    if result.exists():
        try:
            data = json.loads(result.read_text(encoding="utf-8"))
            if data["key"] == key and all(Path(s["thumbnail"]).exists() for s in data["shots"]):
                return data["shots"]
        except (ValueError, KeyError, TypeError):
            pass
    # A previously scanned superset can serve new trims without another decode.
    for stored in cache.glob("*/scenes.json"):
        try:
            data = json.loads(stored.read_text(encoding="utf-8"))
            if (data.get("media") == source_key and data.get("algorithm") == ALGORITHM
                    and data["range"][0] <= start and end <= data["range"][1]
                    and all(Path(s["thumbnail"]).exists() for s in data["shots"])):
                return [{**s, "start": str(max(start, int(s["start"]))),
                         "end": str(min(end, int(s["end"])))} for s in data["shots"]
                        if int(s["start"]) < end and start < int(s["end"])]
        except (ValueError, KeyError, TypeError):
            continue
    directory.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError(f"Cannot decode {path}")
    detector = AdaptiveDetector(adaptive_threshold=3.0, min_scene_len=15,
                                window_width=2, min_content_val=15.0)
    # PTS, not ordinal/rounded fps: OpenCV's FFmpeg backend reports decoded timestamps.
    cap.set(cv2.CAP_PROP_POS_MSEC, start/TPS*1000)
    timestamps, cuts, ordinal, last = [], [], 0, -1
    try:
        while True:
            if Path(cancel).exists():
                raise InterruptedError("Analysis cancelled; completed source caches were retained.")
            ok, image = cap.read()
            if not ok:
                break
            timestamp = ticks(str(cap.get(cv2.CAP_PROP_POS_MSEC)/1000))
            if timestamp < start:
                continue
            if timestamp >= end:
                break
            if timestamp <= last:
                raise ValueError("Decoder did not provide monotonic frame timestamps. Convert this source to constant frame rate first.")
            last = timestamp
            timestamps.append(timestamp)
            image = cv2.resize(image, (640, max(1, round(image.shape[0]*640/image.shape[1]))))
            cuts.extend(detector.process_frame(ordinal, image))
            ordinal += 1
            if ordinal % 1500 == 0:
                print(f"[shots] {Path(path).name}: {timestamp/TPS:.0f}s scanned", flush=True)
    finally:
        cap.release()
    if not timestamps:
        raise ValueError("No frames decoded in the selected source range.")
    boundaries = [start]+[timestamps[i] for i in sorted(set(cuts)) if 0 < i < len(timestamps)]+[end]
    shots = []
    for i, (a, b) in enumerate(zip(boundaries, boundaries[1:])):
        thumb = directory / f"{i}.jpg"
        run([tool("ffmpeg"), "-v", "error", "-y", "-ss", str((a+b)/2/TPS), "-i", str(path),
             "-frames:v", "1", "-vf", "scale=480:-1", str(thumb)])
        shots.append({"id": key[:16]+f"-{i}", "path": str(path), "start": str(a), "end": str(b),
                      "thumbnail": str(thumb), "host": None})
    result.write_text(json.dumps({"key": key, "media": source_key, "range": [start, end],
                                 "algorithm": ALGORITHM, "shots": shots}), encoding="utf-8")
    return shots


def propose_background(shots, references, cancel):
    """Match editor-selected, person-free regions. Proposals always need editor review."""
    import cv2
    import numpy as np
    orb = cv2.ORB_create(nfeatures=1200)
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    def features(image, roi):
        h, w = image.shape[:2]
        x, y, rw, rh = roi
        if not (0 <= x < 1 and 0 <= y < 1 and rw > 0 and rh > 0 and x+rw <= 1 and y+rh <= 1):
            raise ValueError("Invalid background rectangle.")
        crop = image[int(y*h):int((y+rh)*h), int(x*w):int((x+rw)*w)]
        return orb.detectAndCompute(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), None)
    refs = []
    for ref in references:
        shot = next(s for s in shots if s["id"] == ref["id"])
        if not ref.get("backgroundOnly"):
            raise ValueError("Confirm that the selected background region excludes people.")
        refs.append((ref["roi"], features(cv2.imread(shot["thumbnail"]), ref["roi"])))
    for shot in shots:
        if Path(cancel).exists():
            raise InterruptedError("Analysis cancelled.")
        scores = []
        cap = cv2.VideoCapture(shot["path"])
        try:
            # Three independent geometric checks; never presented as guaranteed identity.
            for fraction in (.1, .5, .9):
                a, b = int(shot["start"]), int(shot["end"])
                cap.set(cv2.CAP_PROP_POS_MSEC, (a+(b-a)*fraction)/TPS*1000)
                ok, image = cap.read()
                best = 0
                if ok:
                    image = cv2.resize(image, (480, round(image.shape[0]*480/image.shape[1])))
                    for roi, (kp1, desc1) in refs:
                        kp2, desc2 = features(image, roi)
                        if desc1 is None or desc2 is None or len(desc2) < 2:
                            continue
                        pairs = matcher.knnMatch(desc1, desc2, k=2)
                        good = [pair[0] for pair in pairs if len(pair) == 2 and pair[0].distance < .7*pair[1].distance]
                        if len(good) < 12:
                            continue
                        _, mask = cv2.findHomography(np.float32([kp1[m.queryIdx].pt for m in good]),
                                                    np.float32([kp2[m.trainIdx].pt for m in good]), cv2.RANSAC, 3)
                        if mask is not None and mask.sum()/len(good) >= .7:
                            best = max(best, int(mask.sum()))
                scores.append(best)
        finally:
            cap.release()
        shot["proposedHost"] = bool(scores and min(scores) >= 12)
        shot["backgroundInliers"] = scores
    return shots
