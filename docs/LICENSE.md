# 📄 License & Legal Notices

**Media Optimizer** is free and open-source software released under the **MIT License**.

---

## MIT License

```text
MIT License

Copyright (c) 2026 Raj Roshan

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## 📋 What Does This Mean for You?

The MIT License is one of the most permissive and developer-friendly open-source licenses.

| Permission / Condition | Status | Description |
| :--- | :---: | :--- |
| **Commercial Use** | ✅ Allowed | You can use Media Optimizer internally at work or within commercial workflows. |
| **Modification** | ✅ Allowed | You can modify, adapt, and rewrite any part of the codebase. |
| **Distribution** | ✅ Allowed | You can redistribute original or modified copies of the project. |
| **Private Use** | ✅ Allowed | You can run and adapt the software privately without publishing your changes. |
| **License & Copyright Notice** | ℹ️ Required | You must include the original copyright notice and permission notice in any copies or substantial portions of the software. |
| **Warranty & Liability** | ⚠️ Disclaimer | The software is provided "as is", without warranty of any kind. Authors are not liable for any damages or losses. |

---

## 🧩 Third-Party Software Acknowledgments

Media Optimizer stands on the shoulders of exceptional open-source libraries and utilities. The project interfaces with or builds upon the following third-party components:

### 1. FFmpeg
- **Role**: Powers video analysis and hardware-accelerated transcoding (`hevc_videotoolbox` and `h264_videotoolbox`) on macOS.
- **License**: GNU Lesser General Public License (LGPL) v2.1+ or GNU General Public License (GPL) v2+ (depending on how your system binary was compiled).
- **Home**: [ffmpeg.org](https://ffmpeg.org)
- *Note: Media Optimizer communicates with FFmpeg via standard command-line process interfaces.*

### 2. ExifTool (by Phil Harvey)
- **Role**: Powers lossless extraction and injection of camera EXIF, GPS coordinates, color profiles, and filesystem creation/modification timestamps.
- **License**: Perl Artistic License and GNU General Public License (GPL).
- **Home**: [exiftool.org](https://exiftool.org)
- *Note: Media Optimizer interacts with ExifTool as an external command-line process.*

### 3. Pillow
- **Role**: High-performance image decoding, perceptual analysis, downsampling, and JPEG re-encoding.
- **License**: Historical Permission Notice and Disclaimer (HPND License).
- **Home**: [python-pillow.org](https://python-pillow.org)

### 4. pillow-heif (Optional)
- **Role**: Provides native HEIC/AVIF container reading capabilities when installed.
- **License**: GNU Lesser General Public License (LGPL) v3.
- **Home**: [github.com/bigcat88/pillow_heif](https://github.com/bigcat88/pillow_heif)

---

## 🔒 Privacy & Data Guarantee

- **100% Local Execution**: All compression, analysis, and metadata transfers occur on your local Mac hardware.
- **Zero Telemetry**: Media Optimizer contains no analytics trackers, crash reporters, pingbacks, or background network calls.
- **Read-Only Ingestion**: Original source media is strictly opened read-only and is never overwritten or deleted.
