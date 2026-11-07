# -*- coding: utf-8 -*-
"""Builds dist/MayaShield-<version>-Setup.exe and dist/MayaShield-<version>-module.zip.

    python installer/build.py               # everything
    python installer/build.py --stage-only  # only build/stage/MayaShield (used by tests/run_gui_test.ps1)

Needs only Windows' own .NET Framework 4 compiler (csc.exe) for the Setup.exe.  The
installer's source is C# 5, the newest language that compiler accepts.
"""
import argparse
import glob
import gzip
import hashlib
import io
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import zipfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BUILD = os.path.join(ROOT, "build")
STAGE = os.path.join(BUILD, "stage")
DIST = os.path.join(ROOT, "dist")
MODULE = "MayaShield"


def version():
    with open(os.path.join(ROOT, "mayashield_core", "__init__.py"), encoding="utf-8") as handle:
        return re.search(r'^__version__\s*=\s*"([^"]+)"', handle.read(), re.M).group(1)


# ---------------------------------------------------------------- staging
def stage(ver):
    """build/stage/MayaShield: the module folder exactly as it lands on disk."""
    target = os.path.join(STAGE, MODULE)
    shutil.rmtree(STAGE, ignore_errors=True)
    package = os.path.join(target, "scripts", "mayashield_core")
    os.makedirs(package)
    os.makedirs(os.path.join(target, "plug-ins"))
    os.makedirs(os.path.join(target, "icons"))

    shutil.copy2(os.path.join(ROOT, "mayashield.py"), os.path.join(target, "plug-ins", "mayashield.py"))
    shutil.copy2(os.path.join(HERE, "payload", "userSetup.py"), os.path.join(target, "scripts", "userSetup.py"))
    files = {}
    for source in sorted(glob.glob(os.path.join(ROOT, "mayashield_core", "*.py"))):
        name = os.path.basename(source)
        shutil.copy2(source, os.path.join(package, name))
        with open(source, "rb") as handle:
            files[name] = hashlib.sha256(handle.read()).hexdigest()
    # persistence.py trusts files in its own folder only when they match this list.
    with open(os.path.join(package, "manifest.json"), "w", encoding="utf-8", newline="\n") as handle:
        json.dump({"version": ver, "files": files}, handle, indent=2, sort_keys=True)
        handle.write("\n")
    with open(os.path.join(target, "icons", "mayashield.png"), "wb") as handle:
        handle.write(png(32))
    return target


def staged_files(folder):
    result = []
    for base, _dirs, names in os.walk(folder):
        for name in sorted(names):
            path = os.path.join(base, name)
            result.append((os.path.relpath(path, folder).replace(os.sep, "/"), path))
    return sorted(result)


def payload(folder, out_path):
    """MSPK: count, then (path length, UTF-8 path, data length, data) per file, all gzipped."""
    entries = staged_files(folder)
    raw = io.BytesIO()
    raw.write(b"MSPK" + struct.pack("<i", len(entries)))
    for relative, path in entries:
        name = relative.encode("utf-8")
        with open(path, "rb") as handle:
            data = handle.read()
        raw.write(struct.pack("<i", len(name)) + name + struct.pack("<i", len(data)) + data)
    with gzip.open(out_path, "wb", compresslevel=9) as handle:
        handle.write(raw.getvalue())


# ---------------------------------------------------------------- icon
def shield_pixel(x, y):
    """RGBA of the shield at (x, y) in 0..1 coordinates, or None outside it."""
    top, bottom, half = 0.07, 0.95, 0.40
    dip = 0.035 * (1 - ((x - 0.5) / half) ** 2) if abs(x - 0.5) < half else 0
    if y < top + dip or y > bottom:
        return None
    knee = 0.50
    if y <= knee:
        width = half
    else:
        t = (y - knee) / (bottom - knee)
        width = half * math.cos(t * math.pi / 2) ** 0.85
    if abs(x - 0.5) > width:
        return None
    # Edge band, then a vertical gradient.
    edge = min(width - abs(x - 0.5), y - top - dip, (bottom - y) * 0.6)
    if edge < 0.045:
        return (16, 54, 140, 255)
    shade = (y - top) / (bottom - top)
    colour = tuple(int(a + (b - a) * shade) for a, b in zip((52, 120, 246), (24, 70, 190)))
    # A check mark.
    for (ax, ay), (bx, by) in (((0.31, 0.50), (0.45, 0.65)), ((0.45, 0.65), (0.71, 0.35))):
        dx, dy = bx - ax, by - ay
        t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)))
        if math.hypot(x - (ax + t * dx), y - (ay + t * dy)) < 0.065:
            return (255, 255, 255, 255)
    return colour + (255,)


def png(size, samples=4):
    rows = []
    for row in range(size):
        line = bytearray([0])
        for column in range(size):
            total = [0, 0, 0, 0]
            for sy in range(samples):
                for sx in range(samples):
                    pixel = shield_pixel((column + (sx + 0.5) / samples) / size, (row + (sy + 0.5) / samples) / size)
                    if pixel:
                        for index in range(3):
                            total[index] += pixel[index] * pixel[3]
                        total[3] += pixel[3]
            alpha = total[3] / (samples * samples)
            if total[3]:
                line += bytes(int(total[index] / total[3]) for index in range(3))
            else:
                line += b"\0\0\0"
            line.append(int(alpha))
        rows.append(bytes(line))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b"")


def ico(out_path, sizes=(16, 24, 32, 48, 256)):
    images = [png(size) for size in sizes]
    offset = 6 + 16 * len(images)
    data = struct.pack("<HHH", 0, 1, len(images))
    for size, image in zip(sizes, images):
        edge = 0 if size >= 256 else size
        data += struct.pack("<BBBBHHII", edge, edge, 0, 0, 1, 32, len(image), offset)
        offset += len(image)
    with open(out_path, "wb") as handle:
        handle.write(data + b"".join(images))


# ---------------------------------------------------------------- compile
def find_csc():
    windows = os.environ.get("WINDIR", r"C:\Windows")
    for framework in ("Framework64", "Framework"):
        path = os.path.join(windows, "Microsoft.NET", framework, "v4.0.30319", "csc.exe")
        if os.path.isfile(path):
            return path
    sys.exit("csc.exe from .NET Framework 4 was not found.")


def compile_setup(ver, payload_path, icon_path, out_path):
    info = os.path.join(BUILD, "BuildInfo.cs")
    with open(info, "w", encoding="utf-8") as handle:
        handle.write("namespace MayaShieldSetup\n{\n    static class BuildInfo\n    {\n"
                     "        public const string Version = \"%s\";\n    }\n}\n" % ver)
    assembly_info = os.path.join(BUILD, "AssemblyInfo.cs")
    numeric = ".".join((re.findall(r"\d+", ver) + ["0"] * 4)[:4])
    with open(assembly_info, "w", encoding="utf-8") as handle:
        handle.write("using System.Reflection;\n"
                     "[assembly: AssemblyTitle(\"MayaShield Setup\")]\n"
                     "[assembly: AssemblyProduct(\"MayaShield\")]\n"
                     "[assembly: AssemblyCompany(\"4rest\")]\n"
                     "[assembly: AssemblyDescription(\"MayaShield %s Setup\")]\n"
                     "[assembly: AssemblyVersion(\"%s\")]\n"
                     "[assembly: AssemblyFileVersion(\"%s\")]\n"
                     "[assembly: AssemblyInformationalVersion(\"%s\")]\n" % (ver, numeric, numeric, ver))
    sources = sorted(glob.glob(os.path.join(HERE, "src", "*.cs"))) + [info, assembly_info]
    command = [find_csc(), "/nologo", "/target:winexe", "/optimize+", "/platform:anycpu", "/codepage:65001",
               "/out:" + out_path,
               "/win32icon:" + icon_path,
               "/win32manifest:" + os.path.join(HERE, "app.manifest"),
               "/resource:%s,MayaShield.payload" % payload_path,
               "/reference:System.dll", "/reference:System.Core.dll",
               "/reference:System.Drawing.dll", "/reference:System.Windows.Forms.dll"] + sources
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = result.stdout.decode(sys.stdout.encoding or "utf-8", "replace").strip()
    if output:
        print(output)
    if result.returncode != 0:
        sys.exit("csc failed")


def module_zip(ver, folder, out_path):
    """For macOS, Linux and manual installs: unzip into <Maya user folder>/<version>/modules/."""
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mayashield.mod", "+ mayashield %s ./%s\n" % (ver, MODULE))
        for relative, path in staged_files(folder):
            archive.write(path, MODULE + "/" + relative)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stage-only", action="store_true", help="Only build build/stage/MayaShield")
    args = parser.parse_args()

    ver = version()
    folder = stage(ver)
    print("staged %s" % folder)
    if args.stage_only:
        return 0

    os.makedirs(DIST, exist_ok=True)
    payload_path = os.path.join(BUILD, "payload.bin")
    payload(folder, payload_path)
    icon_path = os.path.join(BUILD, "setup.ico")
    ico(icon_path)

    setup = os.path.join(DIST, "MayaShield-%s-Setup.exe" % ver)
    compile_setup(ver, payload_path, icon_path, setup)
    print("built %s" % setup)
    archive = os.path.join(DIST, "MayaShield-%s-module.zip" % ver)
    module_zip(ver, folder, archive)
    print("built %s" % archive)
    return 0


if __name__ == "__main__":
    sys.exit(main())
