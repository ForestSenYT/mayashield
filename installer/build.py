# -*- coding: utf-8 -*-
"""Builds dist/MayaShield-<version>-Setup.exe and dist/MayaShield-<version>-module.zip.

    python installer/build.py               # everything, signed if the certificate is there
    python installer/build.py --stage-only  # only build/stage/MayaShield (used by tests/run_gui_test.ps1)
    python installer/build.py --no-sign     # leave the Setup.exe unsigned

Needs only Windows' own .NET Framework 4 compiler (csc.exe) for the Setup.exe.  The
installer's source is C# 5, the newest language that compiler accepts.

Signing uses a code-signing certificate with the subject CN=4rest from the current
user's certificate store (self-signed is fine; see the README).  The private key never
leaves that store.  Without the certificate the build still works, unsigned.
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
SIGN_SUBJECT = "CN=4rest"
TIMESTAMP_SERVER = "http://timestamp.digicert.com"

# Maya remembers a hash of every userSetup.py it runs and, by default, warns the user
# when one changes.  The bootstrap is therefore kept byte-identical between releases:
# this is its SHA-256 as shipped since 4.0.0 (LF line endings).  If you really have to
# change the bootstrap, every user sees that warning once after upgrading; update the
# hash here on purpose.
BOOTSTRAP_SHA256 = "9fed04194feed823da9050419a72740ca8e824ded99a4b53b8af883f74e4fe11"


def version():
    with open(os.path.join(ROOT, "mayashield_core", "__init__.py"), encoding="utf-8") as handle:
        return re.search(r'^__version__\s*=\s*"([^"]+)"', handle.read(), re.M).group(1)


def read_lf(path):
    """A source file's bytes with LF line endings, whatever the checkout made of them."""
    with open(path, "rb") as handle:
        return handle.read().replace(b"\r\n", b"\n")


# ---------------------------------------------------------------- staging
def core_files(allow_untracked):
    """The engine's source files.  In a git checkout, exactly the tracked ones.

    A scratch file left in the package would otherwise ship to every user and be
    listed in the manifest as part of MayaShield.
    """
    folder = os.path.join(ROOT, "mayashield_core")
    present = sorted(os.path.basename(path) for path in glob.glob(os.path.join(folder, "*.py")))
    try:
        listed = subprocess.run(["git", "ls-files", "--", "mayashield_core"], cwd=ROOT, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, check=True).stdout.decode("utf-8").split("\n")
    except (OSError, subprocess.CalledProcessError):
        return present   # not a git checkout (a source archive): take what is there
    tracked = sorted(name.split("/", 1)[1] for name in listed
                     if name.startswith("mayashield_core/") and name.count("/") == 1 and name.endswith(".py"))
    stray = [name for name in present if name not in tracked]
    missing = [name for name in tracked if name not in present]
    if missing:
        sys.exit("Tracked engine files are missing from the working tree: %s" % ", ".join(missing))
    if stray and not allow_untracked:
        sys.exit("These files in mayashield_core are not tracked by git and would not ship: %s\n"
                 "`git add` them, delete them, or pass --allow-untracked to ship them anyway." % ", ".join(stray))
    return present if allow_untracked else tracked


def stage(ver, allow_untracked=False):
    """build/stage/MayaShield: the module folder exactly as it lands on disk."""
    target = os.path.join(STAGE, MODULE)
    shutil.rmtree(STAGE, ignore_errors=True)
    package = os.path.join(target, "scripts", "mayashield_core")
    os.makedirs(package)
    os.makedirs(os.path.join(target, "plug-ins"))
    os.makedirs(os.path.join(target, "icons"))

    def put(data, *parts):
        with open(os.path.join(target, *parts), "wb") as handle:
            handle.write(data)

    bootstrap = read_lf(os.path.join(HERE, "payload", "userSetup.py"))
    if hashlib.sha256(bootstrap).hexdigest() != BOOTSTRAP_SHA256:
        sys.exit("installer/payload/userSetup.py has changed.  Maya will warn every user about it after they\n"
                 "upgrade.  Restore it, or update BOOTSTRAP_SHA256 in installer/build.py if that is intended.")
    put(read_lf(os.path.join(ROOT, "mayashield.py")), "plug-ins", "mayashield.py")
    put(bootstrap, "scripts", "userSetup.py")
    files = {}
    for name in core_files(allow_untracked):
        data = read_lf(os.path.join(ROOT, "mayashield_core", name))
        put(data, "scripts", "mayashield_core", name)
        files[name] = hashlib.sha256(data).hexdigest()
    # persistence.py trusts files in its own folder only when they match this list.
    manifest = json.dumps({"version": ver, "files": files}, indent=2, sort_keys=True) + "\n"
    put(manifest.encode("utf-8"), "scripts", "mayashield_core", "manifest.json")
    put(png(32), "icons", "mayashield.png")
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
    # mtime=0 and no file name: the same sources give the same bytes.
    with open(out_path, "wb") as handle:
        with gzip.GzipFile(filename="", mode="wb", fileobj=handle, compresslevel=9, mtime=0) as packed:
            packed.write(raw.getvalue())


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
    numeric = ".".join((re.findall(r"\d+", ver) + ["0"] * 4)[:4])
    info = os.path.join(BUILD, "BuildInfo.cs")
    with open(info, "w", encoding="utf-8") as handle:
        handle.write("namespace MayaShieldSetup\n{\n    static class BuildInfo\n    {\n"
                     "        public const string Version = \"%s\";\n    }\n}\n" % ver)
    assembly_info = os.path.join(BUILD, "AssemblyInfo.cs")
    with open(assembly_info, "w", encoding="utf-8") as handle:
        handle.write("using System.Reflection;\n"
                     "[assembly: AssemblyTitle(\"MayaShield Setup\")]\n"
                     "[assembly: AssemblyProduct(\"MayaShield\")]\n"
                     "[assembly: AssemblyCompany(\"4rest\")]\n"
                     "[assembly: AssemblyCopyright(\"Copyright (c) 2026 4rest. MIT License.\")]\n"
                     "[assembly: AssemblyDescription(\"MayaShield %s Setup\")]\n"
                     "[assembly: AssemblyVersion(\"%s\")]\n"
                     "[assembly: AssemblyFileVersion(\"%s\")]\n"
                     "[assembly: AssemblyInformationalVersion(\"%s\")]\n" % (ver, numeric, numeric, ver))
    manifest = os.path.join(BUILD, "app.manifest")
    with open(os.path.join(HERE, "app.manifest"), encoding="utf-8") as handle:
        text = handle.read()
    with open(manifest, "w", encoding="utf-8") as handle:
        handle.write(text.replace('assemblyIdentity version="1.0.0.0" name="MayaShield.Setup"',
                                  'assemblyIdentity version="%s" name="MayaShield.Setup"' % numeric))
    sources = sorted(glob.glob(os.path.join(HERE, "src", "*.cs"))) + [info, assembly_info]
    command = [find_csc(), "/nologo", "/target:winexe", "/optimize+", "/platform:anycpu", "/codepage:65001",
               "/warnaserror+",
               "/out:" + out_path,
               "/win32icon:" + icon_path,
               "/win32manifest:" + manifest,
               "/resource:%s,MayaShield.payload" % payload_path,
               "/reference:System.dll", "/reference:System.Core.dll",
               "/reference:System.Drawing.dll", "/reference:System.Windows.Forms.dll"] + sources
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = result.stdout.decode(sys.stdout.encoding or "utf-8", "replace").strip()
    if output:
        print(output)
    if result.returncode != 0:
        sys.exit("csc failed")


# ---------------------------------------------------------------- signing
_SIGN_SCRIPT = r"""
param([string]$File, [string]$Subject, [string]$Timestamp, [string]$CerOut)
$ErrorActionPreference = 'Stop'
$cert = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert |
    Where-Object { $_.Subject -eq $Subject -and $_.HasPrivateKey -and $_.NotAfter -gt (Get-Date) } |
    Sort-Object NotAfter -Descending | Select-Object -First 1
if (-not $cert) { Write-Output 'NOCERT'; exit 3 }
$stamped = $true
try {
    $result = Set-AuthenticodeSignature -FilePath $File -Certificate $cert -HashAlgorithm SHA256 -TimestampServer $Timestamp
    if (-not $result.SignerCertificate) { throw 'no signature' }
} catch {
    # No network, or the timestamp service is down: a signature without a timestamp is still a signature.
    $stamped = $false
    $result = Set-AuthenticodeSignature -FilePath $File -Certificate $cert -HashAlgorithm SHA256
}
if (-not $result.SignerCertificate) { Write-Output 'FAILED'; exit 4 }
if ($CerOut) { [System.IO.File]::WriteAllBytes($CerOut, $cert.Export('Cert')) }
Write-Output ("SIGNED|{0}|{1}|{2}|{3}" -f $cert.Subject, $cert.Thumbprint, $cert.NotAfter.ToString('yyyy-MM-dd'), $stamped)
"""


def sign(path, subject, cer_out=None):
    """Sign with the certificate from the user's store.  Returns a line describing the outcome."""
    script = os.path.join(BUILD, "sign.ps1")
    with open(script, "w", encoding="utf-8-sig") as handle:
        handle.write(_SIGN_SCRIPT)
    command = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script, "-File", path,
               "-Subject", subject, "-Timestamp", TIMESTAMP_SERVER]
    if cer_out:
        command += ["-CerOut", cer_out]
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as error:
        return "not signed: could not run PowerShell (%s)" % error
    lines = [line.strip() for line in result.stdout.decode("utf-8", "replace").splitlines() if line.strip()]
    last = lines[-1] if lines else ""
    if last.startswith("SIGNED|"):
        _tag, name, thumbprint, expires, stamped = last.split("|")
        return "signed by %s (thumbprint %s, valid until %s)%s" % (
            name, thumbprint, expires, ", timestamped" if stamped == "True" else ", NOT timestamped (no network?)")
    if last == "NOCERT":
        return ("not signed: no code-signing certificate with the subject %s in this user's certificate store. "
                "See the README for creating one." % subject)
    return "not signed: %s" % (result.stderr.decode("utf-8", "replace").strip()[-300:] or last or "unknown error")


def module_zip(ver, folder, out_path):
    """For macOS, Linux and manual installs: unzip into <Maya user folder>/<version>/modules/."""
    def add(archive, name, data):
        info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))   # fixed: same sources, same zip
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        archive.writestr(info, data)

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as archive:
        add(archive, "mayashield.mod", ("+ mayashield %s ./%s\n" % (ver, MODULE)).encode("ascii"))
        for relative, path in staged_files(folder):
            with open(path, "rb") as handle:
                add(archive, MODULE + "/" + relative, handle.read())


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stage-only", action="store_true", help="Only build build/stage/MayaShield")
    parser.add_argument("--no-sign", action="store_true", help="Do not sign the Setup.exe")
    parser.add_argument("--sign-subject", default=SIGN_SUBJECT, help="Certificate subject to sign with (default %(default)s)")
    parser.add_argument("--allow-untracked", action="store_true",
                        help="Ship engine files git does not track yet (for trying things out before a commit)")
    args = parser.parse_args()

    ver = version()
    folder = stage(ver, args.allow_untracked)
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
    if args.no_sign:
        print("  not signed (--no-sign)")
    else:
        print("  " + sign(setup, args.sign_subject, os.path.join(DIST, "MayaShield-codesign.cer")))
    archive = os.path.join(DIST, "MayaShield-%s-module.zip" % ver)
    module_zip(ver, folder, archive)
    print("built %s" % archive)
    return 0


if __name__ == "__main__":
    sys.exit(main())
