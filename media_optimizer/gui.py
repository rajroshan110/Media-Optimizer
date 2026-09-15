"""macOS Native GUI for Media Optimizer using Tkinter and TTK."""

import os
import sys
import threading
import time
try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    HAS_TKINTER = True
except ImportError:
    HAS_TKINTER = False
    tk = None
    filedialog = None
    messagebox = None
    ttk = None
from pathlib import Path
from typing import Optional, List

from media_optimizer.config import get_default_config
from media_optimizer.core.journal import BatchSummary
from media_optimizer.pipeline import OptimizationPipeline


from media_optimizer.utils import format_bytes


class MediaOptimizerApp:
    """Tkinter-based macOS Native UI."""

    def __init__(self, root: tk.Tk, initial_folder: Optional[str] = None):
        self.root = root
        self.root.title("Media Optimizer")
        self.root.geometry("640x700")
        self.root.minsize(560, 600)

        self.config = get_default_config()
        self.pipeline: Optional[OptimizationPipeline] = None
        self.worker_thread: Optional[threading.Thread] = None
        self.selected_files: List[Path] = []
        self._user_custom_output = False
        self._setting_default_output = False

        self._init_ui()

        if initial_folder and os.path.exists(initial_folder):
            self.input_var.set(str(Path(initial_folder).resolve()))
            self._update_default_output()

        # Lift and bring window to front
        self.root.after(100, self._bring_to_front)

    def _bring_to_front(self) -> None:
        """Ensure GUI window pops into the foreground on macOS/desktop."""
        try:
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.after(150, lambda: self.root.attributes("-topmost", False))
            self.root.focus_force()
            if sys.platform == "darwin":
                import subprocess
                subprocess.Popen(
                    ["osascript", "-e", f'tell application "System Events" to set frontmost of (first process whose unix id is {os.getpid()}) to true'],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except Exception:
            pass

    def _init_ui(self) -> None:
        # Style configuration
        style = ttk.Style()
        try:
            style.theme_use("aqua")  # macOS native theme
        except Exception:
            pass

        main_frame = ttk.Frame(self.root, padding="20 20 20 20")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Title / Header
        title_frame = ttk.Frame(main_frame)
        title_frame.pack(fill=tk.X, pady=(0, 16))

        lbl_frame = ttk.Frame(title_frame)
        lbl_frame.pack(side=tk.LEFT)
        title_label = ttk.Label(lbl_frame, text="Media Optimizer", font=("SF Pro Display", 22, "bold"))
        title_label.pack(anchor=tk.W)
        subtitle = "macOS Apple Silicon Hardware Accelerated | Smart Adaptive Quality"
        sub_label = ttk.Label(lbl_frame, text=subtitle, font=("SF Pro Text", 11), foreground="#666666")
        sub_label.pack(anchor=tk.W)

        settings_btn = ttk.Button(title_frame, text="⚙️ Settings", command=self._open_settings)
        settings_btn.pack(side=tk.RIGHT, anchor=tk.N)

        # Media Selection Frame
        folders_frame = ttk.LabelFrame(main_frame, text="Select Media Source & Destination", padding="12 12 12 12")
        folders_frame.pack(fill=tk.X, pady=(0, 12))

        # Input Row (Files or Folder)
        ttk.Label(folders_frame, text="Input:").grid(row=0, column=0, sticky=tk.W, pady=4)
        self.input_var = tk.StringVar()
        self.input_var.trace_add("write", lambda *args: self._on_input_text_changed())
        input_entry = ttk.Entry(folders_frame, textvariable=self.input_var, width=38)
        input_entry.grid(row=0, column=1, padx=(6, 6), sticky=tk.EW)

        btn_box = ttk.Frame(folders_frame)
        btn_box.grid(row=0, column=2, sticky=tk.W)
        browse_file_btn = ttk.Button(btn_box, text="File(s)...", command=self._browse_input_files)
        browse_file_btn.pack(side=tk.LEFT, padx=(0, 4))
        browse_folder_btn = ttk.Button(btn_box, text="Folder...", command=self._browse_input_folder)
        browse_folder_btn.pack(side=tk.LEFT)

        # Output Folder Row
        ttk.Label(folders_frame, text="Output Folder:").grid(row=1, column=0, sticky=tk.W, pady=4)
        self.output_var = tk.StringVar()
        self.output_var.trace_add("write", lambda *args: self._on_output_text_changed())
        output_entry = ttk.Entry(folders_frame, textvariable=self.output_var, width=38)
        output_entry.grid(row=1, column=1, padx=(6, 6), sticky=tk.EW)
        browse_output_btn = ttk.Button(folders_frame, text="Browse...", command=self._browse_output)
        browse_output_btn.grid(row=1, column=2, sticky=tk.W)

        folders_frame.columnconfigure(1, weight=1)

        # Action Buttons
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=(4, 16))

        self.start_btn = ttk.Button(
            btn_frame,
            text="OPTIMIZE MEDIA",
            command=self._start_optimization,
        )
        self.start_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        self.stop_btn = ttk.Button(
            btn_frame,
            text="Stop",
            command=self._stop_optimization,
            state=tk.DISABLED,
        )
        self.stop_btn.pack(side=tk.RIGHT, padx=(6, 0))

        # Progress / Stats Card
        stats_frame = ttk.LabelFrame(main_frame, text="Progress & Savings", padding="12 12 12 12")
        stats_frame.pack(fill=tk.X, pady=(0, 12))

        self.progress_bar = ttk.Progressbar(stats_frame, orient=tk.HORIZONTAL, mode="determinate")
        self.progress_bar.pack(fill=tk.X, pady=(4, 10))

        # Stats Labels
        self.lbl_processing = ttk.Label(stats_frame, text="Processing: Idle", font=("SF Pro Text", 12))
        self.lbl_processing.pack(anchor=tk.W, pady=1)

        self.lbl_original = ttk.Label(stats_frame, text="Original: 0 B", font=("SF Pro Text", 12))
        self.lbl_original.pack(anchor=tk.W, pady=1)

        self.lbl_optimized = ttk.Label(stats_frame, text="Optimized: 0 B", font=("SF Pro Text", 12))
        self.lbl_optimized.pack(anchor=tk.W, pady=1)

        self.lbl_saved = ttk.Label(
            stats_frame,
            text="Saved: 0 B (0.0%)",
            font=("SF Pro Text", 12, "bold"),
            foreground="#008800"
        )
        self.lbl_saved.pack(anchor=tk.W, pady=1)

        # Log Output Frame
        log_frame = ttk.LabelFrame(main_frame, text="Activity Log", padding="8 8 8 8")
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(
            log_frame,
            wrap=tk.WORD,
            height=10,
            font=("SF Mono" if sys.platform == "darwin" else "Courier", 11),
            bg="#000000",
            fg="#ffffff",
            insertbackground="#ffffff",
            selectbackground="#27272a",
            selectforeground="#ffffff",
            relief=tk.FLAT,
            padx=10,
            pady=10,
        )
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=scrollbar.set)
        # Log entry color tags tuned for high contrast on black background
        self.log_text.tag_configure("time", foreground="#888888")
        self.log_text.tag_configure("msg", foreground="#ffffff")
        self.log_text.tag_configure("start", foreground="#fbbf24")
        self.log_text.tag_configure("saved", foreground="#4ade80")
        self.log_text.tag_configure("copied", foreground="#94a3b8")
        self.log_text.tag_configure("error", foreground="#f87171")
        self.log_text.tag_configure("complete", foreground="#c084fc")
        self.log_text.tag_configure("info", foreground="#38bdf8")

    def _open_settings(self):
        """Open a modal window for user settings."""
        top = tk.Toplevel(self.root)
        top.title("Optimization Settings")
        top.geometry("470x500")
        top.resizable(False, False)
        top.transient(self.root)
        top.grab_set()

        class Tooltip:
            def __init__(self, widget, text):
                self.widget = widget
                self.text = text
                self.tipwindow = None
                self.id = None
                self.x = self.y = 0
                self.widget.bind("<Enter>", self.enter)
                self.widget.bind("<Leave>", self.leave)

            def enter(self, event=None):
                self.id = self.widget.after(300, self.showtip)

            def leave(self, event=None):
                if self.id:
                    self.widget.after_cancel(self.id)
                if self.tipwindow:
                    self.tipwindow.destroy()
                    self.tipwindow = None

            def showtip(self):
                if self.tipwindow or not self.text:
                    return
                x, y, _, _ = self.widget.bbox("insert")
                x = x + self.widget.winfo_rootx() + 25
                y = y + self.widget.winfo_rooty() + 20
                self.tipwindow = tw = tk.Toplevel(self.widget)
                tw.wm_overrideredirect(True)
                tw.wm_geometry(f"+{x}+{y}")
                label = tk.Label(tw, text=self.text, justify=tk.LEFT,
                              background="#1e1e1e" if sys.platform == "darwin" else "#ffffe0", 
                              foreground="#ffffff" if sys.platform == "darwin" else "#000000",
                              relief=tk.SOLID, borderwidth=1,
                              font=("SF Pro Text", 11, "normal"),
                              padx=8, pady=6, wraplength=350)
                label.pack(ipadx=1)

        frame = ttk.Frame(top, padding="20 20 20 20")
        frame.pack(fill=tk.BOTH, expand=True)

        # Variables
        var_auto_q = tk.BooleanVar(value=getattr(self.config, "auto_quality", True))
        var_deep = tk.BooleanVar(value=getattr(self.config, "deep_mode", False))
        var_heic = tk.BooleanVar(value=self.config.convert_heic_to_jpeg)
        var_ow = tk.BooleanVar(value=self.config.overwrite_existing)
        var_meta = tk.BooleanVar(value=self.config.preserve_metadata)
        var_img_q = tk.IntVar(value=self.config.jpeg_quality)
        var_img_max = tk.IntVar(value=self.config.image_max_dimension)
        var_vid_h = tk.IntVar(value=self.config.video_max_height)
        var_vid_fps = tk.IntVar(value=self.config.video_max_fps)

        manual_widgets = []

        def add_row(parent, row, text, var, tooltip, is_check=False, is_manual=False):
            lbl_frame = ttk.Frame(parent)
            lbl_frame.grid(row=row, column=0, sticky=tk.W, pady=8)
            
            chk_widget = None
            if is_check:
                chk_widget = ttk.Checkbutton(lbl_frame, text=text, variable=var)
                chk_widget.pack(side=tk.LEFT)
            else:
                lbl = ttk.Label(lbl_frame, text=text)
                lbl.pack(side=tk.LEFT)
                if is_manual:
                    manual_widgets.append(lbl)
            
            info = ttk.Label(lbl_frame, text=" ⓘ", foreground="#0a84ff", cursor="hand2", font=("SF Pro Text", 13))
            info.pack(side=tk.LEFT, padx=(4, 0))
            Tooltip(info, tooltip)
            
            if not is_check:
                ent = ttk.Entry(parent, textvariable=var, width=10)
                ent.grid(row=row, column=1, sticky=tk.E, pady=8)
                if is_manual:
                    manual_widgets.append(ent)
            return chk_widget

        row = 0
        add_row(frame, row, "Automatic Best Quality (Autonomous)", var_auto_q, "Completely autonomous: Media Optimizer inspects each file's entropy and visual structure to automatically select the optimal rate-distortion quality and resolution for least storage and pristine visual quality.\n\nUncheck to manually enforce fixed constraints below.", is_check=True)
        row += 1
        self.chk_deep = add_row(frame, row, "Deep Perceptual Analysis (Slow)", var_deep, "When enabled, performs local-window SSIM binary search and video bitrate sampling for mathematically optimal compression. Slower.\n\nLeave off for WhatsApp-fast single-pass processing.", is_check=True)
        row += 1
        add_row(frame, row, "Convert HEIC to JPEG", var_heic, "Converts Apple HEIC photos to standard JPEG for universal compatibility.\nUncheck to keep original format.", is_check=True)
        row += 1
        add_row(frame, row, "Overwrite Existing Files", var_ow, "When re-processing files, overwrite the existing optimized file instead of adding a suffix like _1.", is_check=True)
        row += 1
        add_row(frame, row, "Preserve Metadata (EXIF/GPS)", var_meta, "Keeps hidden data like date taken and GPS location.\nUncheck to strip data and save a few kilobytes.", is_check=True)
        row += 1
        add_row(frame, row, "Manual Image Quality (1-100):", var_img_q, "Fallback compression level when auto quality is off. 80 is the sweet spot.\nLower = smaller file but blurrier.", is_manual=True)
        row += 1
        add_row(frame, row, "Max Image Dimension (px):", var_img_max, "Resizes huge photos down to this size on their longest edge.\n2048px is WhatsApp HD quality.", is_manual=True)
        row += 1
        add_row(frame, row, "Max Video Height (px):", var_vid_h, "Resizes 4K/UHD videos down to this height (e.g., 1080 for 1080p).\nSaves massive space.", is_manual=True)
        row += 1
        add_row(frame, row, "Max Video FPS:", var_vid_fps, "Drops 60fps video down to 30fps.\n30fps cuts file size in half with normal motion.", is_manual=True)
        row += 1

        def _update_ui_state(*args):
            is_auto = var_auto_q.get()
            for w in manual_widgets:
                try:
                    w.config(state=tk.DISABLED if is_auto else tk.NORMAL)
                except Exception:
                    pass
            # Deep Mode requires Auto Quality to be enabled
            if hasattr(self, "chk_deep"):
                self.chk_deep.config(state=tk.NORMAL if is_auto else tk.DISABLED)

        var_auto_q.trace_add("write", _update_ui_state)
        _update_ui_state()

        def _save():
            self.config.auto_quality = var_auto_q.get()
            self.config.deep_mode = var_deep.get()
            self.config.convert_heic_to_jpeg = var_heic.get()
            self.config.overwrite_existing = var_ow.get()
            self.config.preserve_metadata = var_meta.get()
            self.config.jpeg_quality = var_img_q.get()
            self.config.webp_quality = var_img_q.get() - 2
            self.config.heic_quality = var_img_q.get() - 2
            self.config.image_max_dimension = var_img_max.get()
            self.config.video_max_height = var_vid_h.get()
            self.config.video_max_fps = var_vid_fps.get()
            
            from media_optimizer.config import save_user_config
            save_user_config(self.config)
            top.destroy()
            messagebox.showinfo("Settings Saved", "Configuration has been updated and saved to disk.", parent=self.root)

        def _reset():
            var_auto_q.set(True)
            var_deep.set(False)
            var_heic.set(True)
            var_ow.set(False)
            var_meta.set(True)
            var_img_q.set(80)
            var_img_max.set(2048)
            var_vid_h.set(1080)
            var_vid_fps.set(30)
            _update_ui_state()

        btn_box = ttk.Frame(frame)
        btn_box.grid(row=row, column=0, columnspan=2, pady=(20, 0))
        ttk.Button(btn_box, text="Save Settings", command=_save).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(btn_box, text="Reset", command=_reset).pack(side=tk.LEFT)

    def _browse_input_files(self) -> None:
        filetypes = [
            ("Supported Media", "*.jpg *.jpeg *.png *.webp *.heic *.mp4 *.mov *.m4v *.mkv *.avi *.webm"),
            ("Photos", "*.jpg *.jpeg *.png *.webp *.heic"),
            ("Videos", "*.mp4 *.mov *.m4v *.mkv *.avi *.webm"),
            ("All Files", "*.*"),
        ]
        chosen = filedialog.askopenfilenames(
            title="Select Media File(s) to Optimize",
            filetypes=filetypes,
        )
        if chosen:
            self._user_custom_output = False
            self.selected_files = [Path(f).resolve() for f in chosen]
            if len(self.selected_files) == 1:
                self.input_var.set(str(self.selected_files[0]))
            else:
                parent_name = self.selected_files[0].parent.name
                self.input_var.set(f"{len(self.selected_files)} files selected ({parent_name})")
            self._update_default_output()

    def _browse_input_folder(self) -> None:
        folder = filedialog.askdirectory(title="Select Media Folder to Optimize")
        if folder:
            self._user_custom_output = False
            self.selected_files = []
            self.input_var.set(folder)
            self._update_default_output()

    def _browse_input(self) -> None:
        """Compatibility fallback alias."""
        self._browse_input_folder()

    def _browse_output(self) -> None:
        folder = filedialog.askdirectory(title="Select Destination Folder")
        if folder:
            self._user_custom_output = True
            self.output_var.set(folder)

    def _on_output_text_changed(self) -> None:
        if not getattr(self, "_setting_default_output", False):
            self._user_custom_output = True

    def _on_input_text_changed(self) -> None:
        inp = self.input_var.get().strip()
        if inp and not inp.endswith("files selected)"):
            p = Path(inp)
            if p.is_file():
                self.selected_files = [p]
            elif p.is_dir():
                self.selected_files = []
        self._update_default_output()

    def _update_default_output(self) -> None:
        if self.selected_files:
            default_out = self.selected_files[0].parent
            if not self._user_custom_output or not self.output_var.get().strip():
                self._setting_default_output = True
                self.output_var.set(str(default_out))
                self._setting_default_output = False
            return

        inp = self.input_var.get().strip()
        if inp:
            p = Path(inp)
            if p.is_file():
                default_out = p.parent
            else:
                default_out = p.parent / f"{p.name}_optimized"
            if not self._user_custom_output or not self.output_var.get().strip():
                self._setting_default_output = True
                self.output_var.set(str(default_out))
                self._setting_default_output = False

    def _log(self, text: str, tag: Optional[str] = None) -> None:
        if tag:
            self.log_text.insert(tk.END, text + "\n", tag)
        else:
            self.log_text.insert(tk.END, text + "\n", "msg")
        self.log_text.see(tk.END)

    def _start_optimization(self) -> None:
        out = self.output_var.get().strip()
        if not out:
            messagebox.showerror("Error", "Please specify an output directory.")
            return

        output_path = Path(out).resolve()

        if self.selected_files:
            input_target = self.selected_files
            target_desc = f"{len(self.selected_files)} selected file(s)"
            if len(self.selected_files) == 1 and self.selected_files[0] == output_path:
                messagebox.showerror("Error", "Output destination cannot be identical to the input file.")
                return
        else:
            inp = self.input_var.get().strip()
            if not inp or not os.path.exists(inp):
                messagebox.showerror("Error", "Please select a valid input file or directory.")
                return
            input_target = Path(inp).resolve()
            target_desc = str(input_target)
            if input_target.is_dir() and input_target == output_path:
                messagebox.showerror("Error", "Output directory cannot be identical to the input directory.")
                return
            if input_target.is_file() and input_target == output_path:
                messagebox.showerror("Error", "Output destination cannot be identical to the input file.")
                return

        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.progress_bar["value"] = 0
        self.log_text.delete("1.0", tk.END)
        now_str = time.strftime("%H:%M:%S")
        self._log(f"[{now_str}] 🚀 Starting optimization: {target_desc}")
        self._log(f"[{now_str}] 📁 Destination: {output_path}")

        self.pipeline = OptimizationPipeline(self.config)

        def worker():
            try:
                summary: BatchSummary = self.pipeline.process_batch(
                    input_target,
                    output_path,
                    on_progress=self._on_progress_update,
                    on_item_finish=self._on_item_finish,
                    on_activity=self._on_activity,
                )
                self.root.after(0, lambda: self._on_finish(summary))
            except Exception as e:
                self.root.after(0, lambda: self._on_error(str(e)))

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()

    def _stop_optimization(self) -> None:
        if self.pipeline:
            now_str = time.strftime("%H:%M:%S")
            self._log(f"[{now_str}] 🛑 Stopping batch... finishing active files.")
            self.pipeline.stop()
            self.stop_btn.config(state=tk.DISABLED)

    def _on_activity(self, msg: str, level: str) -> None:
        def log_act():
            now = time.strftime("%H:%M:%S")
            icons = {
                "start": "⚡️",
                "saved": "✅",
                "copied": "📁",
                "error": "❌",
                "complete": "🎉",
                "info": "ℹ️",
            }
            icon = icons.get(level, "•")
            self.log_text.insert(tk.END, f"[{now}] ", "time")
            self.log_text.insert(tk.END, f"{icon} ", level)
            self.log_text.insert(tk.END, f"{msg}\n", "msg")
            self.log_text.see(tk.END)
        self.root.after(0, log_act)

    def _on_progress_update(self, current: int, total: int, current_file: str, orig_b: int, opt_b: int, saved_b: int, msg: str) -> None:
        def update_gui():
            if total > 0:
                self.progress_bar["maximum"] = total
                self.progress_bar["value"] = current
                pct_str = f"({(current/total)*100:.1f}%)"
            else:
                pct_str = ""

            saved_pct = (saved_b / orig_b * 100.0) if orig_b > 0 else 0.0

            file_info = f" | {current_file}" if current_file else ""
            self.lbl_processing.config(text=f"Processing: {current} / {total} {pct_str}{file_info}")
            self.lbl_original.config(text=f"Original: {format_bytes(orig_b)}")
            self.lbl_optimized.config(text=f"Optimized: {format_bytes(opt_b)}")
            self.lbl_saved.config(text=f"Saved: {format_bytes(saved_b)} ({saved_pct:.1f}%)")

        self.root.after(0, update_gui)

    def _on_item_finish(self, rel_path: str, orig_sz: int, opt_sz: int, msg: str) -> None:
        # Handled uniformly via _on_activity to maintain clean single-stream log ordering
        pass

    def _on_finish(self, summary: BatchSummary) -> None:
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self._log("\n" + summary.format_report())
        messagebox.showinfo("Completed", f"Batch Complete!\nSpace Saved: {format_bytes(summary.saved_bytes)} ({summary.reduction_percent:.1f}%)")

    def _on_error(self, err_msg: str) -> None:
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self._log(f"\n[Error] {err_msg}")
        messagebox.showerror("Error", f"Processing error: {err_msg}")


def launch_gui(initial_folder: Optional[str] = None, force_web: bool = False) -> None:
    """Launch the GUI window (Native Aqua Tkinter or Web fallback)."""
    if force_web or not HAS_TKINTER:
        if not HAS_TKINTER:
            print("[Notice] Tkinter is not available in the current Python environment.")
            print("[Notice] Automatically launching local browser-based Web GUI...")
        from media_optimizer.web_gui import launch_web_gui
        launch_web_gui(initial_folder)
        return

    try:
        root = tk.Tk()
        app = MediaOptimizerApp(root, initial_folder)
        root.mainloop()
    except Exception as e:
        print(f"[Notice] Failed to open native Tkinter window: {e}")
        print("[Notice] Automatically falling back to local Web GUI...")
        from media_optimizer.web_gui import launch_web_gui
        launch_web_gui(initial_folder)


if __name__ == "__main__":
    launch_gui()
