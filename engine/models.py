"""Fetch the speaker-diarization models once into models/.

Both are ungated releases from the sherpa-onnx project. pyannote's own weights
are installed on this machine but gated behind a HuggingFace token that does not
exist here, which is why the ONNX exports are used instead.

Usage: python models.py [--force]
"""
import sys
import tarfile
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MODEL_DIR = Path(__file__).resolve().parent.parent / "models"

SEGMENTATION = {
    "name": "sherpa-onnx-pyannote-segmentation-3-0",
    "url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/"
           "sherpa-onnx-pyannote-segmentation-3-0.tar.bz2",
    "member": "sherpa-onnx-pyannote-segmentation-3-0/model.onnx",
    "target": "segmentation.onnx",
}
EMBEDDING = {
    "name": "nemo_en_titanet_large",
    "url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
           "nemo_en_titanet_large.onnx",
    "target": "embedding.onnx",
}


def _download(url: str, dest: Path):
    print(f"[models] downloading {url.rsplit('/', 1)[-1]}", flush=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")

    def hook(block, size, total):
        if total > 0 and block % 200 == 0:
            pct = min(100, block * size * 100 // total)
            print(f"[models]   {pct}%", end="\r", flush=True)

    urllib.request.urlretrieve(url, tmp, hook)
    tmp.replace(dest)
    print(f"[models] -> {dest} ({dest.stat().st_size / 1e6:.1f} MB)")


def ensure_models(force: bool = False) -> dict:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    seg = MODEL_DIR / SEGMENTATION["target"]
    emb = MODEL_DIR / EMBEDDING["target"]

    if force or not seg.exists():
        archive = MODEL_DIR / "segmentation.tar.bz2"
        _download(SEGMENTATION["url"], archive)
        with tarfile.open(archive, "r:bz2") as tf:
            member = tf.extractfile(SEGMENTATION["member"])
            if member is None:
                raise RuntimeError(f"{SEGMENTATION['member']} missing from archive")
            seg.write_bytes(member.read())
        archive.unlink()
        print(f"[models] -> {seg} ({seg.stat().st_size / 1e6:.1f} MB)")

    if force or not emb.exists():
        _download(EMBEDDING["url"], emb)

    return {"segmentation": str(seg), "embedding": str(emb)}


if __name__ == "__main__":
    paths = ensure_models("--force" in sys.argv)
    for k, v in paths.items():
        print(f"{k:14} {v}")
    print("[models] ready")
