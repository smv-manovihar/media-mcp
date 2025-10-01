import os
from tqdm import tqdm
from datetime import datetime, timezone
from typing import List, Dict
from utils import database
from helpers.helpers import sha256_file, list_all_files, get_file_type


def scan_files(scan_paths: List[str], silent: bool = False) -> Dict:
    """
    Scans for all files and updates the main 'files' table, correctly
    handling new, moved, and modified files.
    """
    all_files = list_all_files(scan_paths)
    found_paths_on_disk = set(map(os.path.abspath, all_files))

    new_files_data = []
    updated, deleted_count = 0, 0

    with database.db.cursor() as cur:
        # Fetch all records to create both hash-to-path and path-to-hash maps
        cur.execute("SELECT hash, path FROM files")
        db_records = cur.fetchall()
        known_hashes = {rec[0]: rec[1] for rec in db_records}
        known_paths = {rec[1]: rec[0] for rec in db_records}

        iterable = tqdm(all_files, desc="Scanning files", disable=silent)
        for fp in iterable:
            try:
                abs_fp = os.path.abspath(fp)
                h = sha256_file(fp)
            except (FileNotFoundError, IsADirectoryError):
                continue  # Skip files that are deleted mid-scan or are directories

            now = datetime.now(timezone.utc).isoformat()

            if abs_fp in known_paths:
                # Case 1: Path is known. Check if content (hash) has changed.
                if h != known_paths[abs_fp]:
                    # File was MODIFIED. Update the hash and metadata for this path.
                    cur.execute(
                        "UPDATE files SET hash=?, file_size=?, updated_at=? WHERE path=?",
                        (h, os.path.getsize(fp), now, abs_fp),
                    )
                    updated += 1
                # If hash is the same, do nothing.

            elif h in known_hashes:
                # Case 2: Path is new, but hash is known. File was MOVED.
                cur.execute(
                    "UPDATE files SET path=?, updated_at=? WHERE hash=?",
                    (abs_fp, now, h),
                )
                updated += 1

            else:
                # Case 3: Both path and hash are new. A truly NEW file.
                file_size = os.path.getsize(fp)
                file_type = get_file_type(fp)
                new_files_data.append((h, abs_fp, file_type, file_size, now, now))

        # --- Bulk insert all new files ---
        if new_files_data:
            cur.executemany(
                "INSERT INTO files (hash, path, file_type, file_size, added_at, updated_at) VALUES (?,?,?,?,?,?)",
                new_files_data,
            )
            if not silent:
                print(f"✓ Added {len(new_files_data)} new file records.")

        # --- Find and remove records for files that were deleted ---
        db_paths = set(known_paths.keys())
        paths_to_delete = db_paths - found_paths_on_disk

        if paths_to_delete:
            # We must also ensure the file truly doesn't exist, in case it's outside the scan path
            deleted_hashes = []
            for path in paths_to_delete:
                if not os.path.exists(path):
                    deleted_hashes.append(known_paths[path])

            if deleted_hashes:
                cur.executemany(
                    "DELETE FROM files WHERE hash=?", [(h,) for h in deleted_hashes]
                )
                deleted_count = len(deleted_hashes)
                if not silent:
                    print(f"✓ Removed {deleted_count} deleted file records.")

        if updated and not silent:
            print(f"✓ Updated {updated} moved or modified files.")

    return {
        "scan_type": "all_files",
        "found": len(all_files),
        "new": len(new_files_data),
        "updated": updated,
        "deleted": deleted_count,
    }


def main():
    import argparse, pprint, pathlib, tempfile

    parser = argparse.ArgumentParser(description="Scan files in specified directories.")
    parser.add_argument(
        "paths",
        nargs="*",
        default=[],
        help="One or more paths to scan. Defaults to a temporary test directory.",
    )
    parser.add_argument(
        "--silent",
        action="store_true",
        help="Run the scanner without progress bars or print statements.",
    )
    args = parser.parse_args()

    scan_paths = args.paths
    if not scan_paths:
        # If no paths are provided, create a temporary directory for demonstration
        print("No paths provided. Creating a temporary directory for testing...")
        temp_dir = tempfile.mkdtemp(prefix="scan_test_")
        pathlib.Path(temp_dir, "file1.txt").touch()
        pathlib.Path(temp_dir, "image.jpg").touch()
        sub_dir = pathlib.Path(temp_dir, "subdir")
        sub_dir.mkdir()
        pathlib.Path(sub_dir, "file2.txt").touch()
        print(f"Scanning temporary directory: {temp_dir}")
        scan_paths = [temp_dir]

    print("\nStarting file scan...")
    results = scan_files(scan_paths=scan_paths, silent=args.silent)

    print("\n--- Scan Complete ---")
    pprint.pprint(results)

    # Clean up temporary directory if it was created
    if not args.paths:
        import shutil

        print(f"\nCleaning up temporary directory: {scan_paths[0]}")
        shutil.rmtree(scan_paths[0])


if __name__ == "__main__":
    main()
