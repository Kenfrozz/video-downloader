from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, Dict, Any

from yt_dlp import YoutubeDL


ProgressHook = Callable[[Dict[str, Any]], None]


def build_ydl_opts(
    download_dir: Path,
    quality: str,
    progress_hook: Optional[ProgressHook] = None,
) -> dict:
    fmt_best = "bestvideo+bestaudio/best"
    fmt_mp4 = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"

    ydl_opts: Dict[str, Any] = {
        "outtmpl": str(download_dir / "%(title)s.%(ext)s"),
        "concurrent_fragment_downloads": 4,
        "format": fmt_best,
        "noprogress": True,
        # Do not ignore errors; fail fast so UI doesn't add stale items
        "ignoreerrors": False,
        "quiet": True,
        "no_warnings": True,
        # Save thumbnails alongside media for richer UI
        "writethumbnail": True,
    }

    if quality == "mp4":
        ydl_opts["format"] = fmt_mp4
    elif quality == "mp3":
        ydl_opts["format"] = "bestaudio/best"
    
    # Postprocessors
    postprocessors: list[dict] = []
    # Always try converting thumbnails to jpg for easy display
    postprocessors.append({
        "key": "FFmpegThumbnailsConvertor",
        "format": "jpg",
    })
    if quality == "mp3":
        postprocessors.append({
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        })
    if postprocessors:
        ydl_opts["postprocessors"] = postprocessors

    if progress_hook is not None:
        ydl_opts["progress_hooks"] = [progress_hook]

    return ydl_opts


def download(url: str, download_dir: Path, quality: str, progress_hook: Optional[ProgressHook] = None) -> None:
    download_dir.mkdir(parents=True, exist_ok=True)
    opts = build_ydl_opts(download_dir, quality, progress_hook)
    with YoutubeDL(opts) as ydl:
        ydl.download([url])
