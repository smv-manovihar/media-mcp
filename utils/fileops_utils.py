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
    if not scan_paths_abs and not prune:
        return {
            "scan_type": "all_files",
            "found": 0,
            "new": 0,
            "updated": 0,
            "deleted": 0,
            "excluded": 0,
        }

    all_files = list_all_files(scan_paths_abs) if scan_paths_abs else []
    found_paths_on_disk = {os.path.abspath(fp) for fp in all_files}

    allowed_files = []
    scan_excluded = 0
    for fp in all_files:
        abs_fp = os.path.abspath(fp)
        try:
            if should_exclude(Path(abs_fp), config):
                scan_excluded += 1
                continue
        except Exception:
            scan_excluded += 1
            continue
        allowed_files.append(fp)

    new_files_data = []
    updated = 0
    pruned_deleted = 0
    pruned_excluded = 0

    now = datetime.now(timezone.utc).isoformat()

    with database.db.cursor() as cur:
        cur.execute("SELECT hash, path FROM files")
        db_records = cur.fetchall()
        known_hashes = {rec[0]: rec[1] for rec in db_records}
        known_paths = {rec[1]: rec[0] for rec in db_records}
        db_paths = set(known_paths.keys())

        # Collect records to prune
        to_prune: List[tuple[str, str]] = []
        if prune:
            # Global prune: check all DB records
            for h, p in db_records:
                abs_p = os.path.abspath(p)
                if not os.path.exists(abs_p):
                    to_prune.append((h, "deleted"))
                    continue
                try:
                    if should_exclude(Path(abs_p), config):
                        to_prune.append((h, "excluded"))
                except Exception:
                    to_prune.append((h, "excluded"))
        else:
            # Global deletions (like original)
            candidates_deleted = db_paths - found_paths_on_disk
            for path in candidates_deleted:
                if not os.path.exists(path):
                    h = known_paths[path]
                    to_prune.append((h, "deleted"))

            # Local exclusions: only on paths found in scan (existing and in scan_paths)
            local_paths_to_check = db_paths & found_paths_on_disk
            for path in local_paths_to_check:
                try:
                    if should_exclude(Path(path), config):
                        h = known_paths[path]
                        to_prune.append((h, "excluded"))
                except Exception:
                    h = known_paths[path]
                    to_prune.append((h, "excluded"))

        # Perform deletions if any
        if to_prune:
            hashes_to_delete = [h for h, _ in to_prune]
            with database.chroma_lock:
                try:
                    database.chroma_coll.delete(ids=hashes_to_delete)
                except Exception as e:
                    if verbose:
                        print(f"⚠️ Warning: failed to delete vectors from chroma: {e}")

            cur.executemany(
                "DELETE FROM files WHERE hash=?", [(h,) for h, _ in to_prune]
            )
            pruned_deleted = sum(1 for _, t in to_prune if t == "deleted")
            pruned_excluded = sum(1 for _, t in to_prune if t == "excluded")
            if verbose:
                print(
                    f"✓ Pruned {len(to_prune)} DB records (deleted: {pruned_deleted}, excluded: {pruned_excluded})"
                )

        # Update in-memory known maps by removing pruned items
        for h, _ in to_prune:
            p = known_hashes.pop(h, None)
            if p:
                known_paths.pop(p, None)

        # Scan and process allowed files
        iterable = tqdm(allowed_files, desc="Scanning files", disable=not verbose)
        for fp in iterable:
            try:
                abs_fp = os.path.abspath(fp)
                h = sha256_file(fp)
            except (
                FileNotFoundError,
                IsADirectoryError,
                PermissionError,
                OSError,
            ) as e:
                if verbose:
                    print(f"⚠️ Skipped file {fp}: {str(e)}")
                continue

            file_size = os.path.getsize(fp)

            if abs_fp in known_paths:
                # Case 1: Path known, check if modified
                if h != known_paths[abs_fp]:
                    cur.execute(
                        "UPDATE files SET hash=?, file_size=?, updated_at=? WHERE path=?",
                        (h, file_size, now, abs_fp),
                    )
                    updated += 1
            elif h in known_hashes:
                # Case 2: Hash known, file moved
                cur.execute(
                    "UPDATE files SET path=?, updated_at=? WHERE hash=?",
                    (abs_fp, now, h),
                )
                updated += 1
            else:
                # Case 3: New file
                file_type = get_file_type(fp)
                new_files_data.append((h, abs_fp, file_type, file_size, now, now))

        # Bulk insert new files
        if new_files_data:
            cur.executemany(
                "INSERT INTO files (hash, path, file_type, file_size, added_at, updated_at) VALUES (?,?,?,?,?,?)",
                new_files_data,
            )
            if verbose:
                print(f"✓ Added {len(new_files_data)} new file records.")

        if updated and verbose:
            print(f"✓ Updated {updated} moved or modified files.")

    return {
        "scan_type": "all_files",
        "found": len(all_files),
        "new": len(new_files_data),
        "updated": updated,
        "deleted": pruned_deleted,
        "excluded": pruned_excluded + scan_excluded,
    }


if __name__ == "__main__":
    import argparse, pprint

    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", nargs="+", help="Paths to files/dirs to (re)scan")

    args = ap.parse_args()
    result = scan_files(args.scan, prune=True, verbose=True)
    print(result)
