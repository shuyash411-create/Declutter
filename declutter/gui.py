"""Tkinter desktop window for declutter."""

import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import APP_NAME, __version__
from .actions import ListError, apply_deletions, apply_moves, load_list, plan_deletions, plan_moves
from .pipeline import Cancelled, default_report_dir, run_scan
from .report import human_size
from .resources import configure_tesseract

SETTINGS = Path.home() / ".declutter_settings.json"


def open_path(path):
    """Open a file/folder with the OS default app, without leaking the frozen
    app's library path (PyInstaller / bundled Tesseract) into the browser."""
    path = str(path)
    if sys.platform.startswith("win"):
        os.startfile(path)  # noqa: S606
        return
    env = dict(os.environ)
    for var in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        orig = env.pop(var + "_ORIG", None)
        if orig is not None:
            env[var] = orig
        else:
            env.pop(var, None)
    cmd = ["open", path] if sys.platform == "darwin" else ["xdg-open", path]
    try:
        subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        import webbrowser
        webbrowser.open(Path(path).as_uri())


def _load_settings():
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_settings(data):
    try:
        SETTINGS.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


class App:
    STAGES = {"scan": "Finding files", "photos": "Analysing photos", "documents": "Reading documents",
              "dups": "Finding duplicates", "report": "Writing report", "done": "Done"}

    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.cancel = threading.Event()
        self.worker = None
        self.report_path = None
        self.settings = _load_settings()

        root.title(f"{APP_NAME} — offline photo & document organizer")
        root.minsize(620, 480)
        style = ttk.Style(root)
        if sys.platform.startswith("win") and "vista" in style.theme_names():
            style.theme_use("vista")
        elif sys.platform.startswith("linux") and "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Title.TLabel", font=("TkDefaultFont", 16, "bold"))
        style.configure("Muted.TLabel", foreground="#667085")
        style.configure("Horizontal.TProgressbar", background="#2563eb", troughcolor="#e5e7eb")
        style.configure("Accent.TButton", font=("TkDefaultFont", 11, "bold"), padding=(14, 6))

        outer = ttk.Frame(root, padding=16)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="declutter", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, style="Muted.TLabel",
                  text="Sort, de-duplicate and clean up photos & documents. 100% offline — "
                       "nothing leaves this computer, and nothing is changed until you confirm.",
                  wraplength=700, justify="left").pack(anchor="w", pady=(2, 12))

        # ---- folder list
        lf = ttk.LabelFrame(outer, text=" 1. Folders to scan (treated as one collection) ", padding=10)
        lf.pack(fill="both", expand=True)
        row = ttk.Frame(lf)
        row.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(row, selectmode="extended", height=7, width=90, activestyle="none")
        sb = ttk.Scrollbar(row, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        btns = ttk.Frame(lf)
        btns.pack(fill="x", pady=(8, 0))
        ttk.Button(btns, text="Add folder…", command=self.add_folder).pack(side="left")
        ttk.Button(btns, text="Remove selected", command=self.remove_selected).pack(side="left", padx=6)
        self.ocr_var = tk.BooleanVar(value=self.settings.get("ocr", True))
        ttk.Checkbutton(btns, text="Read text in photos (OCR: receipts, notes, memes)",
                        variable=self.ocr_var).pack(side="right")
        for f in self.settings.get("folders", []):
            if os.path.isdir(f):
                self.listbox.insert("end", f)

        # ---- scan
        sf = ttk.LabelFrame(outer, text=" 2. Scan ", padding=10)
        sf.pack(fill="x", pady=(12, 0))
        srow = ttk.Frame(sf)
        srow.pack(fill="x")
        self.scan_btn = ttk.Button(srow, text="Scan all folders", style="Accent.TButton", command=self.start_scan)
        self.scan_btn.pack(side="left")
        self.cancel_btn = ttk.Button(srow, text="Cancel", command=self.cancel_scan, state="disabled")
        self.cancel_btn.pack(side="left", padx=6)
        self.open_btn = ttk.Button(srow, text="Open report", command=self.open_report, state="disabled")
        self.open_btn.pack(side="right")
        ttk.Button(srow, text="Reports folder", command=self.open_reports_folder).pack(side="right", padx=6)
        self.progress = ttk.Progressbar(sf, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(10, 4))
        self.status = tk.StringVar(value="Add one or more folders, then press “Scan all folders”.")
        ttk.Label(sf, textvariable=self.status, wraplength=700, justify="left").pack(anchor="w")

        # ---- apply
        af = ttk.LabelFrame(outer, text=" 3. After reviewing the report ", padding=10)
        af.pack(fill="x", pady=(12, 0))
        ttk.Label(af, style="Muted.TLabel", wraplength=700, justify="left",
                  text="In the report, press “Export move list” / “Export delete list”, "
                       "then apply the saved file here. You will see a summary and must confirm first.").pack(anchor="w")
        arow = ttk.Frame(af)
        arow.pack(fill="x", pady=(8, 0))
        ttk.Button(arow, text="Apply move list…", command=self.apply_move_list).pack(side="left")
        ttk.Button(arow, text="Apply delete list…", command=self.apply_delete_list).pack(side="left", padx=6)
        ttk.Label(arow, text=f"v{__version__}", style="Muted.TLabel").pack(side="right")

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(200, self._check_ocr)

    # ------------------------------------------------------------ folders --
    def folders(self):
        return list(self.listbox.get(0, "end"))

    def add_folder(self):
        start = self.settings.get("last_dir") or str(Path.home())
        d = filedialog.askdirectory(parent=self.root, title="Choose a folder to scan", initialdir=start, mustexist=True)
        if not d:
            return
        d = os.path.normpath(d)
        self.settings["last_dir"] = os.path.dirname(d)
        existing = self.folders()
        if d in existing:
            return
        for e in existing:
            if d.startswith(e.rstrip(os.sep) + os.sep):
                messagebox.showinfo(APP_NAME, f"{d}\n\nis already included via\n\n{e}", parent=self.root)
                return
        self.listbox.insert("end", d)
        self._persist()

    def remove_selected(self):
        for i in reversed(self.listbox.curselection()):
            self.listbox.delete(i)
        self._persist()

    def _persist(self):
        self.settings["folders"] = self.folders()
        self.settings["ocr"] = bool(self.ocr_var.get())
        _save_settings(self.settings)

    def _check_ocr(self):
        cmd, bundled = configure_tesseract()
        if not cmd:
            self.status.set("Note: Tesseract OCR was not found, so receipts / notes / memes can’t be "
                            "detected. Everything else works.")

    # --------------------------------------------------------------- scan --
    def start_scan(self):
        folders = self.folders()
        if not folders:
            messagebox.showinfo(APP_NAME, "Add at least one folder first.", parent=self.root)
            return
        self._persist()
        self.cancel.clear()
        self.scan_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.open_btn.configure(state="disabled")
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.status.set("Starting…")
        use_ocr = bool(self.ocr_var.get())

        def work():
            try:
                res = run_scan(folders, progress=lambda *a: self.q.put(("progress", a)),
                               cancel_event=self.cancel, use_ocr=use_ocr)
                self.q.put(("done", res))
            except Cancelled:
                self.q.put(("cancelled", None))
            except Exception as e:  # surfaced in the window, never a console
                self.q.put(("error", f"{type(e).__name__}: {e}"))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()
        self.root.after(100, self._poll)

    def cancel_scan(self):
        self.cancel.set()
        self.status.set("Cancelling…")

    def _poll(self):
        last = None
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "progress":
                    last = payload
                    continue
                self._finish(kind, payload)
                return
        except queue.Empty:
            pass
        if last:
            stage, done, total, msg = last
            label = self.STAGES.get(stage, stage)
            if total and stage in ("photos", "documents"):
                if str(self.progress["mode"]) != "determinate":
                    self.progress.stop()
                    self.progress.configure(mode="determinate")
                self.progress["value"] = 100.0 * done / total
                self.status.set(f"{label}: {done} / {total}  —  {msg}")
            else:
                self.status.set(f"{label}… {msg}")
        self.root.after(100, self._poll)

    def _finish(self, kind, payload):
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.scan_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        if kind == "done":
            self.progress["value"] = 100
            self.report_path = payload["report_path"]
            self.open_btn.configure(state="normal")
            self.status.set(
                f"Done in {payload['duration_s']} s: {payload['photos']} photos, {payload['documents']} documents, "
                f"{payload['photo_dup_groups'] + payload['doc_dup_groups']} duplicate groups. OCR: {payload['ocr']}.\n"
                f"Report: {self.report_path}")
            self.open_report()
        elif kind == "cancelled":
            self.progress["value"] = 0
            self.status.set("Scan cancelled. Nothing was changed.")
        else:
            self.progress["value"] = 0
            self.status.set("Scan failed: " + payload)
            messagebox.showerror(APP_NAME, "The scan failed:\n\n" + payload, parent=self.root)

    def open_report(self):
        if self.report_path and os.path.exists(self.report_path):
            open_path(self.report_path)

    def open_reports_folder(self):
        d = default_report_dir()
        d.mkdir(parents=True, exist_ok=True)
        open_path(d)

    # -------------------------------------------------------------- apply --
    def _pick_list(self, title, default_name):
        downloads = Path.home() / "Downloads"
        initial = downloads if downloads.is_dir() else Path.home()
        return filedialog.askopenfilename(parent=self.root, title=title, initialdir=str(initial),
                                          initialfile=default_name,
                                          filetypes=[("declutter list", "*.json"), ("All files", "*")])

    def _busy(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo(APP_NAME, "Please wait for the scan to finish.", parent=self.root)
            return True
        return False

    @staticmethod
    def _skipped_text(plan):
        if not plan.skipped:
            return ""
        lines = [f"\n\n{len(plan.skipped)} item(s) will be skipped:"]
        for it, why in plan.skipped[:6]:
            lines.append(f"  • {os.path.basename(it.get('path', '?'))} — {why}")
        if len(plan.skipped) > 6:
            lines.append(f"  … and {len(plan.skipped) - 6} more")
        return "\n".join(lines)

    def apply_move_list(self):
        if self._busy():
            return
        path = self._pick_list("Choose the exported move list", "declutter_move_list.json")
        if not path:
            return
        try:
            plan = plan_moves(load_list(path, "move"))
        except ListError as e:
            messagebox.showerror(APP_NAME, str(e), parent=self.root)
            return
        if not plan.ok:
            messagebox.showinfo(APP_NAME, "Nothing to move." + self._skipped_text(plan), parent=self.root)
            return
        dests = set()
        for it, d in plan.ok:
            base = Path(d).parent
            for _ in it["subfolder"].split("/"):
                base = base.parent
            dests.add(str(base))
        dests = sorted(dests)
        msg = (f"Move {len(plan.ok)} file(s) ({human_size(plan.total_bytes)}) into category folders under:\n\n"
               + "\n".join("  " + d for d in dests[:5]) + ("\n  …" if len(dests) > 5 else "")
               + "\n\nExisting files are never overwritten, and an undo log is saved."
               + self._skipped_text(plan) + "\n\nContinue?")
        if not messagebox.askyesno(APP_NAME, msg, parent=self.root, icon="question"):
            return
        moved, failed, log = apply_moves(plan)
        text = f"Moved {moved} file(s)."
        if log:
            text += f"\n\nUndo log saved to:\n{log}"
        if failed:
            text += f"\n\n{len(failed)} failed, e.g. {os.path.basename(failed[0][0]['path'])}: {failed[0][1]}"
        messagebox.showinfo(APP_NAME, text, parent=self.root)

    def apply_delete_list(self):
        if self._busy():
            return
        path = self._pick_list("Choose the exported delete list", "declutter_delete_list.json")
        if not path:
            return
        try:
            plan = plan_deletions(load_list(path, "delete"))
        except ListError as e:
            messagebox.showerror(APP_NAME, str(e), parent=self.root)
            return
        if not plan.ok:
            messagebox.showinfo(APP_NAME, "Nothing to delete." + self._skipped_text(plan), parent=self.root)
            return
        choice = DeleteDialog(self.root, plan, self._skipped_text(plan)).result
        if choice is None:
            return
        quarantine = None
        if choice == "quarantine":
            quarantine = filedialog.askdirectory(parent=self.root, title="Folder to move the files into")
            if not quarantine:
                return
        done, failed = apply_deletions(plan, quarantine)
        text = f"{'Moved to quarantine' if quarantine else 'Deleted'}: {done} file(s)."
        if failed:
            text += f"\n\n{len(failed)} failed, e.g. {os.path.basename(failed[0][0]['path'])}: {failed[0][1]}"
        messagebox.showinfo(APP_NAME, text, parent=self.root)

    def on_close(self):
        if self.worker and self.worker.is_alive():
            self.cancel.set()
        self._persist()
        self.root.destroy()


class DeleteDialog(tk.Toplevel):
    """Final confirmation for deletions: permanent delete, quarantine, or cancel."""

    def __init__(self, parent, plan, skipped_text):
        super().__init__(parent)
        self.result = None
        self.title("Confirm deletion")
        self.transient(parent)
        self.resizable(False, False)
        f = ttk.Frame(self, padding=16)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text=f"Delete {len(plan.ok)} file(s) ({human_size(plan.total_bytes)})?",
                  font=("TkDefaultFont", 12, "bold")).pack(anchor="w")
        names = "\n".join("  • " + os.path.basename(it["path"]) for it, _ in plan.ok[:8])
        if len(plan.ok) > 8:
            names += f"\n  … and {len(plan.ok) - 8} more"
        ttk.Label(f, text=names + skipped_text, justify="left").pack(anchor="w", pady=(8, 8))
        ttk.Label(f, text="Permanent deletion cannot be undone. Quarantine moves the files to a folder "
                          "you choose so you can double-check before emptying it.",
                  wraplength=420, justify="left").pack(anchor="w")
        b = ttk.Frame(f)
        b.pack(fill="x", pady=(14, 0))
        ttk.Button(b, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(b, text="Delete permanently", command=lambda: self._done("delete")).pack(side="right", padx=6)
        ttk.Button(b, text="Move to quarantine…", command=lambda: self._done("quarantine")).pack(side="right")
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Escape>", lambda e: self._cancel())
        self.update_idletasks()
        self.geometry(f"+{parent.winfo_rootx() + 60}+{parent.winfo_rooty() + 60}")
        self.grab_set()
        self.wait_window()

    def _done(self, v):
        self.result = v
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


def run():
    if sys.platform.startswith("win"):
        try:  # crisp text on high-DPI Windows displays
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    App(root)
    root.mainloop()
