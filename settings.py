from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path


SETTINGS_FILE = Path(__file__).resolve().parent / "settings.json"


@dataclass
class AppSettings:
    download_dir: str

    @staticmethod
    def default() -> "AppSettings":
        return AppSettings(download_dir=str(Path.home() / "Downloads"))


def load_settings() -> AppSettings:
    try:
        if SETTINGS_FILE.exists():
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            return AppSettings(**data)
    except Exception:
        pass
    return AppSettings.default()


def save_settings(settings: AppSettings) -> None:
    try:
        SETTINGS_FILE.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        # Sessizce geç; UI tarafında hatalar gösteriliyor.
        pass

