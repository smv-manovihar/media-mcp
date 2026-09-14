import os
from tqdm import tqdm
from datetime import datetime, timezone
from typing import List, Dict, Any
from pathlib import Path

# Assume these helpers and utils are available from your project
from utils import database
from helpers.helpers import sha256_file, list_all_files, get_file_type
from config.settings import (
    load_config,
    compile_exclusions,
    should_exclude_compiled,
)


def scan_files(
    scan_paths: List[str],
    verbose: bool = False,
    prune: bool = False,
    progress_callback: Any = None,
    cancel_event: Any = None,
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
    excl_names, excl_globs = compile_exclusions(config)

    def _excluded(abs_path: str) -> bool:
        try:
            return should_exclude_compiled(Path(abs_path), excl_names, excl_globs)
        except Exception:
            return True

    # If nothing to scan and not pruning, return zero counts
    if not scan_paths_abs and not prune:
        return {
            "scan_type": "all_files",
            "found": 0,
            "new": 0,
            "updated": 0,
            "deleted": 0,
            "excluded": 0,
            "unchanged": 0,
            "hashed": 0,
        }

    # Excluded dirs are pruned during the walk (never descended into);
    # excluded files are skipped inline by the walker.
    all_files = list_all_files(scan_paths_abs, config=config) if scan_paths_abs else []
    found_paths_on_disk = {os.path.abspath(fp) for fp in all_files}

    allowed_files = []
    scan_excluded = 0
    for fp in all_files:
        abs_fp = os.path.abspath(fp)
        if _excluded(abs_fp):
            scan_excluded += 1
            continue
        allowed_files.append(fp)

    new_files_data = []
    updated = 0
    unchanged = 0
    hashed_count = 0
    pruned_deleted = 0
    pruned_excluded = 0

    now = datetime.now(timezone.utc).isoformat()

    if progress_callback:
        progress_callback(0, len(allowed_files) if allowed_files else 1, "Checking file records to prune...")
    if cancel_event and cancel_event.is_set():
        return {"cancelled": True, "scan_type": "all_files"}

    def _mtime_matches(a, b) -> bool:
        try:
            return a is not None and b is not None and abs(float(a) - float(b)) < 0.001
        except Exception:
            return False

    with database.db.cursor() as cur:
        # file_mtime may not exist on very old DBs if migration hasn't run;
        # fall back gracefully.
        try:
            cur.execute("SELECT hash, path, file_size, file_mtime, file_type FROM files")
            db_records = cur.fetchall()
            has_mtime_col = True
        except Exception:
            cur.execute("SELECT hash, path, file_size, file_type FROM files")
            db_records = [(r[0], r[1], r[2], None, r[3]) for r in cur.fetchall()]
            has_mtime_col = False
        # rec: (hash, path, size, mtime, file_type)
        known_hashes = {rec[0]: rec[1] for rec in db_records}
        known_paths = {rec[1]: (rec[0], rec[2], rec[3]) for rec in db_records}
        known_types = {rec[0]: rec[4] for rec in db_records}
        db_paths = set(known_paths.keys())

        # Collect records to prune.
        # Existence is checked before exclusions so missing files short-circuit
        # to "deleted" without running the (heavier) exclusion matcher.
        to_prune: List[tuple[str, str]] = []
        if prune:
            # Global prune: check all DB records
            for rec in db_records:
                h, p = rec[0], rec[1]
                abs_p = os.path.abspath(p)
                if not os.path.exists(abs_p):
                    to_prune.append((h, "deleted"))
                    continue
                if _excluded(abs_p):
                    to_prune.append((h, "excluded"))
        else:
            # Global deletions (like original)
            candidates_deleted = db_paths - found_paths_on_disk
            for path in candidates_deleted:
                if not os.path.exists(path):
                    h = known_paths[path][0]
                    to_prune.append((h, "deleted"))

            # Local exclusions: only on paths found in scan (existing and in scan_paths)
            local_paths_to_check = db_paths & found_paths_on_disk
            for path in local_paths_to_check:
                if _excluded(path):
                    h = known_paths[path][0]
                    to_prune.append((h, "excluded"))

        # Perform deletions if any.
        # Chroma only stores image embeddings, so only image hashes are
        # removed from the vector store — non-image prunes skip it entirely.
        if to_prune:
            image_hashes = [h for h, _ in to_prune if known_types.get(h) == "image"]
            if image_hashes:
                with database.chroma_lock:
                    try:
                        database.chroma_coll.delete(ids=image_hashes)
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

        # Scan and process allowed files.
        # Fast path: single os.stat per file; skip sha256 when size+mtime match DB.
        # This makes incremental scans over large videos ~instant (stat only),
        # hashing only new/changed/moved files.
        total_allowed = len(allowed_files)
        iterable = tqdm(allowed_files, desc="Scanning files", disable=not verbose)
        pending_moved: list = []  # (abs_fp, h, size, mtime)
        for idx, fp in enumerate(iterable, 1):
            if cancel_event and cancel_event.is_set():
                if verbose:
                    print("File scan cancelled by user.")
                break
            if progress_callback and (idx % 5 == 0 or idx == total_allowed):
                progress_callback(
                    idx,
                    total_allowed,
                    f"Scanning file: {Path(fp).name} ({idx}/{total_allowed})",
                )
            try:
                abs_fp = os.path.abspath(fp)
                try:
                    st = os.stat(abs_fp)
                    file_size = st.st_size
                    file_mtime = st.st_mtime
                except (FileNotFoundError, IsADirectoryError, PermissionError, OSError) as e:
                    if verbose:
                        print(f"⚠️ Skipped file {fp}: {str(e)}")
                    continue
            except (FileNotFoundError, IsADirectoryError, PermissionError, OSError) as e:
                if verbose:
                    print(f"⚠️ Skipped file {fp}: {str(e)}")
                continue

            if abs_fp in known_paths:
                # Case 1: Path known — skip hashing when stat matches
                old_h, old_size, old_mtime = known_paths[abs_fp]
                if (
                    old_size is not None
                    and old_size == file_size
                    and _mtime_matches(old_mtime, file_mtime)
                ):
                    unchanged += 1
                    continue
                try:
                    # Larger chunks for big files = fewer read syscalls
                    chunk = (1 << 23) if file_size > (100 << 20) else (1 << 20)
                    h = sha256_file(abs_fp, chunk=chunk)
                    hashed_count += 1
                except (
                    FileNotFoundError,
                    IsADirectoryError,
                    PermissionError,
                    OSError,
                ) as e:
                    if verbose:
                        print(f"⚠️ Skipped file {fp}: {str(e)}")
                    continue
                if h != old_h:
                    if has_mtime_col:
                        cur.execute(
                            "UPDATE files SET hash=?, file_size=?, file_mtime=?, updated_at=? WHERE path=?",
                            (h, file_size, file_mtime, now, abs_fp),
                        )
                    else:
                        cur.execute(
                            "UPDATE files SET hash=?, file_size=?, updated_at=? WHERE path=?",
                            (h, file_size, now, abs_fp),
                        )
                    # keep in-memory maps consistent
                    known_hashes.pop(old_h, None)
                    known_paths[abs_fp] = (h, file_size, file_mtime)
                    known_hashes[h] = abs_fp
                    updated += 1
                else:
                    # Content identical, only stat drifted (e.g. touched) —
                    # backfill size/mtime so next scan skips hashing.
                    if has_mtime_col and not _mtime_matches(old_mtime, file_mtime):
                        try:
                            cur.execute(
                                "UPDATE files SET file_size=?, file_mtime=? WHERE path=?",
                                (file_size, file_mtime, abs_fp),
                            )
                        except Exception:
                            pass
                        known_paths[abs_fp] = (old_h, file_size, file_mtime)
                    unchanged += 1
            else:
                # Case 2/3: unknown path — need content hash to detect moves.
                # Defer DB writes; collect then resolve move vs new.
                try:
                    chunk = (1 << 23) if file_size > (100 << 20) else (1 << 20)
                    h = sha256_file(abs_fp, chunk=chunk)
                    hashed_count += 1
                except (
                    FileNotFoundError,
                    IsADirectoryError,
                    PermissionError,
                    OSError,
                ) as e:
                    if verbose:
                        print(f"⚠️ Skipped file {fp}: {str(e)}")
                    continue
                if h in known_hashes:
                    pending_moved.append((abs_fp, h, file_size, file_mtime))
                else:
                    file_type = get_file_type(abs_fp)
                    if has_mtime_col:
                        new_files_data.append((h, abs_fp, file_type, file_size, file_mtime, now, now))
                    else:
                        new_files_data.append((h, abs_fp, file_type, file_size, now, now))
                    known_hashes[h] = abs_fp
                    known_paths[abs_fp] = (h, file_size, file_mtime)

        # Resolve moved files (hash known, path new).
        # If the old path still exists on disk it's a duplicate copy, not a
        # move — fall through to a normal insert instead of clobbering.
        for abs_fp, h, file_size, file_mtime in pending_moved:
            old_path = known_hashes.get(h)
            if old_path is None or old_path == abs_fp:
                continue
            if old_path in known_paths and os.path.exists(old_path):
                file_type = get_file_type(abs_fp)
                if has_mtime_col:
                    new_files_data.append((h, abs_fp, file_type, file_size, file_mtime, now, now))
                else:
                    new_files_data.append((h, abs_fp, file_type, file_size, now, now))
                known_paths[abs_fp] = (h, file_size, file_mtime)
                # keep first-writer wins for hash->path; duplicates share hash
                continue
            if has_mtime_col:
                cur.execute(
                    "UPDATE files SET path=?, file_size=?, file_mtime=?, updated_at=? WHERE hash=?",
                    (abs_fp, file_size, file_mtime, now, h),
                )
            else:
                cur.execute(
                    "UPDATE files SET path=?, updated_at=? WHERE hash=?",
                    (abs_fp, now, h),
                )
            known_hashes[h] = abs_fp
            known_paths.pop(old_path, None)
            known_paths[abs_fp] = (h, file_size, file_mtime)
            updated += 1

        # Bulk insert new files (in-memory maps already guard duplicates,
        # so no per-file SELECT needed). INSERT OR IGNORE keeps it safe.
        inserted = 0
        if new_files_data:
            insert_sql = (
                "INSERT OR IGNORE INTO files (hash, path, file_type, file_size, file_mtime, added_at, updated_at) VALUES (?,?,?,?,?,?,?)"
                if has_mtime_col
                else "INSERT OR IGNORE INTO files (hash, path, file_type, file_size, added_at, updated_at) VALUES (?,?,?,?,?,?)"
            )
            try:
                cur.executemany(insert_sql, new_files_data)
                inserted = cur.rowcount if cur.rowcount and cur.rowcount > 0 else len(new_files_data)
                # rowcount is unreliable for executemany on some builds; recount cheaply
                if cur.rowcount is None or cur.rowcount < 0:
                    inserted = len(new_files_data)
            except Exception as e:
                if verbose:
                    print(f"⚠️ Bulk insert failed ({e}), attempting individual inserts.")
                for entry in new_files_data:
                    try:
                        cur.execute(insert_sql, entry)
                        if cur.rowcount != 0:
                            inserted += 1
                    except Exception as e2:
                        if verbose:
                            print(f"⚠️ Skipped insert for {entry[1]}: {e2}")

        if inserted and verbose:
            print(f"✓ Added {inserted} new file records.")
        if updated and verbose:
            print(f"✓ Updated {updated} moved or modified files.")
        if unchanged and verbose:
            print(f"✓ Skipped {unchanged} unchanged files (stat match, no hashing).")

    return {
        "scan_type": "all_files",
        "found": len(all_files),
        "new": inserted,
        "updated": updated,
        "deleted": pruned_deleted,
        "excluded": pruned_excluded + scan_excluded,
        "unchanged": unchanged,
        "hashed": hashed_count,
    }


if __name__ == "__main__":
    import argparse, pprint

    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", nargs="+", help="Paths to files/dirs to (re)scan")

    args = ap.parse_args()
    result = scan_files(args.scan, prune=True, verbose=True)
    print(result)
