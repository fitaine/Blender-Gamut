"""Build the installable extension zips (one per platform) into ./dist.

    python tools/build.py [--blender PATH]

Downloads the Pillow wheels listed in gamut_viewer/blender_manifest.toml,
then runs Blender's own extension builder. Needs Python with pip, and
Blender 4.2 or newer (found on PATH, via --blender, or the BLENDER variable).
"""

import argparse
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "gamut_viewer")
WHEELS = os.path.join(SRC, "wheels")
DIST = os.path.join(ROOT, "dist")

WHEEL_RE = re.compile(r"^(?P<name>[^-]+)-(?P<version>[^-]+)-cp(?P<py>\d)(?P<minor>\d+)-[^-]+-(?P<plat>[^.]+(?:\.[^.]+)*?)\.whl$")


def manifest_wheels():
    text = open(os.path.join(SRC, "blender_manifest.toml"), encoding="utf-8").read()
    block = re.search(r"wheels\s*=\s*\[(.*?)\]", text, re.S).group(1)
    return [os.path.basename(p) for p in re.findall(r'"([^"]+\.whl)"', block)]


def fetch(filename):
    target = os.path.join(WHEELS, filename)
    if os.path.exists(target):
        return
    m = WHEEL_RE.match(filename)
    if not m:
        sys.exit(f"cannot parse wheel name {filename}")
    plat = m["plat"].split(".")[0]
    cmd = [sys.executable, "-m", "pip", "download", f"{m['name']}=={m['version']}",
           "--only-binary=:all:", "--no-deps", "--quiet",
           "--python-version", f"{m['py']}.{m['minor']}", "--platform", plat, "-d", WHEELS]
    print("fetching", filename)
    subprocess.run(cmd, check=True)
    if not os.path.exists(target):
        sys.exit(f"pip did not produce {filename}; check the name in blender_manifest.toml")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--blender", default=os.environ.get("BLENDER") or shutil.which("blender"))
    args = ap.parse_args()
    if not args.blender:
        sys.exit("Blender not found: pass --blender PATH or set BLENDER")

    os.makedirs(WHEELS, exist_ok=True)
    for w in manifest_wheels():
        fetch(w)

    os.makedirs(DIST, exist_ok=True)
    subprocess.run([args.blender, "--factory-startup", "--command", "extension", "build",
                    "--source-dir", SRC, "--output-dir", DIST, "--split-platforms"], check=True)
    for f in sorted(os.listdir(DIST)):
        print(" ", f, f"{os.path.getsize(os.path.join(DIST, f)) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
