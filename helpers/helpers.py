import os, glob, hashlib
from typing import List
from pathlib import Path


def sha256_file(fp: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(fp, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def list_images(paths: List[str]) -> List[str]:
    images = []
    for p in paths:
        if os.path.isdir(p):
            for ext in ("*.jpg", "*.jpeg", "*.png"):
                images += glob.glob(os.path.join(p, "**", ext), recursive=True)
        elif os.path.isfile(p) and p.lower().endswith((".jpg", ".jpeg", ".png")):
            images.append(p)
    return images


def get_file_type(file_path: str) -> str:
    """Determines a file's general type based on its extension."""
    ext = Path(file_path).suffix.lower()
    if ext in [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp"]:
        return "image"
    if ext in [".mp4", ".avi", ".mov", ".mkv", ".wmv"]:
        return "video"
    if ext in [".mp3", ".wav", ".flac", ".aac", ".ogg"]:
        return "audio"
    if ext in [".pdf", ".doc", ".docx", ".txt", ".md"]:
        return "document"
    return "binary"  # Default fallback type


def list_all_files(paths: List[str]) -> List[str]:
    """Recursively lists all files (not directories) in the given paths."""
    all_files = []
    for p in paths:
        path_obj = Path(p)
        if path_obj.is_dir():
            # rglob('*') finds all paths, f.is_file() filters out directories
            all_files.extend([str(f) for f in path_obj.rglob("*") if f.is_file()])
        elif path_obj.is_file():
            all_files.append(str(path_obj))
    return all_files
