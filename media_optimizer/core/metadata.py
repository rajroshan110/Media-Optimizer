"""Metadata and timestamp preservation utilities."""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional


def preserve_timestamps(src: Path, dst: Path) -> None:
    """Preserve modification time and access time from source to destination."""
    try:
        st = src.stat()
        os.utime(dst, (st.st_atime, st.st_mtime))
    except Exception:
        pass


def copy_metadata(src: Path, dst: Path, exiftool_path: Optional[str] = None) -> bool:
    """Copy all EXIF, XMP, IPTC, and ColorSync metadata from source to destination.
    
    Uses exiftool when available to preserve complete camera maker notes, GPS,
    timestamps, and orientation tags.
    """
    preserve_timestamps(src, dst)

    if not exiftool_path or not os.path.exists(exiftool_path):
        return False

    try:
        # ExifTool flags:
        # -overwrite_original: don't create _original backup file
        # -TagsFromFile <src>: copy all tags
        # -all:all: copy all tags including maker notes
        # -unsafe: copy tags marked unsafe (like orientation, color space)
        # -icc_profile: copy embedded color profiles
        cmd = [
            exiftool_path,
            "-q", "-q",  # quiet
            "-overwrite_original",
            "-TagsFromFile", str(src),
            "-all:all",
            "-unsafe",
            "-icc_profile",
            str(dst),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        # Re-apply timestamps since exiftool may update file modification time
        preserve_timestamps(src, dst)
        return res.returncode == 0
    except Exception:
        return False
