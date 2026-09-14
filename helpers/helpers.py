import os, glob, hashlib
from typing import List
from pathlib import Path


import hashlib
from typing import Optional


def sha256_file(fp: str, chunk: int = 1 << 20) -> Optional[str]:
    """
    Compute SHA-256 hash of a file, reading it in chunks.

    Args:
        fp: File path to hash.
        chunk: Size of chunks to read (default: 1MB).

    Returns:
        Hexadecimal SHA-256 hash, or None if the file cannot be read.

    Raises:
        FileNotFoundError: If the file does not exist.
        PermissionError: If access to the file is denied.
        IsADirectoryError: If the path is a directory.
        OSError: For other OS-related errors (e.g., I/O issues).
    """
    h = hashlib.sha256()
    try:
        with open(fp, "rb") as f:
            for block in iter(lambda: f.read(chunk), b""):
                h.update(block)
        return h.hexdigest()
    except (FileNotFoundError, PermissionError, IsADirectoryError, OSError):
        raise  # Re-raise specific exceptions for caller to handle


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".gif"}


def list_images(paths: List[str]) -> List[str]:
    images = []
    for p in paths:
        path_obj = Path(p)
        if path_obj.is_dir():
            try:
                for f in path_obj.rglob("*"):
                    if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS:
                        images.append(str(f.resolve()))
            except (PermissionError, OSError):
                pass
        elif path_obj.is_file() and path_obj.suffix.lower() in IMAGE_EXTENSIONS:
            images.append(str(path_obj.resolve()))
    return sorted(list(set(images)))


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
            try:
                # rglob('*') finds all paths, f.is_file() filters out directories
                all_files.extend([str(f) for f in path_obj.rglob("*") if f.is_file()])
            except (PermissionError, OSError):
                # Skip directories we can't access
                pass
        elif path_obj.is_file():
            all_files.append(str(path_obj))
    return all_files
