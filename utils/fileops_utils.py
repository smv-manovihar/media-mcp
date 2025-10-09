import os
from tqdm import tqdm
from datetime import datetime, timezone
from typing import List, Dict
from pathlib import Path

# Assume these helpers and utils are available from your project
from utils import database
from helpers.helpers import sha256_file, list_all_files, get_file_type
from config.settings import load_config, should_exclude


def scan_files(
    scan_paths: List[str], verbose: bool = False, prune: bool = False
) -> Dict:
    """
    Scans the provided paths, updating the database.

    - verbose: If True, shows progress bars and prints status messages.
    - prune: If True, performs a global prune of the database, removing records for any file that is deleted or now excluded. Defaults to False.

    Returns a dictionary of counts for all operations.
    """
    if scan_paths is None:
        scan_paths = []
    scan_paths_abs = [os.path.abspath(p) for p in scan_paths]
    config = load_config()

    # If nothing to scan and not pruning, return zero counts
    if not scan_paths and not prune:
        return {
            "scan_type": "all_files",
            "found": 0,
            "new": 0,
            "updated": 0,
            "deleted": 0,
            "excluded": 0,
        }

    all_files = list_all_files(scan_paths_abs) if scan_paths else []
    allowed_files = []
    excluded_count = 0
    for fp in all_files:
        try:
            if should_exclude(Path(fp), config):
                excluded_count += 1
                continue
        except Exception:
            excluded_count += 1
            continue
        allowed_files.append(fp)

    new_files_data = []
    updated = 0
    pruned_deleted = 0
    pruned_excluded = 0

    with database.db.cursor() as cur:
        if prune:
            cur.execute("SELECT hash, path FROM files")
            db_records_for_prune = cur.fetchall()
            to_prune = []
            for h, p in db_records_for_prune:
                abs_p = os.path.abspath(p)
                if not os.path.exists(abs_p):
                    to_prune.append((h, "deleted"))
                    continue
                try:
                    if should_exclude(Path(abs_p), config):
                        to_prune.append((h, "excluded"))
                except Exception:
                    to_prune.append((h, "excluded"))

            if to_prune:
                hashes_to_delete = [h for h, _ in to_prune]
                with database.chroma_lock:
                    try:
                        database.chroma_coll.delete(ids=hashes_to_delete)
                    except Exception as e:
                        if verbose:
                            print(
                                f"⚠️ Warning: failed to delete vectors from chroma: {e}"
                            )

                cur.executemany(
                    "DELETE FROM files WHERE hash=?", [(h,) for h in hashes_to_delete]
                )
                pruned_deleted = sum(1 for _, t in to_prune if t == "deleted")
                pruned_excluded = sum(1 for _, t in to_prune if t == "excluded")
                if verbose:
                    print(
                        f"✓ Pruned {len(hashes_to_delete)} DB records (deleted: {pruned_deleted}, excluded: {pruned_excluded})"
                    )

        cur.execute("SELECT hash, path FROM files")
        db_records = cur.fetchall()
        known_hashes = {rec[0]: rec[1] for rec in db_records}
        known_paths = {rec[1]: rec[0] for rec in db_records}

        iterable = tqdm(allowed_files, desc="Scanning files", disable=not verbose)
        for fp in iterable:
            try:
                abs_fp = os.path.abspath(fp)
                h = sha256_file(fp)
            except (FileNotFoundError, IsADirectoryError):
                continue

            now = datetime.now(timezone.utc).isoformat()
            if abs_fp in known_paths:
                if h != known_paths[abs_fp]:
                    cur.execute(
                        "UPDATE files SET hash=?, file_size=?, updated_at=? WHERE path=?",
                        (h, os.path.getsize(fp), now, abs_fp),
                    )
                    updated += 1
            elif h in known_hashes:
                cur.execute(
                    "UPDATE files SET path=?, updated_at=? WHERE hash=?",
                    (abs_fp, now, h),
                )
                updated += 1
            else:
                new_files_data.append(
                    (h, abs_fp, get_file_type(fp), os.path.getsize(fp), now, now)
                )

        if new_files_data:
            cur.executemany(
                "INSERT INTO files (hash, path, file_type, file_size, added_at, updated_at) VALUES (?,?,?,?,?,?)",
                new_files_data,
            )
            if verbose:
                print(f"✓ Added {len(new_files_data)} new file records.")

        if updated and verbose:
            print(f"✓ Updated {updated} moved or modified files.")

    pruned_total = pruned_deleted + pruned_excluded
    return {
        "scan_type": "all_files",
        "found": len(allowed_files),
        "new": len(new_files_data),
        "updated": updated,
        "deleted": pruned_total,
        "excluded": excluded_count,
    }
