"""Metadata and timestamp preservation utilities."""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional


import sys
import ctypes

ATTR_BIT_MAP_COUNT = 5
ATTR_CMN_CRTIME = 0x00000200

class _AttrList(ctypes.Structure):
    _fields_ = [
        ("bitmapcount", ctypes.c_ushort),
        ("reserved", ctypes.c_ushort),
        ("commonattr", ctypes.c_uint),
        ("volattr", ctypes.c_uint),
        ("dirattr", ctypes.c_uint),
        ("fileattr", ctypes.c_uint),
        ("forkattr", ctypes.c_uint),
    ]

class _TimeSpec(ctypes.Structure):
    _fields_ = [
        ("tv_sec", ctypes.c_long),
        ("tv_nsec", ctypes.c_long),
    ]


def set_macos_birthtime(dst: Path, birthtime: float) -> bool:
    """Set the macOS file creation (birth) timestamp using native Darwin setattrlist."""
    if sys.platform != "darwin" or birthtime <= 0:
        return False
    try:
        libc = ctypes.CDLL(None)
        alist = _AttrList()
        alist.bitmapcount = ATTR_BIT_MAP_COUNT
        alist.commonattr = ATTR_CMN_CRTIME

        ts = _TimeSpec()
        ts.tv_sec = int(birthtime)
        ts.tv_nsec = int((birthtime - ts.tv_sec) * 1e9)

        ret = libc.setattrlist(
            str(dst).encode("utf-8"),
            ctypes.byref(alist),
            ctypes.byref(ts),
            ctypes.sizeof(ts),
            0,
        )
        return ret == 0
    except Exception:
        return False


def preserve_timestamps(src: Path, dst: Path) -> None:
    """Preserve modification, access, and macOS creation (birth) timestamps."""
    try:
        st = src.stat()
        os.utime(dst, (st.st_atime, st.st_mtime))
        # Preserve macOS creation/birth date if supported
        birthtime = getattr(st, "st_birthtime", None)
        if birthtime:
            set_macos_birthtime(dst, birthtime)
    except Exception:
        pass


import threading

class ExifToolDaemon:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if not cls._instance:
                cls._instance = super().__new__(cls)
                cls._instance._init_daemon(*args, **kwargs)
            return cls._instance

    def _init_daemon(self, executable_path: str):
        self.lock = threading.Lock()
        self.executable_path = executable_path
        self.proc = None
        self._start()

    def _start(self):
        try:
            self.proc = subprocess.Popen(
                [self.executable_path, "-stay_open", "True", "-@", "-"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=1,
                text=True
            )
        except Exception:
            self.proc = None

    def execute(self, *args) -> bool:
        with self.lock:
            if not self.proc or self.proc.poll() is not None:
                self._start()
                if not self.proc:
                    return False
            
            try:
                for arg in args:
                    self.proc.stdin.write(f"{arg}\n")
                self.proc.stdin.write("-execute\n")
                self.proc.stdin.flush()
                
                while True:
                    line = self.proc.stdout.readline()
                    if not line:
                        break
                    if line.strip() == "{ready}":
                        break
                return True
            except Exception:
                self.proc = None
                return False

    def shutdown(self):
        with self.lock:
            if self.proc and self.proc.poll() is None:
                try:
                    self.proc.stdin.write("-stay_open\nFalse\n")
                    self.proc.stdin.flush()
                    self.proc.wait(timeout=2)
                except Exception:
                    self.proc.kill()
                    self.proc.wait(timeout=1)
                finally:
                    if self.proc.stdin:
                        self.proc.stdin.close()
                    if self.proc.stdout:
                        self.proc.stdout.close()
                self.proc = None
import atexit

def shutdown_exiftool() -> None:
    """Safely terminate the background ExifTool daemon."""
    if ExifToolDaemon._instance:
        ExifToolDaemon._instance.shutdown()

atexit.register(shutdown_exiftool)

def copy_metadata(src: Path, dst: Path, exiftool_path: Optional[str] = None) -> bool:
    """Copy all EXIF, XMP, IPTC, and ColorSync metadata from source to destination.
    
    Uses exiftool in a persistent background daemon mode to copy complete camera maker notes, GPS,
    timestamps, and orientation tags instantly without subprocess boot overhead.
    """
    preserve_timestamps(src, dst)

    if not exiftool_path or not os.path.exists(exiftool_path):
        return False

    try:
        daemon = ExifToolDaemon(exiftool_path)
        success = daemon.execute(
            "-overwrite_original",
            "-TagsFromFile", str(src),
            "-all:all",
            "-unsafe",
            "-icc_profile",
            str(dst)
        )
        # Re-apply timestamps since exiftool may update file modification time
        preserve_timestamps(src, dst)
        return success
    except Exception:
        return False


import json


def validate_metadata(src: Path, dst: Path, exiftool_path: Optional[str] = None) -> dict:
    """Validate preserved metadata fields between source and destination.
    
    Checks critical metadata tags (Orientation, GPS, Date/Time, Camera Make/Model,
    Color Space, and HDR transfer characteristics) and returns preserved vs dropped fields.
    """
    CRITICAL_TAGS = [
        "Orientation",
        "DateTimeOriginal",
        "GPSLatitude",
        "GPSLongitude",
        "Make",
        "Model",
        "ColorSpace",
        "TransferCharacteristics",
    ]

    preserved = []
    dropped = []

    if exiftool_path and os.path.exists(exiftool_path):
        try:
            cmd = [exiftool_path, "-json", "-fast2"] + [f"-{t}" for t in CRITICAL_TAGS] + [str(src), str(dst)]
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0:
                data = json.loads(res.stdout)
                if len(data) >= 2:
                    src_meta, dst_meta = data[0], data[1]
                    for tag in CRITICAL_TAGS:
                        if tag in src_meta and src_meta[tag]:
                            if tag in dst_meta and dst_meta[tag] is not None:
                                preserved.append(tag)
                            else:
                                dropped.append(tag)
                    return {
                        "preserved": preserved,
                        "dropped": dropped,
                        "status": "valid" if not dropped else "partial",
                    }
        except Exception:
            pass

    # Pillow fallback validation for images
    try:
        from PIL import Image, ExifTags
        with Image.open(src) as s_img, Image.open(dst) as d_img:
            s_exif = s_img.getexif() if hasattr(s_img, "getexif") else {}
            d_exif = d_img.getexif() if hasattr(d_img, "getexif") else {}

            tag_names = {v: k for k, v in ExifTags.TAGS.items()}
            for name in ["Orientation", "DateTime", "Make", "Model"]:
                code = tag_names.get(name)
                if code and code in s_exif:
                    if code in d_exif:
                        preserved.append(name)
                    else:
                        dropped.append(name)
    except Exception:
        pass

    return {
        "preserved": preserved,
        "dropped": dropped,
        "status": "valid" if not dropped else "partial",
    }
