"""Who is speaking, and which one of them is the host.

Speaker labels are what make "a moment where the guest talks" something the tool
can reason about, so this runs before discovery.

Uses sherpa-onnx with ONNX exports of pyannote segmentation + NeMo TitaNet.
pyannote's own weights are installed on this machine but gated behind a
HuggingFace token that does not exist here.

Runs on the GPU when the CUDA execution provider will load, falling back to CPU
otherwise. Install the matching build with:

    pip install "sherpa-onnx==1.13.4+cuda12.cudnn9" -f https://k2-fsa.github.io/sherpa/onnx/cuda.html

Measured on 180s of this podcast: the old CPU-only wheel ran 1.67x realtime, the
CUDA wheel runs 4.72x on CPU and 5.72x on GPU. Most of that gain is the newer
onnxruntime the CUDA build ships, not the GPU itself, which only adds about 20%.

Still runs in **resumable chunks**: an hour of audio is roughly 11 minutes of
work, and caching each chunk means an interrupted run resumes instead of
starting over.

Speaker identity is settled globally at the end, not per chunk: one embedding
per segment, clustered with sklearn. sherpa's own automatic clustering is not
used on purpose, it once produced 207 speakers on a two-person recording.

Usage: python diarize.py <media> [--speakers 2] [--budget 480]
"""
import argparse
import json
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
HOST_VOICE = CACHE_DIR / "host_voice.npy"
SAMPLE_RATE = 16000
# matters on the CPU path; the default 4 is ~4x slower on this 16-thread i7
NUM_THREADS = 12
CHUNK_S = 300.0
DEFAULT_BUDGET_S = 480.0
# a cluster holding less than this much speech is noise, not a person
MIN_SPEAKER_S = 60.0
HOST_MATCH_MIN = 0.55
INTRO_S = 45.0


def cache_path_for(media: Path) -> Path:
    return CACHE_DIR / f"{media.stem}.speakers.json"


def work_dir_for(media: Path) -> Path:
    return CACHE_DIR / f"{media.stem}.diar"


def extract_audio(media: Path, dest: Path) -> Path:
    """16 kHz mono PCM. -vn matters: sources here are multi-GB 1080p masters."""
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-nostats", "-i", str(media),
         "-vn", "-sn", "-dn", "-map", "0:a:0",
         "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(dest)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0 or not dest.exists():
        raise RuntimeError(f"ffmpeg failed extracting audio:\n{(proc.stderr or '')[-500:]}")
    return dest


def load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        if w.getnchannels() != 1 or w.getframerate() != SAMPLE_RATE:
            raise RuntimeError("expected 16 kHz mono")
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def _diarizer(num_speakers: int, models: dict, provider: str = "cuda"):
    import sherpa_onnx

    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=models["segmentation"]),
            num_threads=NUM_THREADS, provider=provider),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=models["embedding"], num_threads=NUM_THREADS, provider=provider),
        # Pinned per chunk. Global identity is resolved later by clustering the
        # embeddings, so this only has to separate voices within one chunk.
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=num_speakers),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    if not config.validate():
        raise RuntimeError("sherpa-onnx rejected the diarization config")
    return sherpa_onnx.OfflineSpeakerDiarization(config)


def _extractor(models: dict, provider: str = "cuda"):
    import sherpa_onnx

    return sherpa_onnx.SpeakerEmbeddingExtractor(
        sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=models["embedding"], num_threads=NUM_THREADS, provider=provider))


def build_engines(num_speakers: int, models: dict, provider: str):
    """CUDA if it will load, CPU otherwise.

    The CUDA execution provider needs cudart/cublas/cudnn, which come from pip
    wheels here rather than a system CUDA install, so their DLL directories have
    to be on the search path before onnxruntime tries to load its provider.
    Falling back matters: a GPU that is busy with a Premiere render should slow
    this down, not break it.
    """
    if provider == "cuda":
        try:
            import transcribe

            transcribe.add_nvidia_dll_dirs()
            sd = _diarizer(num_speakers, models, "cuda")
            ex = _extractor(models, "cuda")
            print("[diarize] running on the GPU", flush=True)
            return sd, ex
        except Exception as e:
            print(f"[diarize] CUDA unavailable ({type(e).__name__}: "
                  f"{str(e)[:120]}), using CPU", flush=True)
    sd = _diarizer(num_speakers, models, "cpu")
    ex = _extractor(models, "cpu")
    print("[diarize] running on the CPU", flush=True)
    return sd, ex


# TitaNet has a hard input ceiling: its internal mask is fixed at 12288 frames
# (122.9s). Measured on both GPU and CPU: 120s embeds, 125s crashes with
# "condition operand cannot broadcast on dim 2". Guests do talk for two minutes
# straight, so long segments are sampled from the middle. A voice is identified
# long before 60 seconds; more audio adds nothing to the embedding.
MAX_EMBED_S = 60.0


def _embed(extractor, audio: np.ndarray) -> np.ndarray | None:
    if len(audio) < SAMPLE_RATE // 2:
        return None
    limit = int(MAX_EMBED_S * SAMPLE_RATE)
    if len(audio) > limit:
        start = (len(audio) - limit) // 2
        audio = audio[start:start + limit]
    try:
        stream = extractor.create_stream()
        stream.accept_waveform(sample_rate=SAMPLE_RATE, waveform=audio)
        stream.input_finished()
        if not extractor.is_ready(stream):
            return None
        return np.array(extractor.compute(stream), dtype=np.float32)
    except RuntimeError as e:
        # One unembeddable segment must not throw away a whole run. It only
        # loses its own vote in the clustering; its time stays in the turns of
        # the neighbouring segments.
        print(f"[diarize] skipped a {len(audio) / SAMPLE_RATE:.0f}s segment the "
              f"embedding model rejected: {str(e)[:80]}", flush=True)
        return None


def run_chunks(media: Path, num_speakers: int, budget_s: float,
               provider: str = "cuda") -> dict:
    """Work through pending chunks until the time budget runs out."""
    import models as model_store

    paths = model_store.ensure_models()
    work = work_dir_for(media)
    work.mkdir(parents=True, exist_ok=True)

    wav = CACHE_DIR / f"{media.stem}.16k.wav"
    if not wav.exists():
        print("[diarize] extracting audio", flush=True)
    extract_audio(media, wav)
    audio = load_wav(wav)
    duration = len(audio) / SAMPLE_RATE
    n_chunks = int(np.ceil(duration / CHUNK_S))

    pending = [i for i in range(n_chunks) if not (work / f"chunk_{i:03d}.json").exists()]
    print(f"[diarize] {duration/60:.1f} min, {n_chunks} chunks, {len(pending)} pending",
          flush=True)
    if not pending:
        return {"done": True, "n_chunks": n_chunks, "duration": duration}

    sd, extractor = build_engines(num_speakers, paths, provider)
    started = time.time()
    processed = 0

    for i in pending:
        if time.time() - started > budget_s:
            break
        a = int(i * CHUNK_S * SAMPLE_RATE)
        b = min(len(audio), int((i + 1) * CHUNK_S * SAMPLE_RATE))
        piece = audio[a:b]
        if len(piece) < SAMPLE_RATE:
            (work / f"chunk_{i:03d}.json").write_text("[]", encoding="utf-8")
            continue

        t0 = time.time()
        result = sd.process(piece).sort_by_start_time()
        offset = i * CHUNK_S
        segs, vecs = [], []
        for s in result:
            seg = {"start": round(s.start + offset, 3), "end": round(s.end + offset, 3)}
            vec = _embed(extractor, piece[int(s.start * SAMPLE_RATE):int(s.end * SAMPLE_RATE)])
            if vec is None:
                continue
            segs.append(seg)
            vecs.append(vec)

        (work / f"chunk_{i:03d}.json").write_text(
            json.dumps(segs), encoding="utf-8")
        np.save(work / f"chunk_{i:03d}.npy",
                np.array(vecs, dtype=np.float32) if vecs else np.zeros((0, 1), np.float32))
        processed += 1
        print(f"[diarize] chunk {i+1}/{n_chunks}: {len(segs)} segments "
              f"in {time.time()-t0:.0f}s", flush=True)

    remaining = [i for i in range(n_chunks) if not (work / f"chunk_{i:03d}.json").exists()]
    print(f"[diarize] {processed} chunks this pass, {len(remaining)} still pending",
          flush=True)
    return {"done": not remaining, "n_chunks": n_chunks, "duration": duration,
            "remaining": len(remaining)}


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    return 0.0 if na == 0 or nb == 0 else float(np.dot(a, b) / (na * nb))


def cluster_speakers(media: Path, num_speakers: int) -> tuple[list[dict], dict]:
    """One label per segment, decided across the whole episode at once."""
    from sklearn.cluster import AgglomerativeClustering

    work = work_dir_for(media)
    segs, vecs = [], []
    for jf in sorted(work.glob("chunk_*.json")):
        chunk_segs = json.loads(jf.read_text(encoding="utf-8"))
        chunk_vecs = np.load(jf.with_suffix(".npy"))
        if len(chunk_segs) != len(chunk_vecs):
            continue
        segs.extend(chunk_segs)
        vecs.extend(chunk_vecs)
    if not segs:
        return [], {}

    X = np.array(vecs, dtype=np.float32)
    X /= np.maximum(np.linalg.norm(X, axis=1, keepdims=True), 1e-9)
    k = min(num_speakers, len(X))
    dur = np.array([s["end"] - s["start"] for s in segs], dtype=np.float32)
    # Fit the clusters on segments long enough to embed reliably, then assign
    # every segment, fragments included, to the nearest centroid.
    #
    # Sub-second fragments ("nici", "să", "poți") give noisy embeddings that sit
    # far from everyone. Clustering all segments lets those outliers take a whole
    # cluster: sample B came out 58.5 min / 0.3 min with complete linkage,
    # a two-person interview labelled as one voice, and average linkage did the
    # same to sample A. Silhouette cannot catch it either, since it prefers
    # the degenerate split. Fitting on segments of at least min_fit_s gives
    # 36.6 / 22.2 on sample B and 40.3 / 15.1 on sample A, matching ward and kmeans.
    min_fit_s = 3.0
    if k > 1:
        fit = dur >= min_fit_s
        if fit.sum() < max(10, k * 3):
            fit = np.ones(len(X), dtype=bool)
        fit_labels = AgglomerativeClustering(
            n_clusters=k, metric="cosine", linkage="complete").fit_predict(X[fit])
        cents = np.array([X[fit][fit_labels == l].mean(axis=0) for l in range(k)])
        cents /= np.maximum(np.linalg.norm(cents, axis=1, keepdims=True), 1e-9)
        labels = np.argmax(X @ cents.T, axis=1)
    else:
        labels = np.zeros(len(X), dtype=int)

    if k > 1:
        held = {int(l): sum(s["end"] - s["start"]
                            for s, lab in zip(segs, labels) if lab == l)
                for l in set(labels.tolist())}
        if min(held.values()) < MIN_SPEAKER_S:
            print(f"[diarize] WARNING: clustering is lopsided {held}. Expected "
                  f"{k} speakers but one holds almost nothing, so the labels are "
                  f"probably wrong rather than merely noisy.")

    turns = [{**s, "speaker": int(l)} for s, l in zip(segs, labels)]
    embeds = {int(l): X[labels == l].mean(axis=0) for l in set(labels.tolist())}
    return turns, embeds


def _merge_tiny_speakers(turns: list[dict], embeds: dict) -> list[dict]:
    totals = {}
    for t in turns:
        totals[t["speaker"]] = totals.get(t["speaker"], 0.0) + (t["end"] - t["start"])
    real = [s for s, d in totals.items() if d >= MIN_SPEAKER_S]
    tiny = [s for s in totals if s not in real]
    if not tiny or not real:
        return turns
    for s in tiny:
        sims = {r: _cosine(embeds[s], embeds[r]) for r in real if r in embeds}
        target = max(sims, key=sims.get) if sims else max(real, key=lambda r: totals[r])
        print(f"[diarize] speaker {s} holds {totals[s]:.0f}s, merging into {target}")
        for t in turns:
            if t["speaker"] == s:
                t["speaker"] = target
    return turns


def identify_host(turns: list[dict], embeds: dict, words: list[dict] | None) -> dict:
    """Enrolled voiceprint if we have one, otherwise how a host behaves."""
    totals = {}
    for t in turns:
        totals[t["speaker"]] = totals.get(t["speaker"], 0.0) + (t["end"] - t["start"])
    speakers = sorted(totals)

    if HOST_VOICE.exists():
        try:
            ref = np.load(HOST_VOICE)
            sims = {s: _cosine(embeds[s], ref) for s in speakers if s in embeds}
            best = max(sims, key=sims.get) if sims else None
            if best is not None and sims[best] >= HOST_MATCH_MIN:
                return {"host": best, "method": "voiceprint",
                        "confidence": round(sims[best], 3),
                        "similarities": {str(k): round(v, 3) for k, v in sims.items()}}
        except Exception as e:
            print(f"[diarize] voiceprint unusable ({e}), using heuristics")

    # The host opens the episode, asks the questions, and in an interview
    # speaks least.
    scores = {s: 0.0 for s in speakers}
    detail = {}
    for s in speakers:
        opened = any(t["speaker"] == s and t["start"] < INTRO_S for t in turns)
        if opened:
            scores[s] += 2.0
        detail[s] = {"opens": opened, "minutes": round(totals[s] / 60, 1)}
    if words:
        for s in speakers:
            said = []
            for t in turns:
                if t["speaker"] != s:
                    continue
                said.extend(w["word"] for w in words if t["start"] <= w["start"] < t["end"])
            text = "".join(said)
            per100 = text.count("?") / max(1.0, len(text.split()) / 100)
            scores[s] += 3.0 * per100
            detail[s]["questions_per_100w"] = round(per100, 2)
    if totals:
        scores[min(totals, key=totals.get)] += 1.5

    host = max(scores, key=scores.get)
    return {"host": host, "method": "heuristic", "confidence": None,
            "scores": {str(k): round(v, 2) for k, v in scores.items()},
            "detail": {str(k): v for k, v in detail.items()}}


def save_host_voice(embeds: dict, speaker: int) -> bool:
    vec = embeds.get(speaker)
    if vec is None:
        return False
    HOST_VOICE.parent.mkdir(parents=True, exist_ok=True)
    np.save(HOST_VOICE, vec)
    return True


def finalize(media: Path, num_speakers: int, words: list[dict] | None) -> dict:
    turns, embeds = cluster_speakers(media, num_speakers)
    if not turns:
        raise RuntimeError("no diarization chunks found")
    turns = _merge_tiny_speakers(turns, embeds)
    live = {t["speaker"] for t in turns}
    embeds = {s: v for s, v in embeds.items() if s in live}

    host_info = identify_host(turns, embeds, words)
    totals = {}
    for t in turns:
        totals[t["speaker"]] = totals.get(t["speaker"], 0.0) + (t["end"] - t["start"])

    data = {
        "media": str(media),
        "turns": turns,
        "totals": {str(k): round(v, 2) for k, v in totals.items()},
        "host": host_info["host"],
        "host_detection": host_info,
        "speakers": sorted(totals),
    }
    out = cache_path_for(media)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    np.save(CACHE_DIR / f"{media.stem}.embeds.npy",
            np.array([embeds[s] for s in sorted(embeds)], dtype=np.float32))
    print(f"[diarize] -> {out}")
    return data


def diarize(media: Path, num_speakers: int = 2, words: list[dict] | None = None,
            budget_s: float = DEFAULT_BUDGET_S, force: bool = False,
            provider: str = "cuda") -> dict:
    out = cache_path_for(media)
    if out.exists() and not force:
        return json.loads(out.read_text(encoding="utf-8"))
    state = run_chunks(media, num_speakers, budget_s, provider)
    if not state["done"]:
        return {"incomplete": True, **state}
    return finalize(media, num_speakers, words)


def label_of(speaker: int, host: int) -> str:
    return "HOST" if speaker == host else "GUEST"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("media")
    ap.add_argument("--speakers", type=int, default=2)
    ap.add_argument("--budget", type=float, default=DEFAULT_BUDGET_S)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--provider", default="cuda", choices=["cuda", "cpu"])
    args = ap.parse_args()

    media = Path(args.media)
    words = None
    import transcribe
    wp = transcribe.cache_path_for(media)
    if wp.exists():
        words = json.loads(wp.read_text(encoding="utf-8"))["words"]

    data = diarize(media, args.speakers, words, args.budget, args.force, args.provider)
    if data.get("incomplete"):
        print(f"\n  {data['remaining']} chunks left, run again to continue")
        sys.exit(3)
    print()
    for s in data["speakers"]:
        tag = "HOST " if s == data["host"] else "guest"
        print(f"  speaker {s}  {tag}  {data['totals'][str(s)]/60:6.1f} min")
    print(f"\n  detection: {data['host_detection']['method']} "
          f"{data['host_detection'].get('confidence') or ''}")

