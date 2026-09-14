"""Zero-dependency local Web GUI fallback for Media Optimizer.

Provides a modern macOS-inspired browser interface when Tkinter is not installed
or when requested via --web. Uses only Python standard library.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

from media_optimizer.config import get_default_config
from media_optimizer.core.journal import BatchSummary
from media_optimizer.pipeline import OptimizationPipeline


from media_optimizer.utils import format_bytes


class OptimizerState:
    """Thread-safe state container for active optimization run."""

    def __init__(self):
        self.lock = threading.Lock()
        self.running: bool = False
        self.pipeline: Optional[OptimizationPipeline] = None
        self.current: int = 0
        self.total: int = 0
        self.current_file: str = "Idle"
        self.orig_bytes: int = 0
        self.opt_bytes: int = 0
        self.saved_bytes: int = 0
        self.reduction_percent: float = 0.0
        self.logs: List[Dict[str, Any]] = []
        self.error: Optional[str] = None
        self.summary: Optional[str] = None
        self.completed: bool = False

    def reset(self):
        with self.lock:
            self.running = True
            self.current = 0
            self.total = 0
            self.current_file = "Starting..."
            self.orig_bytes = 0
            self.opt_bytes = 0
            self.saved_bytes = 0
            self.reduction_percent = 0.0
            self.logs = []
            self.error = None
            self.summary = None
            self.completed = False

    def add_log(self, level: str, msg: str, rel_path: str = "", orig_sz: int = 0, opt_sz: int = 0):
        with self.lock:
            now_str = time.strftime("%H:%M:%S")
            self.logs.append({
                "time": now_str,
                "level": level,
                "rel_path": rel_path,
                "orig_sz": orig_sz,
                "opt_sz": opt_sz,
                "msg": msg,
            })
            if len(self.logs) > 300:
                self.logs = self.logs[-300:]

    def to_dict(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "running": self.running,
                "current": self.current,
                "total": self.total,
                "current_file": self.current_file,
                "orig_bytes": self.orig_bytes,
                "opt_bytes": self.opt_bytes,
                "saved_bytes": self.saved_bytes,
                "orig_formatted": format_bytes(self.orig_bytes),
                "opt_formatted": format_bytes(self.opt_bytes),
                "saved_formatted": format_bytes(self.saved_bytes),
                "reduction_percent": round(self.reduction_percent, 1),
                "logs": self.logs[-100:],  # Return latest 100 log entries
                "error": self.error,
                "summary": self.summary,
                "completed": self.completed,
            }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Media Optimizer</title>
<style>
  :root {
    --bg: #f5f5f7;
    --card: #ffffff;
    --text: #1d1d1f;
    --subtext: #86868b;
    --accent: #0071e3;
    --accent-hover: #0077ed;
    --accent-active: #0062c4;
    --border: rgba(0,0,0,0.08);
    --success: #34c759;
    --danger: #ff3b30;
    --log-bg: #fbfbfd;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #1c1c1e;
      --card: #2c2c2e;
      --text: #f5f5f7;
      --subtext: #a1a1a6;
      --accent: #0a84ff;
      --accent-hover: #409cff;
      --accent-active: #0060df;
      --border: rgba(255,255,255,0.12);
      --success: #30d158;
      --danger: #ff453a;
      --log-bg: #1e1e20;
    }
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", Helvetica, Arial, sans-serif;
    background: var(--bg);
    color: var(--text);
    padding: 30px 20px;
    display: flex;
    justify-content: center;
    min-height: 100vh;
  }
  .container {
    width: 100%;
    max-width: 740px;
    display: flex;
    flex-direction: column;
    gap: 20px;
  }
  .header {
    text-align: center;
    padding: 10px 0;
  }
  .header h1 {
    font-size: 26px;
    font-weight: 700;
    letter-spacing: -0.5px;
  }
  .header p {
    font-size: 13px;
    color: var(--subtext);
    margin-top: 4px;
  }
  .card {
    background: var(--card);
    border-radius: 14px;
    border: 1px solid var(--border);
    padding: 20px;
    box-shadow: 0 4px 20px rgba(0,0,0,0.04);
  }
  .card-title {
    font-size: 13px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.6px;
    color: var(--subtext);
    margin-bottom: 14px;
  }
  .form-group {
    margin-bottom: 14px;
  }
  .form-group:last-child { margin-bottom: 0; }
  .form-group label {
    display: block;
    font-size: 13px;
    font-weight: 500;
    margin-bottom: 6px;
  }
  .input-row {
    display: flex;
    gap: 8px;
  }
  .input-row input {
    flex: 1;
    padding: 10px 12px;
    border-radius: 8px;
    border: 1px solid var(--border);
    background: var(--bg);
    color: var(--text);
    font-size: 13px;
    outline: none;
    font-family: inherit;
  }
  .input-row input:focus {
    border-color: var(--accent);
  }
  button {
    cursor: pointer;
    font-family: inherit;
    font-size: 13px;
    font-weight: 500;
    border-radius: 8px;
    border: none;
    padding: 10px 16px;
    transition: all 0.15s ease;
  }
  .btn-secondary {
    background: var(--bg);
    color: var(--text);
    border: 1px solid var(--border);
  }
  .btn-secondary:hover { background: rgba(128,128,128,0.15); }
  .btn-primary {
    background: var(--accent);
    color: #ffffff;
    font-weight: 600;
    font-size: 14px;
    padding: 12px 24px;
    width: 100%;
  }
  .btn-primary:hover { background: var(--accent-hover); }
  .btn-primary:active { background: var(--accent-active); }
  .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
  .btn-danger {
    background: var(--danger);
    color: #fff;
    padding: 12px 20px;
  }
  .btn-danger:disabled { opacity: 0.5; cursor: not-allowed; }
  .button-group {
    display: flex;
    gap: 10px;
    margin-top: 6px;
  }
  .stats-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 12px;
    margin-top: 14px;
  }
  .stat-box {
    background: var(--bg);
    padding: 12px;
    border-radius: 10px;
    text-align: center;
  }
  .stat-label {
    font-size: 11px;
    color: var(--subtext);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 4px;
  }
  .stat-value {
    font-size: 16px;
    font-weight: 700;
  }
  .stat-value.saved {
    color: var(--success);
  }
  .progress-wrap {
    margin-top: 14px;
  }
  .progress-bar-bg {
    width: 100%;
    height: 8px;
    background: var(--bg);
    border-radius: 4px;
    overflow: hidden;
    margin-bottom: 8px;
  }
  .progress-bar-fill {
    height: 100%;
    width: 0%;
    background: var(--accent);
    border-radius: 4px;
    transition: width 0.25s ease;
  }
  .progress-meta {
    display: flex;
    justify-content: space-between;
    font-size: 12px;
    color: var(--subtext);
  }
  .log-container {
    background: #000000;
    color: #ffffff;
    border-radius: 10px;
    border: 1px solid rgba(255, 255, 255, 0.15);
    padding: 14px;
    font-family: "SF Mono", Menlo, Monaco, Consolas, monospace;
    font-size: 11px;
    max-height: 240px;
    overflow-y: auto;
    white-space: pre-wrap;
    word-break: break-word;
    display: flex;
    flex-direction: column;
    gap: 6px;
    box-shadow: inset 0 2px 8px rgba(0, 0, 0, 0.6);
  }
  .log-container::-webkit-scrollbar {
    width: 6px;
  }
  .log-container::-webkit-scrollbar-track {
    background: #000000;
  }
  .log-container::-webkit-scrollbar-thumb {
    background: #27272a;
    border-radius: 3px;
  }
  .log-container::-webkit-scrollbar-thumb:hover {
    background: #3f3f46;
  }
  .log-item {
    line-height: 1.45;
    color: #ffffff;
  }
  .log-item strong {
    color: #ffffff;
  }
  .log-msg {
    color: #ffffff;
  }
  .badge {
    display: inline-block;
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 10px;
    font-weight: 700;
    margin-right: 6px;
    text-transform: uppercase;
    letter-spacing: 0.3px;
  }
  .badge-opt {
    background: rgba(34, 197, 94, 0.2);
    color: #4ade80;
    border: 1px solid rgba(74, 222, 128, 0.35);
  }
  .badge-copy {
    background: rgba(148, 163, 184, 0.16);
    color: #cbd5e1;
    border: 1px solid rgba(148, 163, 184, 0.3);
  }
  .badge-fail {
    background: rgba(244, 63, 94, 0.25);
    color: #fb7185;
    border: 1px solid rgba(251, 113, 133, 0.45);
  }
  .badge-start {
    background: rgba(245, 158, 11, 0.22);
    color: #fbbf24;
    border: 1px solid rgba(251, 191, 36, 0.45);
  }
  .badge-info {
    background: rgba(56, 189, 248, 0.2);
    color: #38bdf8;
    border: 1px solid rgba(56, 189, 248, 0.4);
  }
  .badge-complete {
    background: rgba(168, 85, 247, 0.24);
    color: #c084fc;
    border: 1px solid rgba(192, 132, 252, 0.45);
  }
  .log-time {
    color: #888888;
    margin-right: 6px;
    font-size: 10px;
    font-weight: 500;
  }
  .summary-banner {
    background: rgba(52, 199, 89, 0.12);
    border: 1px solid var(--success);
    border-radius: 10px;
    padding: 14px;
    margin-top: 14px;
    font-size: 13px;
    display: none;
  }
  
  /* Settings Modal */
  .modal-overlay {
    position: fixed; top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(0,0,0,0.5); display: none;
    align-items: center; justify-content: center; z-index: 1000;
  }
  .modal-content {
    background: var(--card); border-radius: 14px;
    width: 90%; max-width: 500px; padding: 24px;
    border: 1px solid var(--border); box-shadow: 0 10px 30px rgba(0,0,0,0.3);
  }
  .modal-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
  .modal-title { font-size: 18px; font-weight: 600; }
  .close-btn { background: none; border: none; color: var(--subtext); font-size: 20px; cursor: pointer; }
  .setting-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; font-size: 14px; }
  .setting-input { background: var(--bg); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 6px 10px; width: 100px; text-align: right; }
  .setting-checkbox { width: 18px; height: 18px; }
  span[title] { cursor: help; opacity: 0.8; }
  span[title]:hover { opacity: 1; }
  
</style>
</head>
<body>

<div id="settingsModal" class="modal-overlay">
  <div class="modal-content">
    <div class="modal-header">
      <div class="modal-title">Optimization Settings</div>
      <button class="close-btn" onclick="closeSettings()">&times;</button>
    </div>
    <div class="setting-row">
      <label>Convert HEIC to JPEG <span title="Converts Apple HEIC photos to standard JPEG for universal compatibility. Uncheck to keep original format.">ⓘ</span></label>
      <input type="checkbox" id="cfg-heic" class="setting-checkbox">
    </div>
    <div class="setting-row">
      <label>Preserve Metadata <span title="Keeps hidden data like date taken and GPS location. Uncheck to strip data and save a few kilobytes.">ⓘ</span></label>
      <input type="checkbox" id="cfg-meta" class="setting-checkbox">
    </div>
    <div class="setting-row">
      <label>Image Quality (1-100) <span title="Compression level. 80 is the WhatsApp sweet spot. Lower = smaller file but blurrier.">ⓘ</span></label>
      <input type="number" id="cfg-img-q" class="setting-input" min="1" max="100">
    </div>
    <div class="setting-row">
      <label>Max Image Dimension <span title="Resizes huge photos down to this size on their longest edge. 2048px is WhatsApp HD quality.">ⓘ</span></label>
      <input type="number" id="cfg-img-max" class="setting-input">
    </div>
    <div class="setting-row">
      <label>Max Video Height <span title="Resizes 4K/UHD videos down to this height (e.g., 1080 for 1080p). Saves massive space.">ⓘ</span></label>
      <input type="number" id="cfg-vid-h" class="setting-input">
    </div>
    <div class="setting-row">
      <label>Max Video FPS <span title="Drops 60fps video down to 30fps. 30fps cuts file size in half with normal motion.">ⓘ</span></label>
      <input type="number" id="cfg-vid-fps" class="setting-input">
    </div>
    <div style="margin-top: 20px; display: flex; gap: 10px;">
      <button class="btn-primary" onclick="saveSettings()">Save Settings</button>
      <button class="btn-secondary" onclick="resetSettings()">Reset</button>
    </div>
  </div>
</div>

<div class="container">
  <div class="header">
    <h1>Media Optimizer</h1>
    <p>macOS Apple Silicon Hardware Accelerated | Smart Adaptive Quality</p>
    <button class="btn-secondary" style="margin-top: 10px; font-size: 12px; padding: 6px 12px;" onclick="openSettings()">⚙️ Settings</button>
  </div>

  <div class="card">
    <div class="card-title">Select Source & Destination</div>
    <div class="form-group">
      <label>Input (File or Folder)</label>
      <div class="input-row">
        <input type="text" id="inputFolder" placeholder="/path/to/media/file-or-folder" value="__INITIAL_INPUT__">
        <button class="btn-secondary" onclick="browseSource('file')">File...</button>
        <button class="btn-secondary" onclick="browseSource('folder')">Folder...</button>
      </div>
    </div>
    <div class="form-group">
      <label>Output Folder</label>
      <div class="input-row">
        <input type="text" id="outputFolder" placeholder="/path/to/optimized/folder" value="__INITIAL_OUTPUT__">
        <button class="btn-secondary" onclick="browseSource('output')">Browse...</button>
      </div>
    </div>

    <div class="button-group">
      <button class="btn-primary" id="startBtn" onclick="startOptimization()">OPTIMIZE MEDIA</button>
      <button class="btn-danger" id="stopBtn" onclick="stopOptimization()" disabled>Stop</button>
    </div>
  </div>

  <div class="card">
    <div class="card-title">Progress & Savings</div>
    
    <div class="progress-wrap">
      <div class="progress-bar-bg">
        <div class="progress-bar-fill" id="progressFill"></div>
      </div>
      <div class="progress-meta">
        <span id="currentStatus">Ready</span>
        <span id="progressPct">0%</span>
      </div>
    </div>

    <div class="stats-grid">
      <div class="stat-box">
        <div class="stat-label">Original</div>
        <div class="stat-value" id="statOrig">0 B</div>
      </div>
      <div class="stat-box">
        <div class="stat-label">Optimized</div>
        <div class="stat-value" id="statOpt">0 B</div>
      </div>
      <div class="stat-box">
        <div class="stat-label">Saved</div>
        <div class="stat-value saved" id="statSaved">0 B (0.0%)</div>
      </div>
    </div>

    <div class="summary-banner" id="summaryBanner"></div>
  </div>

  <div class="card">
    <div class="card-title">Activity Log</div>
    <div class="log-container" id="logContainer">
      <div class="log-item" style="color: var(--subtext);">Waiting to start...</div>
    </div>
  </div>
</div>

<script>
let pollingInterval = null;

function updateDefaultOutput() {
  const inp = document.getElementById("inputFolder").value.trim();
  const out = document.getElementById("outputFolder");
  if (inp && (!out.value || out.value.endsWith("_optimized") || out.dataset.auto === "true")) {
    const cleaned = inp.replace(/[/]+$/, "");
    const parts = cleaned.split("/");
    const last = parts[parts.length - 1];
    if (last && last.includes(".")) {
      parts.pop();
      out.value = parts.join("/") || "/";
    } else {
      out.value = cleaned + "_optimized";
    }
    out.dataset.auto = "true";
  }
}
document.getElementById("inputFolder").addEventListener("input", updateDefaultOutput);
document.getElementById("outputFolder").addEventListener("input", () => {
  document.getElementById("outputFolder").dataset.auto = "false";
});

async function browseSource(type) {
  try {
    const res = await fetch("/api/browse", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type })
    });
    const data = await res.json();
    if (data.path) {
      if (type === "output") {
        document.getElementById("outputFolder").value = data.path;
        document.getElementById("outputFolder").dataset.auto = "false";
      } else {
        document.getElementById("inputFolder").value = data.path;
        document.getElementById("outputFolder").dataset.auto = "true";
        updateDefaultOutput();
      }
    }
  } catch (err) {
    console.error("Browse error:", err);
  }
}
const browseFolder = browseSource;

async function startOptimization() {
  const inp = document.getElementById("inputFolder").value.trim();
  const out = document.getElementById("outputFolder").value.trim();

  if (!inp) {
    alert("Please select or enter an input folder.");
    return;
  }
  if (!out) {
    alert("Please specify an output folder.");
    return;
  }

  document.getElementById("startBtn").disabled = true;
  document.getElementById("stopBtn").disabled = false;
  document.getElementById("summaryBanner").style.display = "none";
  document.getElementById("logContainer").innerHTML = '<div class="log-item">Starting batch optimization...</div>';

  try {
    const res = await fetch("/api/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ input_dir: inp, output_dir: out })
    });
    const data = await res.json();
    if (data.error) {
      alert("Error: " + data.error);
      document.getElementById("startBtn").disabled = false;
      document.getElementById("stopBtn").disabled = true;
      return;
    }
    startPolling();
  } catch (err) {
    alert("Failed to start: " + err);
    document.getElementById("startBtn").disabled = false;
    document.getElementById("stopBtn").disabled = true;
  }
}

async function stopOptimization() {
  document.getElementById("stopBtn").disabled = true;
  await fetch("/api/stop", { method: "POST" });
}

async function openSettings() {
  try {
    const res = await fetch("/api/config");
    const cfg = await res.json();
    document.getElementById("cfg-heic").checked = cfg.convert_heic_to_jpeg;
    document.getElementById("cfg-meta").checked = cfg.preserve_metadata;
    document.getElementById("cfg-img-q").value = cfg.jpeg_quality;
    document.getElementById("cfg-img-max").value = cfg.image_max_dimension;
    document.getElementById("cfg-vid-h").value = cfg.video_max_height;
    document.getElementById("cfg-vid-fps").value = cfg.video_max_fps;
    document.getElementById("settingsModal").style.display = "flex";
  } catch(e) {
    alert("Could not load settings.");
  }
}

function closeSettings() {
  document.getElementById("settingsModal").style.display = "none";
}

function resetSettings() {
  document.getElementById("cfg-heic").checked = true;
  document.getElementById("cfg-meta").checked = true;
  document.getElementById("cfg-img-q").value = 80;
  document.getElementById("cfg-img-max").value = 2048;
  document.getElementById("cfg-vid-h").value = 1080;
  document.getElementById("cfg-vid-fps").value = 30;
}

async function saveSettings() {
  const payload = {
    convert_heic_to_jpeg: document.getElementById("cfg-heic").checked,
    preserve_metadata: document.getElementById("cfg-meta").checked,
    jpeg_quality: parseInt(document.getElementById("cfg-img-q").value) || 80,
    image_max_dimension: parseInt(document.getElementById("cfg-img-max").value) || 2048,
    video_max_height: parseInt(document.getElementById("cfg-vid-h").value) || 1080,
    video_max_fps: parseInt(document.getElementById("cfg-vid-fps").value) || 30
  };
  try {
    await fetch("/api/config", {
      method: "POST",
      body: JSON.stringify(payload),
      headers: { "Content-Type": "application/json" }
    });
    closeSettings();
  } catch(e) {
    alert("Could not save settings.");
  }
}

function startPolling() {
  if (pollingInterval) clearInterval(pollingInterval);
  pollingInterval = setInterval(pollStatus, 400);
}

async function pollStatus() {
  try {
    const res = await fetch("/api/status");
    const data = await res.json();

    const pct = data.total > 0 ? ((data.current / data.total) * 100).toFixed(1) : 0;
    document.getElementById("progressFill").style.width = pct + "%";
    document.getElementById("progressPct").innerText = `${data.current} / ${data.total} (${pct}%)`;
    document.getElementById("currentStatus").innerText = data.current_file ? "Processing: " + data.current_file : "Running";

    document.getElementById("statOrig").innerText = data.orig_formatted;
    document.getElementById("statOpt").innerText = data.opt_formatted;
    document.getElementById("statSaved").innerText = `${data.saved_formatted} (${data.reduction_percent}%)`;

    // Render logs
    if (data.logs && data.logs.length > 0) {
      const container = document.getElementById("logContainer");
      const isNearBottom = (container.scrollHeight - container.clientHeight - container.scrollTop) <= 80;

      container.innerHTML = data.logs.map(l => {
        let level = (l.level || "info").toLowerCase();
        let badgeClass = "badge-info";
        let badgeText = "INFO";

        if (level === "start" || level === "running") {
          badgeClass = "badge-start";
          badgeText = "RUNNING";
        } else if (level === "saved" || level === "opt" || (l.msg && l.msg.includes("Optimized"))) {
          badgeClass = "badge-opt";
          badgeText = "SAVED";
        } else if (level === "copied" || level === "copy" || (l.msg && l.msg.includes("Preserved"))) {
          badgeClass = "badge-copy";
          badgeText = "COPIED";
        } else if (level === "error" || level === "fail" || (l.msg && (l.msg.includes("Failed") || l.msg.includes("Error")))) {
          badgeClass = "badge-fail";
          badgeText = "ERROR";
        } else if (level === "complete") {
          badgeClass = "badge-complete";
          badgeText = "DONE";
        }

        const timeStr = l.time ? `<span class="log-time">[${escapeHtml(l.time)}]</span>` : "";
        const badge = `<span class="badge ${badgeClass}">${badgeText}</span>`;
        const pathPart = l.rel_path ? `<strong>${escapeHtml(l.rel_path)}</strong>: ` : "";
        return `<div class="log-item">${timeStr}${badge}${pathPart}<span class="log-msg">${escapeHtml(l.msg)}</span></div>`;
      }).join("");

      if (isNearBottom) {
        container.scrollTop = container.scrollHeight;
      }
    }

    if (!data.running) {
      if (pollingInterval) {
        clearInterval(pollingInterval);
        pollingInterval = null;
      }
      document.getElementById("startBtn").disabled = false;
      document.getElementById("stopBtn").disabled = true;

      const banner = document.getElementById("summaryBanner");
      if (data.error) {
        document.getElementById("currentStatus").innerText = "Failed";
        banner.style.display = "block";
        banner.style.borderColor = "var(--danger)";
        banner.style.background = "rgba(255, 59, 48, 0.12)";
        banner.innerHTML = `<strong>Optimization Failed:</strong> ${escapeHtml(data.error)}`;
      } else if (data.completed) {
        document.getElementById("currentStatus").innerText = "Completed";
        if (data.summary) {
          banner.style.display = "block";
          banner.style.borderColor = "var(--success)";
          banner.style.background = "rgba(52, 199, 89, 0.12)";
          banner.innerHTML = `<strong>Batch Finished!</strong> Space saved: <strong>${data.saved_formatted}</strong> (${data.reduction_percent}% reduction).`;
        }
      } else {
        document.getElementById("currentStatus").innerText = "Stopped";
      }
    }
  } catch (err) {
    console.error("Poll error:", err);
  }
}

function escapeHtml(text) {
  if (text === null || text === undefined) return "";
  const div = document.createElement("div");
  div.innerText = String(text);
  return div.innerHTML;
}

// Check status immediately on page load and resume polling if active
window.addEventListener("DOMContentLoaded", () => {
  pollStatus().then(() => {
    fetch("/api/status").then(r => r.json()).then(data => {
      if (data.running) {
        document.getElementById("startBtn").disabled = true;
        document.getElementById("stopBtn").disabled = false;
        startPolling();
      }
    }).catch(() => {});
  }).catch(() => {});
});
</script>
</body>
</html>
"""


class WebGUIRequestHandler(BaseHTTPRequestHandler):
    """HTTP handler for local optimizer web client."""

    state: OptimizerState
    initial_input: str = ""
    initial_output: str = ""

    def log_message(self, format, *args):
        # Suppress noisy standard HTTP access logs in console
        pass

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            html = HTML_TEMPLATE.replace("__INITIAL_INPUT__", self.initial_input).replace("__INITIAL_OUTPUT__", self.initial_output)
            self.wfile.write(html.encode("utf-8"))
        elif self.path == "/api/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(self.state.to_dict()).encode("utf-8"))
        elif self.path == "/api/config":
            from media_optimizer.config import get_default_config
            conf = get_default_config()
            data = {
                "convert_heic_to_jpeg": conf.convert_heic_to_jpeg,
                "preserve_metadata": conf.preserve_metadata,
                "jpeg_quality": conf.jpeg_quality,
                "image_max_dimension": conf.image_max_dimension,
                "video_max_height": conf.video_max_height,
                "video_max_fps": conf.video_max_fps,
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode("utf-8"))
        else:
            self.send_error(404, "Not Found")

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length) if content_length > 0 else b"{}"
        try:
            body = json.loads(post_data.decode("utf-8"))
        except Exception:
            body = {}

        if self.path == "/api/browse":
            target_type = body.get("type", "folder")
            chosen_path = ""

            # Use native macOS Finder picker via osascript
            if sys.platform == "darwin":
                if target_type == "file":
                    cmd = ["osascript", "-e", 'POSIX path of (choose file with prompt "Select Media File to Optimize" of type {"public.image", "public.movie", "public.video", "public.data"})']
                elif target_type == "output":
                    cmd = ["osascript", "-e", 'POSIX path of (choose folder with prompt "Select Destination Folder")']
                else:
                    cmd = ["osascript", "-e", 'POSIX path of (choose folder with prompt "Select Media Folder to Optimize")']
                try:
                    res = subprocess.run(cmd, capture_output=True, text=True)
                    if res.returncode == 0:
                        chosen_path = res.stdout.strip().rstrip("/")
                except Exception:
                    pass

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"path": chosen_path}).encode("utf-8"))

        elif self.path == "/api/config":
            from media_optimizer.config import get_default_config, save_user_config
            conf = get_default_config()
            for k in ["convert_heic_to_jpeg", "preserve_metadata", "jpeg_quality", "image_max_dimension", "video_max_height", "video_max_fps"]:
                if k in body:
                    setattr(conf, k, body[k])
            # Sync related quality fields
            if "jpeg_quality" in body:
                conf.webp_quality = body["jpeg_quality"] - 2
                conf.heic_quality = body["jpeg_quality"] - 2
            save_user_config(conf)
            
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode("utf-8"))

        elif self.path == "/api/start":
            inp = body.get("input_dir", "").strip()
            out = body.get("output_dir", "").strip()

            if not inp or not os.path.exists(inp):
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": f"Invalid input path: {inp}"}).encode("utf-8"))
                return

            if not out:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Output folder must be specified."}).encode("utf-8"))
                return

            input_path = Path(inp).resolve()
            output_path = Path(out).resolve()

            if input_path == output_path:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Output folder cannot equal input folder."}).encode("utf-8"))
                return

            self.state.reset()
            config = get_default_config()
            pipeline = OptimizationPipeline(config)
            self.state.pipeline = pipeline

            def worker():
                def on_prog(cur, tot, file_name, orig_b, opt_b, saved_b, msg):
                    with self.state.lock:
                        self.state.current = cur
                        self.state.total = tot
                        if file_name:
                            self.state.current_file = file_name
                        self.state.orig_bytes = orig_b
                        self.state.opt_bytes = opt_b
                        self.state.saved_bytes = saved_b
                        if orig_b > 0:
                            self.state.reduction_percent = (saved_b / orig_b) * 100.0

                def on_act(msg, level):
                    self.state.add_log(level=level, msg=msg)

                try:
                    summary = pipeline.process_batch(
                        input_path,
                        output_path,
                        on_progress=on_prog,
                        on_activity=on_act,
                    )
                    with self.state.lock:
                        self.state.running = False
                        self.state.completed = True
                        self.state.summary = summary.format_report()
                except Exception as e:
                    with self.state.lock:
                        self.state.running = False
                        self.state.error = str(e)
                        self.state.add_log(level="error", msg=f"Processing error: {e}")

            thread = threading.Thread(target=worker, daemon=True)
            thread.start()

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "started"}).encode("utf-8"))

        elif self.path == "/api/stop":
            if self.state.pipeline:
                self.state.pipeline.stop()
            with self.state.lock:
                self.state.running = False
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "stopped"}).encode("utf-8"))
        else:
            self.send_error(404, "Not Found")


def find_free_port(start_port: int = 8088) -> int:
    """Find an available TCP port starting from start_port."""
    port = start_port
    while port < 65535:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
        port += 1
    return 8088


def launch_web_gui(initial_folder: Optional[str] = None) -> None:
    """Start local web server and open browser."""
    state = OptimizerState()
    port = find_free_port()

    initial_input = str(Path(initial_folder).resolve()) if initial_folder and os.path.exists(initial_folder) else ""
    initial_output = f"{initial_input}_optimized" if initial_input else ""

    WebGUIRequestHandler.state = state
    WebGUIRequestHandler.initial_input = initial_input
    WebGUIRequestHandler.initial_output = initial_output

    server = HTTPServer(("127.0.0.1", port), WebGUIRequestHandler)
    url = f"http://127.0.0.1:{port}"

    print(f"\n=======================================================")
    print(f" Media Optimizer Web Interface")
    print(f" Running at: {url}")
    print(f" Opening in your default browser...")
    print(f" Press Ctrl+C in this terminal to stop.")
    print(f"=======================================================\n")

    threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down Web GUI...")
    finally:
        server.server_close()


if __name__ == "__main__":
    launch_web_gui()
