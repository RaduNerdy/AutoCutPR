"""Generate an H.264 export preset (.epr) with a bitrate cap.

Premiere cannot set a bitrate from script — `exportAsMediaDirect` only takes a
preset file. Adobe's .epr files are plain XML, so the reliable way to get an
exact cap is to clone a stock H.264 preset and rewrite three parameters. Cloning
rather than authoring from scratch keeps every other key (profile, level, audio,
colour) exactly as Adobe shipped it.

Usage: python preset.py --out path.epr [--mbps 10]
"""
import argparse
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# stock H.264 presets live under NICK_H264 (fourcc "NICK"/"H264")
SEARCH_ROOTS = [
    r"C:\Program Files\Adobe\Adobe Premiere Pro {v}\MediaIO\systempresets\4E49434B_48323634",
    r"C:\Program Files\Adobe\Adobe Media Encoder {v}\MediaIO\systempresets\4E49434B_48323634",
]
VERSIONS = ["2026", "2025", "2024", "2023"]
# a Match Source preset keeps resolution/frame rate tied to the sequence, so the
# same file works for any reel size
BASE_NAMES = [
    "00 - Match Source - High bitrate.epr",
    "01 - Match Source - High bitrate.epr",
    "00 - Match Source - Medium bitrate.epr",
]
# 0 = CBR, 1 = VBR 1-pass, 2 = VBR 2-pass. 1-pass is plenty for social delivery
# and halves render time versus the 2-pass the stock preset uses.
BITRATE_ENCODING = 1


def find_base_preset() -> Path | None:
    for root_tpl in SEARCH_ROOTS:
        for v in VERSIONS:
            root = Path(root_tpl.format(v=v))
            if not root.is_dir():
                continue
            for name in BASE_NAMES:
                p = root / name
                if p.exists():
                    return p
            for p in sorted(root.glob("*Match Source*.epr")):
                return p
    return None


def set_param(xml: str, identifier: str, value: str) -> tuple[str, bool]:
    """Rewrite the <ParamValue> belonging to one <ParamIdentifier> block."""
    pattern = re.compile(
        r"(<ParamIdentifier>" + re.escape(identifier) +
        r"</ParamIdentifier>[\s\S]*?<ParamValue>)([^<]*)(</ParamValue>)"
    )
    new_xml, n = pattern.subn(lambda m: m.group(1) + value + m.group(3), xml, count=1)
    return new_xml, n == 1


def build_preset(out_path: Path, mbps: float, base: Path | None = None) -> dict:
    base = base or find_base_preset()
    if base is None:
        return {"error": "no stock H.264 preset found — is Premiere installed?"}

    xml = base.read_text(encoding="utf-8")
    changed = []
    missing = []
    for ident, value in (
        # Match-source ships as false and the stored settings are 1920x1080 at
        # 23.976fps — exporting a 1080x1920 25fps reel with those would come out
        # sideways and at the wrong rate. True makes resolution, frame rate, PAR
        # and field order follow the sequence; bitrate is unaffected by it.
        ("ADBEVideoMatchSource", "true"),
        ("ADBEVideoBitrateEncoding", str(BITRATE_ENCODING)),
        ("ADBEVideoTargetBitrate", f"{mbps:g}."),
        ("ADBEVideoMaxBitrate", f"{mbps:g}."),
    ):
        xml, ok = set_param(xml, ident, value)
        (changed if ok else missing).append(ident)

    if missing:
        return {"error": f"preset is missing parameters: {', '.join(missing)}"}

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(xml, encoding="utf-8")
    return {"path": str(out_path), "base": str(base), "mbps": mbps, "changed": changed}


def read_params(path: Path, idents: list[str]) -> dict:
    xml = path.read_text(encoding="utf-8")
    out = {}
    for i in idents:
        m = re.search(
            r"<ParamIdentifier>" + re.escape(i) +
            r"</ParamIdentifier>[\s\S]*?<ParamValue>([^<]*)</ParamValue>", xml)
        out[i] = m.group(1) if m else None
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--mbps", type=float, default=10.0)
    args = ap.parse_args()
    res = build_preset(Path(args.out), args.mbps)
    if res.get("error"):
        print("[preset] " + res["error"])
        sys.exit(1)
    print(f"[preset] {res['path']}  ({res['mbps']:g} Mbps max, from {Path(res['base']).name})")
    print("PRESET=" + res["path"])
