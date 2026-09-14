<div align="center">

# ⚡️ Media Optimizer

### High-Speed Local Media Compression Engine for macOS (Apple Silicon)

[![Platform: macOS](https://img.shields.io/badge/Platform-macOS%20(Apple%20Silicon)-000000?style=for-the-badge&logo=apple&logoColor=white)](https://apple.com)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Hardware: VideoToolbox](https://img.shields.io/badge/Hardware%20Accel-VideoToolbox%20(HEVC)-orange?style=for-the-badge)](https://developer.apple.com/documentation/videotoolbox)
[![Offline: 100%](https://img.shields.io/badge/Privacy-100%25%20Offline%20%26%20Local-success?style=for-the-badge)](README.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue?style=for-the-badge)](LICENSE)

<p align="center">
  <b>Reclaim up to 85% disk and cloud storage without perceptual quality loss.</b><br>
  No cloud uploads. No privacy trade-offs. No manual bitrate guesswork.
</p>

</div>

---

## 💡 Why Media Optimizer?

To save cloud storage space, many users resort to **sending photos and videos to themselves on WhatsApp**, downloading the compressed copies, and uploading those to the cloud.

Why? Because WhatsApp's compression algorithm finds the **perceptual sweet spot**: it reduces file sizes by **70% to 90%** while keeping the visual quality sharp on phones and laptops.

However, that workflow has serious drawbacks:
- ❌ **Privacy Compromise**: Your personal family photos and private videos are uploaded to third-party servers.
- ❌ **Metadata Loss**: Capture dates, camera EXIF, and GPS locations are stripped.
- ❌ **Tedious Manual Labor**: Sending, downloading, and renaming dozens of files by hand is exhausting.

**Media Optimizer brings that exact efficiency to your local Mac.**
Using Apple Silicon's hardware video encoders (`hevc_videotoolbox`), smart image downsampling, and an intelligent decision engine, it compresses large media libraries at blazing speed—**100% locally and privately**.

---

## ✨ Features

- 🚀 **Apple Silicon Hardware Acceleration**
  Leverages Apple's dedicated media engine via macOS `hevc_videotoolbox` and `h264_videotoolbox`. Video transcoding is hardware-accelerated with minimal CPU overhead, preserving battery life and system responsiveness.

- 🧠 **Smart Decision Engine**
  No manual bitrate or resolution sliders needed. The engine inspects dimensions, format, bits per pixel (BPP), entropy, and codec:
  - **Over-bloated 4K/60fps videos & raw camera photos**: Intelligently resized and re-encoded using high-efficiency HEVC/JPEG.
  - **Already efficient media**: Small photos (<350 KB) or modern low-bitrate HEVC videos are preserved as-is to avoid generational quality degradation.

- 🛡️ **Zero Overwrites & Configurable Collision Handling**
  - Original files are **never modified, replaced, or deleted** under any circumstance.
  - When saving into the same folder, outputs automatically append `_optimized` (e.g., `IMG_0042.jpg` → `IMG_0042_optimized.jpg`).
  - **Flexible Collision Strategy**: Choose between non-destructive numeric suffixing (`photo_1.jpg`, `photo_2.jpg`) to keep multiple compression experiments, or clean in-place overwriting of previously generated optimized files.
  - If a file cannot be made smaller with meaningful savings (minimum 5%), the original is preserved.

- ⚙️ **Interactive Settings & Built-In Tooltips**
  - Fine-tune optimization parameters right from the native desktop GUI or local web interface.
  - Helpful `ⓘ` hover tooltips explain the performance and perceptual trade-offs of each option.
  - Features a **Reset to Defaults** button to instantly restore the WhatsApp perceptual sweet-spot settings.
  - Automatically persists user configurations to `~/.media_optimizer.json`.

- 📍 **Full Metadata & Timestamp Preservation**
  Transfers EXIF metadata, camera settings, color profiles, GPS location data, and file modification/creation timestamps via ExifTool.

- ⏱️ **Fault-Tolerant & Config-Aware Resumability**
  Backed by a WAL-mode SQLite journal. Resuming an interrupted batch skips already-completed files. If you adjust your compression settings (e.g., quality factor or resolution), the engine recognizes the change via config hashing and safely re-processes your media.

- 🎨 **Three Interfaces for Every Workflow**
  1. **macOS Native Aqua GUI**: Clean desktop interface with real-time dark activity log, single/multi-file selection, settings modal, and space-saving stats.
  2. **Browser-Based Local Web GUI**: Zero-dependency web interface running locally on `localhost:8484` with instant CSS tooltips and live progress metrics.
  3. **Terminal CLI**: Scriptable command-line interface with `--dry-run`, custom worker concurrency, and batch reports.

- 📊 **WhatsApp Benchmark Suite**
  Includes a perceptual quality validation suite measuring SSIM (Structural Similarity Index), PSNR, and bitrate reduction side-by-side against WhatsApp.

---

## 📊 Comparison: Media Optimizer vs. WhatsApp

| Feature | Media Optimizer | WhatsApp Self-Chat |
| :--- | :---: | :---: |
| **Privacy** | 🔒 **100% Local & Offline** (Zero Network) | ⚠️ Uploaded to Meta Cloud |
| **Speed** | ⚡️ **Hardware M-Series VideoToolbox** | ⏳ Limited by Internet Upload/Download |
| **Batch Processing** | 📁 **Full Folder Trees & Multi-Select** | 🖐 Manual click per file |
| **EXIF & GPS Metadata** | ✅ **Preserved** | ❌ Stripped |
| **File Timestamps** | ✅ **Preserved** (Creation & Modified) | ❌ Set to download time |
| **Perceptual Quality** | 🎯 **High (Tuned SSIM > 0.94)** | 🎯 High (Compressed) |
| **Storage Savings** | 📉 **70% – 88% average reduction** | 📉 70% – 85% average reduction |

---

## 🛠 Prerequisites

Media Optimizer runs natively on **macOS Monterey (12.0) or later** (optimized for Apple Silicon M1/M2/M3/M4).

### 1. Install Dependencies via Homebrew
```bash
brew install ffmpeg exiftool python@3.14
```

### 2. Install Python Packages
```bash
pip3 install -r requirements.txt
```
*(Only `Pillow` is required. All other components use Python standard library and macOS native frameworks).*

---

## 🚀 Quick Start

### Option 1: Native macOS GUI
Launch directly using the native runner:
```bash
./run.sh --gui
```
> Or generate a standalone native macOS `.app` bundle:
> ```bash
> ./scripts/create_app_bundle.sh
> open "Media Optimizer.app"
> ```

### Option 2: Local Web GUI
Open in any modern browser:
```bash
./run.sh --web
```
Access the interface at `http://localhost:8484`.

### Option 3: Terminal CLI

```bash
# Optimize a single video or photo (saves as <filename>_optimized.<ext>)
./run.sh "/Users/username/Downloads/drone_shot.mp4"

# Optimize an entire directory (replicates directory structure into <folder>_optimized)
./run.sh "/Users/username/Pictures/Vacation2026"

# Preview decisions without changing anything (Dry-Run mode)
./run.sh "/Users/username/Pictures/Vacation2026" --dry-run

# Specify a custom destination folder
./run.sh "/path/to/media" -o "/path/to/backup"

# Custom concurrency tuning
./run.sh "/path/to/media" --image-workers 8 --video-workers 2
```

---

## ⚙️ Configuration & Settings

Both the Desktop GUI and Web GUI feature an interactive settings modal with instant tooltips. Settings are saved to `~/.media_optimizer.json`:

| Setting | Default | Description |
| :--- | :---: | :--- |
| **Convert HEIC to JPEG** | `Enabled` | Converts Apple HEIC photos to standard JPEG for cross-platform compatibility. Uncheck to preserve HEIC. |
| **Overwrite Existing Files** | `Disabled` | When disabled, collisions append a numeric suffix (`_1`, `_2`) so both files are kept. When enabled, updates previous outputs cleanly. Originals are always protected. |
| **Preserve Metadata** | `Enabled` | Retains full camera EXIF, GPS location, color profiles, and timestamps via ExifTool. |
| **Image Quality** | `80` | Perceptual sweet-spot quality factor (1–100). WhatsApp uses 78–80 for maximum space saving with sharp detail. |
| **Max Image Dimension** | `2048 px` | Downscales camera photos exceeding this dimension on their longest edge (matches WhatsApp HD). |
| **Max Video Height** | `1080 px` | Downscales 4K / UHD videos to 1080p, reducing file size by up to 85% with sharp visual fidelity. |
| **Max Video FPS** | `30 fps` | Caps high-framerate (60fps) clips to 30fps to halve encoding overhead while maintaining smooth motion. |

---

## 🏗 System Architecture

```
                                  Source Media
                     (Individual Files or Folder Hierarchies)
                                        │
                                        ▼
                         ┌─────────────────────────────┐
                         │   File Discovery & Queue    │
                         │   (SQLite Resume Journal)   │
                         └──────────────┬──────────────┘
                                        │
                                        ▼
                         ┌─────────────────────────────┐
                         │  Automatic Decision Engine  │
                         │  (Format, BPP, Dimensions,  │
                         │   Entropy, Codec, Bitrate)  │
                         └──────┬───────────────┬──────┘
                                │               │
                    [Image Path]│               │[Video Path]
                                ▼               ▼
             ┌─────────────────────────┐ ┌─────────────────────────┐
             │ Image Optimizer         │ │ Video Optimizer         │
             │ • Smart Lanczos Resize  │ │ • Apple VideoToolbox    │
             │ • Optimized JPEG / PNG  │ │ • Spatial Quantization  │
             │ • Quality / Size Check  │ │ • AAC Audio + FastStart │
             └────────────┬────────────┘ └────────────┬────────────┘
                          │                           │
                          └─────────────┬─────────────┘
                                        │
                                        ▼
                         ┌─────────────────────────────┐
                         │  Safety & Verification Gate │
                         │  • Guaranteed Non-Overwrite │
                         │  • Transfer EXIF & Dates    │
                         │  • Size Threshold Check     │
                         └──────────────┬──────────────┘
                                        │
                                        ▼
                        Destination (Optimized Library)
```

---

## 🧪 Running Tests & Benchmarks

### Unit Test Suite
The project contains unit tests covering the decision engine, pipeline, overwrite safety, hardware acceleration, and UI states:
```bash
python3 -m unittest discover tests
```

### Comparative Benchmark
Run the benchmark script to generate synthetic test media, optimize it, and view detailed compression metrics:
```bash
python3 scripts/run_benchmark.py
```

---

## 🔒 Privacy Commitment

- **Zero Network Activity**: Media Optimizer makes zero outbound network requests.
- **No Analytics / Telemetry**: No tracking identifiers, analytics SDKs, or third-party reporting.
- **Read-Only Source Guarantee**: Original files are opened strictly in read mode and are never modified in place.

---

## 📄 License

Distributed under the **MIT License**. See `LICENSE` for more information.
