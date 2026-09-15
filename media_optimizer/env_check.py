"""Environment and dependency diagnostic utility for Media Optimizer."""

import os
import shutil
import subprocess
import sys
from pathlib import Path


def check_environment() -> dict:
    """Run diagnostics on host environment, tools, and hardware acceleration."""
    report = {
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "python_ok": sys.version_info >= (3, 9),
        "pillow_ok": False,
        "tkinter_ok": False,
        "ffmpeg_ok": False,
        "hevc_vt_ok": False,
        "h264_vt_ok": False,
        "exiftool_ok": False,
        "sips_ok": False,
        "is_apple_silicon": False,
        "issues": [],
        "suggestions": [],
    }

    # Python version
    if not report["python_ok"]:
        report["issues"].append("Python 3.9 or higher is required.")

    # Pillow
    try:
        import PIL
        report["pillow_ok"] = True
    except ImportError:
        report["issues"].append("Pillow is not installed.")
        report["suggestions"].append("Run: pip3 install pillow")

    # Tkinter
    try:
        import tkinter
        report["tkinter_ok"] = True
    except ImportError:
        report["suggestions"].append("Tkinter is missing; app will run in browser-based Web GUI fallback.")

    # Architecture
    if sys.platform == "darwin":
        arch = os.uname().machine
        report["is_apple_silicon"] = (arch == "arm64")

    # sips
    sips_path = shutil.which("sips") or "/usr/bin/sips"
    report["sips_ok"] = os.path.exists(sips_path)

    # ffmpeg
    ffmpeg_path = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    if os.path.exists(ffmpeg_path):
        report["ffmpeg_ok"] = True
        try:
            res = subprocess.run([ffmpeg_path, "-encoders"], capture_output=True, text=True)
            out = res.stdout + res.stderr
            report["hevc_vt_ok"] = "hevc_videotoolbox" in out
            report["h264_vt_ok"] = "h264_videotoolbox" in out
        except Exception:
            pass
    else:
        report["issues"].append("FFmpeg is not installed (required for video compression).")
        report["suggestions"].append("Run: brew install ffmpeg")

    # exiftool
    exiftool_path = shutil.which("exiftool") or "/opt/homebrew/bin/exiftool"
    if os.path.exists(exiftool_path):
        report["exiftool_ok"] = True
    else:
        report["suggestions"].append("ExifTool is optional but recommended for preserving camera maker notes (Run: brew install exiftool).")

    return report


def print_diagnostic_report():
    """Print formatted diagnostic report to terminal."""
    rep = check_environment()

    print("\n=======================================================")
    print("        MEDIA OPTIMIZER - ENVIRONMENT DIAGNOSTICS      ")
    print("=======================================================")
    print(f"Platform:            macOS ({'Apple Silicon arm64' if rep['is_apple_silicon'] else 'Intel x86_64'})")
    print(f"Python Version:      {rep['python_version']} {'✅' if rep['python_ok'] else '❌'}")
    print(f"Pillow (Image Core): {'Installed ✅' if rep['pillow_ok'] else 'Missing ❌'}")
    print(f"Tkinter (Native GUI):{'Available ✅' if rep['tkinter_ok'] else 'Not Available ⚠️ (Web GUI fallback)'}")
    print(f"macOS sips (HEIC):   {'Available ✅' if rep['sips_ok'] else 'Missing ⚠️'}")
    print(f"FFmpeg (Video Core): {'Installed ✅' if rep['ffmpeg_ok'] else 'Missing ❌'}")
    if rep['ffmpeg_ok']:
        print(f"  └─ HEVC VideoToolbox: {'Hardware Accelerated ✅' if rep['hevc_vt_ok'] else 'Software Only ⚠️'}")
    print(f"ExifTool (Metadata): {'Installed ✅' if rep['exiftool_ok'] else 'Not Installed ⚠️ (basic timestamps only)'}")
    print("-------------------------------------------------------")

    if not rep["issues"] and not rep["suggestions"]:
        print("🎉 Everything is configured perfectly for maximum performance!\n")
    else:
        if rep["issues"]:
            print("🚨 Required Actions:")
            for iss in rep["issues"]:
                print(f"   • {iss}")
            print()
        if rep["suggestions"]:
            print("💡 Recommended Setup:")
            for sug in rep["suggestions"]:
                print(f"   • {sug}")
            print()
            print("   Quick Homebrew install:")
            print("   brew install ffmpeg exiftool && pip3 install -r requirements.txt\n")


def main():
    print_diagnostic_report()


if __name__ == "__main__":
    main()
