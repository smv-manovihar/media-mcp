import os, glob, hashlib
from typing import List, Any
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


def _compiled_filter(config) -> Any:
    """Build a (names, globs) exclusion pair + checker, or (None, None)."""
    if config is None:
        return None, None
    try:
        from config.settings import compile_exclusions

        return compile_exclusions(config), True
    except Exception:
        return None, None


def _walk_files_pruned(root: str, compiled, has_filter: bool):
    """
    Yield absolute file paths under root, pruning excluded directories
    top-down so we never descend into e.g. .git / node_modules / Windows.
    The explicit root itself is never pruned (explicit user intent).
    """
    from pathlib import Path as _Path

    if has_filter:
        from config.settings import should_exclude_compiled

        names, globs = compiled
    else:
        should_exclude_compiled = None  # type: ignore

    for current, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        # Prune excluded subdirectories in place (never the root itself)
        if has_filter:
            kept = []
            for d in dirnames:
                try:
                    if should_exclude_compiled(_Path(current) / d, names, globs):
                        continue
                except Exception:
                    pass
                kept.append(d)
            dirnames[:] = kept
        for fn in filenames:
            fp = os.path.join(current, fn)
            if has_filter:
                try:
                    if should_exclude_compiled(_Path(fp), names, globs):
                        continue
                except Exception:
                    pass
            # Skip non-files (broken symlinks etc.) without raising
            try:
                if not os.path.isfile(fp):
                    continue
            except OSError:
                continue
            yield os.path.abspath(fp)


def list_images(paths: List[str], config: Any = None) -> List[str]:
    compiled, has_filter = _compiled_filter(config)
    images = []
    for p in paths:
        path_obj = Path(p)
        if path_obj.is_dir():
            try:
                for fp in _walk_files_pruned(str(path_obj), compiled, has_filter):
                    if Path(fp).suffix.lower() in IMAGE_EXTENSIONS:
                        images.append(fp)
            except (PermissionError, OSError):
                pass
        elif path_obj.is_file():
            s = str(path_obj.resolve())
            if path_obj.suffix.lower() in IMAGE_EXTENSIONS:
                if has_filter:
                    from config.settings import should_exclude_compiled

                    try:
                        if should_exclude_compiled(
                            Path(s), compiled[0], compiled[1]
                        ):
                            continue
                    except Exception:
                        pass
                images.append(s)
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


def list_all_files(paths: List[str], config: Any = None) -> List[str]:
    """Recursively lists all files (not directories) in the given paths.

    When a config is supplied, excluded directories are pruned during the
    walk (never descended into) and excluded files are skipped inline —
    much faster than listing everything and filtering afterwards.
    """
    compiled, has_filter = _compiled_filter(config)
    all_files = []
    for p in paths:
        path_obj = Path(p)
        if path_obj.is_dir():
            try:
                all_files.extend(
                    _walk_files_pruned(str(path_obj), compiled, has_filter)
                )
            except (PermissionError, OSError):
                # Skip directories we can't access
                pass
        elif path_obj.is_file():
            s = str(path_obj)
            if has_filter:
                from config.settings import should_exclude_compiled

                try:
                    if should_exclude_compiled(Path(s), compiled[0], compiled[1]):
                        continue
                except Exception:
                    pass
            all_files.append(s)
    return all_files
