import os
import hashlib
from pathlib import Path
from datetime import datetime, timezone
from typing import Tuple, Dict, Any, List, Optional
from utils import database
from helpers.helpers import get_file_type


def hash_bytes(data: bytes) -> str:
    """Compute SHA-256 hash of raw bytes."""
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def hash_file(file_path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Compute SHA-256 hash of a file on disk in chunks."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def find_existing_file_by_hash(file_hash: str, target_dir: Path) -> Optional[Path]:
    """
    Check if a file with the exact SHA-256 hash already exists.
    Checks:
    1. The target directory (e.g. uploads/) on disk.
    2. The SQLite database for any tracked file in the target directory.
    """
    # 1. Check SQLite database
    try:
        with database.db.cursor() as cur:
            cur.execute("SELECT path FROM files WHERE hash = ?", (file_hash,))
            rows = cur.fetchall()
            for (p_str,) in rows:
                p = Path(p_str)
                if p.exists():
                    return p.resolve()
    except Exception:
        pass

    # 2. Check files on disk in target_dir
    if target_dir.exists():
        for item in target_dir.glob("*"):
            if item.is_file():
                try:
                    if hash_file(item) == file_hash:
                        return item.resolve()
                except Exception:
                    continue

    return None


def save_uploaded_file_deduplicated(
    uploaded_file: Any, target_dir: str = "uploads"
) -> Tuple[str, bool, str]:
    """
    Saves an uploaded file with content-hash deduplication.
    
    If an identical file (same SHA-256) already exists:
    - Skips writing duplicate bytes to disk.
    - Returns (canonical_existing_path, True, file_hash).

    If the file is new:
    - Writes it with a sanitized unique name.
    - Registers it in database.db ('files' table).
    - Returns (new_saved_path, False, file_hash).
    """
    dest_dir = Path(target_dir).resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Read uploaded bytes into memory (Streamlit UploadedFile allows getbuffer() or read())
    content = uploaded_file.getvalue() if hasattr(uploaded_file, "getvalue") else uploaded_file.read()
    file_hash = hash_bytes(content)
    file_size = len(content)

    # 1. Deduplication check: does identical content already exist?
    existing_path = find_existing_file_by_hash(file_hash, dest_dir)
    if existing_path is not None and existing_path.exists():
        return str(existing_path), True, file_hash

    # 2. File is unique: determine target file path
    orig_name = getattr(uploaded_file, "name", "uploaded_file")
    base, ext = os.path.splitext(orig_name)
    candidate = orig_name
    counter = 1

    while (dest_dir / candidate).exists():
        candidate = f"{base}_{file_hash[:8]}_{counter}{ext}"
        counter += 1

    saved_path = dest_dir / candidate
    with open(saved_path, "wb") as f:
        f.write(content)

    canonical_str = str(saved_path.resolve())

    # 3. Register newly uploaded file in SQLite database
    try:
        now = datetime.now(timezone.utc).isoformat()
        file_type = get_file_type(canonical_str)
        with database.db.cursor() as cur:
            cur.execute(
                """
                INSERT OR REPLACE INTO files (hash, path, file_type, file_size, added_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (file_hash, canonical_str, file_type, file_size, now, now),
            )
    except Exception as e:
        # Non-critical: file is saved on disk even if DB logging encounters an issue
        pass

    return canonical_str, False, file_hash


def clean_duplicate_uploads(target_dir: str = "uploads") -> Dict[str, Any]:
    """
    Scans the uploads directory, groups files by SHA-256 hash, and removes redundant duplicates.
    Keeps the oldest or primary file for each unique hash.
    Returns a summary of cleaned files and reclaimed bytes.
    """
    dest_dir = Path(target_dir).resolve()
    if not dest_dir.exists():
        return {"scanned": 0, "duplicates_removed": 0, "bytes_reclaimed": 0}

    hash_map: Dict[str, List[Path]] = {}
    for item in sorted(dest_dir.iterdir(), key=lambda x: x.stat().st_mtime if x.is_file() else 0):
        if item.is_file():
            try:
                h = hash_file(item)
                hash_map.setdefault(h, []).append(item)
            except Exception:
                continue

    removed_count = 0
    bytes_reclaimed = 0

    for h, file_list in hash_map.items():
        if len(file_list) > 1:
            # Keep first (oldest), remove remaining duplicates
            canonical = file_list[0]
            for dup in file_list[1:]:
                try:
                    bytes_reclaimed += dup.stat().st_size
                    dup.unlink()
                    removed_count += 1
                except Exception:
                    pass

    return {
        "unique_files": len(hash_map),
        "duplicates_removed": removed_count,
        "bytes_reclaimed": bytes_reclaimed,
    }
