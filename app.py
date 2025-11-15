from __future__ import annotations

import sys
import os
import warnings

from PySide6.QtWidgets import QApplication

# Prefer absolute import so PyInstaller detects and bundles modules.
try:
    from main_window import MainWindow
except Exception:
    # Fallbacks for package-style execution
    try:
        from .main_window import MainWindow
    except Exception:
        # As a last resort, ensure current dir is importable when run as a script
        import os, sys
        pkg_root = os.path.dirname(os.path.abspath(__file__))
        if pkg_root not in sys.path:
            sys.path.insert(0, pkg_root)
        from main_window import MainWindow


def main() -> int:
    # Tidy up 3rd-party warnings in dev runs
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    try:
        warnings.filterwarnings(
            "ignore",
            message=r".*pkg_resources is deprecated as an API.*",
            category=UserWarning,
        )
    except Exception:
        pass
    app = QApplication(sys.argv)
    app.setApplicationName("Video İndirici")
    try:
        app.setApplicationDisplayName("Video İndirici")
    except Exception:
        pass
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

