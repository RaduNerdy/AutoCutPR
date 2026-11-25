"""Probe whether faster-whisper can run on CUDA on this machine."""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# CTranslate2 needs cuBLAS/cuDNN DLLs; use the pip-installed NVIDIA wheels
import glob
import os
import site

for sp in site.getsitepackages() + [site.getusersitepackages()]:
    for d in glob.glob(os.path.join(sp, "nvidia", "*", "bin")) + glob.glob(
        os.path.join(sp, "nvidia", "*", "lib")
    ):
        os.add_dll_directory(d)
        os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]

try:
    from faster_whisper import WhisperModel

    model = WhisperModel("large-v3-turbo", device="cuda", compute_type="int8_float16")
    segments, info = model.transcribe(sys.argv[1], language="ro", clip_timestamps="0,30")
    for seg in segments:
        print(f"[{seg.end:6.1f}] {seg.text.strip()}")
    print("CUDA OK")
except Exception as e:
    print(f"CUDA FAILED: {type(e).__name__}: {e}")
