"""Start diemut:  `diemut`  or  `python -m diemut`."""

import multiprocessing
import sys


def main():
    # Speaker labelling runs in a separate process (see speakers.diarize).
    multiprocessing.freeze_support()
    if sys.platform == "win32":
        import ctypes
        try:  # sharp text on high-resolution Windows screens
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        try:  # show diemut's own icon in the taskbar, not Python's
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("diemut.app")
        except Exception:
            pass

    try:
        import tkinter as tk
        from tkinter import messagebox
    except ImportError:
        print("diemut needs Tkinter, which is missing from this Python installation.\n"
              "Windows/macOS: reinstall Python from python.org.\n"
              "Linux: sudo apt install python3-tk")
        sys.exit(1)

    root = tk.Tk()
    missing = []
    for module, package in (("faster_whisper", "faster-whisper"), ("sherpa_onnx", "sherpa-onnx"),
                            ("docx", "python-docx"), ("numpy", "numpy")):
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    if missing:
        root.withdraw()
        messagebox.showerror("diemut", "Some parts of diemut aren't installed: "
                             + ", ".join(missing)
                             + "\n\nIn the diemut folder, run:  pip install .")
        sys.exit(1)

    from .ui import App
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
