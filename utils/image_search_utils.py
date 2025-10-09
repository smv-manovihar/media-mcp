import os, torch, warnings
import numpy as np
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict

from PIL import Image
from tqdm import tqdm
from transformers import AutoProcessor, AutoModel
from transformers.utils import logging as hf_logging

from utils import exif_utils, database
from helpers.helpers import sha256_file, list_images
from config.settings import load_config, should_exclude


# --- Constants ---
MODEL_NAME = "google/siglip-base-patch16-224"
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
BASE_DIR = Path(__file__).parent.parent
MODEL_CACHE_DIR = BASE_DIR / "models"
MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Suppress progress bars and warnings
os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
hf_logging.set_verbosity_error()
warnings.filterwarnings("ignore", message=".*slow image processor.*")


def load_model():
    """Load model into project-local cache dir"""
    processor = AutoProcessor.from_pretrained(
        MODEL_NAME, cache_dir=str(MODEL_CACHE_DIR)
    )
    model = (
        AutoModel.from_pretrained(MODEL_NAME, cache_dir=str(MODEL_CACHE_DIR))
        .to(DEVICE)
        .eval()
    )
    return processor, model


processor, model = load_model()


def embed(paths: List[str], batch: int = 32) -> np.ndarray:
    embs = []
    for i in range(0, len(paths), batch):
        imgs = [Image.open(p).convert("RGB") for p in paths[i : i + batch]]
        inputs = processor(images=imgs, return_tensors="pt").to(DEVICE)
        with torch.no_grad():
            vec = model.get_image_features(**inputs).cpu().numpy()
        embs.append(vec)
        for im in imgs:
            try:
                im.close()
            except Exception:
                pass
    if embs:
        return np.vstack(embs)
    hidden = getattr(model, "config", None)
    hidden_size = getattr(hidden, "hidden_size", 0) if hidden else 0
    return np.zeros((0, hidden_size))


def scan_images(
    scan_paths: List[str], verbose: bool = False, prune: bool = False
) -> Dict:
    """
    Scan images under the provided scan_paths.

    - verbose: If True, shows progress bars and prints status messages.
    - prune: If True, performs a global prune of the database, removing image records that are deleted or now excluded. Defaults to False.
    """
    if not scan_paths and not prune:
        return {
            "success": True,
            "total_media_count": 0,
            "new_media_count": 0,
            "updated_media_count": 0,
            "deleted_media_count": 0,
            "skipped_media_count": 0,
            "excluded_media_count": 0,
        }

    config = load_config()
    found = list_images(scan_paths) if scan_paths else []

    allowed = []
    excluded_count = 0
    for fp in found:
        try:
            if should_exclude(Path(fp), config):
                excluded_count += 1
                continue
        except Exception:
            excluded_count += 1
            continue
        allowed.append(fp)

    new_paths, new_hashes, new_metadata = [], [], []
    updated, skipped_for_exif_error, reindexed_count = 0, 0, 0
    pruned_deleted, pruned_excluded = 0, 0

    with database.db.cursor() as cur:
        if prune:
            if verbose:
                print("Performing global prune of image records...")
            cur.execute("SELECT hash, path FROM files WHERE file_type = 'image'")
            rows_for_prune = cur.fetchall()

            to_prune = []
            for h, p in rows_for_prune:
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
                hashes_to_delete = list(set([h for h, _ in to_prune]))
                with database.chroma_lock:
                    database.chroma_coll.delete(ids=hashes_to_delete)

                cur.executemany(
                    "DELETE FROM files WHERE hash=?", [(h,) for h in hashes_to_delete]
                )
                pruned_deleted = sum(1 for _, reason in to_prune if reason == "deleted")
                pruned_excluded = sum(
                    1 for _, reason in to_prune if reason == "excluded"
                )
                if verbose:
                    print(
                        f"✓ Pruned {len(hashes_to_delete)} records (deleted: {pruned_deleted}, excluded: {pruned_excluded})"
                    )

        cur.execute("SELECT hash, path, id FROM files WHERE file_type = 'image'")
        known_rows = cur.fetchall()
        known = {row[0]: (row[1], row[2]) for row in known_rows}

        iterable = tqdm(allowed, desc="Scanning images", disable=not verbose)
        for fp in iterable:
            try:
                h = sha256_file(fp)
                abs_fp = os.path.abspath(fp)
            except (FileNotFoundError, IsADirectoryError):
                continue

            if h in known:
                if abs_fp != known[h][0]:
                    cur.execute(
                        "UPDATE files SET path=?, updated_at=? WHERE hash=?",
                        (abs_fp, datetime.now(timezone.utc).isoformat(), h),
                    )
                    updated += 1
            else:
                new_paths.append(fp)
                new_hashes.append(h)
                metadata = exif_utils.get_exif_data(fp)
                if "error" in metadata:
                    skipped_for_exif_error += 1
                    if verbose:
                        print(f"Warning: Could not read EXIF for {fp}.")
                    new_metadata.append({})
                else:
                    new_metadata.append(metadata)

        if new_paths:
            if verbose:
                print(f"Embedding {len(new_paths)} new images...")
            vecs = embed(new_paths)
            if getattr(vecs, "shape", (0,))[0] != 0:
                with database.chroma_lock:
                    database.chroma_coll.add(ids=new_hashes, embeddings=vecs)
            now = datetime.now(timezone.utc).isoformat()
            for h, p, meta in zip(new_hashes, new_paths, new_metadata):
                size = os.path.getsize(p)
                cur.execute(
                    "INSERT INTO files (hash, path, file_type, file_size, added_at, updated_at) VALUES (?, ?, 'image', ?, ?, ?)",
                    (h, os.path.abspath(p), size, now, now),
                )
                file_id = cur.lastrowid
                cur.execute(
                    """INSERT INTO images (file_id, make, model, software,
                    width, height, orientation, datetime_original, datetime_digitized,
                    exposure_time, f_number, iso, focal_length, flash,
                    latitude, longitude, altitude, gps_timestamp,
                    location_display_name, location_country, location_state, location_city, location_postcode)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        file_id,
                        meta.get("make"),
                        meta.get("model"),
                        meta.get("software"),
                        meta.get("width"),
                        meta.get("height"),
                        meta.get("orientation"),
                        meta.get("datetime_original"),
                        meta.get("datetime_digitized"),
                        meta.get("exposure_time"),
                        meta.get("f_number"),
                        meta.get("iso"),
                        meta.get("focal_length"),
                        meta.get("flash"),
                        meta.get("latitude"),
                        meta.get("longitude"),
                        meta.get("altitude"),
                        meta.get("gps_timestamp"),
                        meta.get("location_display_name"),
                        meta.get("location_country"),
                        meta.get("location_state"),
                        meta.get("location_city"),
                        meta.get("location_postcode"),
                    ),
                )
            if verbose:
                print(f"✓ Added {len(new_paths)} new images")

        if updated and verbose:
            print(f"✓ Updated {updated} moved images")

        cur.execute("SELECT file_id FROM images")
        existing_image_file_ids = set(r[0] for r in cur.fetchall())
        missing_file_rows = [
            (h, os.path.abspath(path), file_id)
            for h, (path, file_id) in known.items()
            if file_id not in existing_image_file_ids and os.path.exists(path)
        ]

        if missing_file_rows:
            if verbose:
                print(f"Found {len(missing_file_rows)} images to re-index...")
            reindex_paths = [row[1] for row in missing_file_rows]
            reindex_hashes = [row[0] for row in missing_file_rows]
            reindex_metadata = []
            for p in reindex_paths:
                metadata = exif_utils.get_exif_data(p)
                if "error" in metadata:
                    skipped_for_exif_error += 1
                    if verbose:
                        print(f"Warning: Could not read EXIF for {p} during re-index.")
                    reindex_metadata.append({})
                else:
                    reindex_metadata.append(metadata)

            vecs = embed(reindex_paths)
            if getattr(vecs, "shape", (0,))[0] != 0:
                with database.chroma_lock:
                    database.chroma_coll.add(ids=reindex_hashes, embeddings=vecs)

            for (h, p, file_id), meta in zip(missing_file_rows, reindex_metadata):
                cur.execute(
                    """INSERT INTO images (file_id, make, model, software,
                    width, height, orientation, datetime_original, datetime_digitized,
                    exposure_time, f_number, iso, focal_length, flash,
                    latitude, longitude, altitude, gps_timestamp,
                    location_display_name, location_country, location_state, location_city, location_postcode)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        file_id,
                        meta.get("make"),
                        meta.get("model"),
                        meta.get("software"),
                        meta.get("width"),
                        meta.get("height"),
                        meta.get("orientation"),
                        meta.get("datetime_original"),
                        meta.get("datetime_digitized"),
                        meta.get("exposure_time"),
                        meta.get("f_number"),
                        meta.get("iso"),
                        meta.get("focal_length"),
                        meta.get("flash"),
                        meta.get("latitude"),
                        meta.get("longitude"),
                        meta.get("altitude"),
                        meta.get("gps_timestamp"),
                        meta.get("location_display_name"),
                        meta.get("location_country"),
                        meta.get("location_state"),
                        meta.get("location_city"),
                        meta.get("location_postcode"),
                    ),
                )
                reindexed_count += 1
            if verbose:
                print(f"✓ Re-indexed {reindexed_count} images.")

    pruned_total = pruned_deleted + pruned_excluded
    return {
        "success": True,
        "total_media_count": len(found),
        "new_media_count": len(new_paths) + reindexed_count,
        "updated_media_count": updated,
        "deleted_media_count": pruned_total,
        "skipped_media_count": skipped_for_exif_error,
        "excluded_media_count": excluded_count,
    }


def search_by_text(query: str, top_k: int = 5):
    inputs = processor(text=[query], return_tensors="pt", padding="max_length").to(
        DEVICE
    )
    with torch.no_grad():
        vec = model.get_text_features(**inputs).cpu().numpy()
    with database.chroma_lock:
        res = database.chroma_coll.query(query_embeddings=vec, n_results=top_k)
    ids, distances = res.get("ids", [[]])[0], res.get("distances", [[]])[0]
    if not ids:
        return []
    placeholders = ",".join(["?"] * len(ids))
    with database.db.cursor() as cur:
        cur.execute(
            f"SELECT f.hash, f.path, i.make, i.model, i.width, i.height, i.latitude, i.longitude, i.location_city, i.location_country "
            + f"FROM files f JOIN images i ON f.id = i.file_id "
            + f"WHERE f.hash IN ({placeholders})",
            ids,
        )
        rows = cur.fetchall()
    rows_dict = {row[0]: row for row in rows}
    return [
        {
            "hash": h,
            "distance": dist,
            "path": rows_dict.get(h)[1],
            "make": rows_dict.get(h)[2],
            "model": rows_dict.get(h)[3],
            "resolution": (
                f"{rows_dict.get(h)[4]}x{rows_dict.get(h)[5]}"
                if rows_dict.get(h)[4] and rows_dict.get(h)[5]
                else None
            ),
            "city": rows_dict.get(h)[8],
            "country": rows_dict.get(h)[9],
        }
        for h, dist in zip(ids, distances)
        if h in rows_dict
    ]


def search_by_image(path: str, top_k: int = 5):
    vec = embed([path])
    if vec.shape[0] == 0:
        return []
    with database.chroma_lock:
        res = database.chroma_coll.query(query_embeddings=vec, n_results=top_k)
    ids, distances = res.get("ids", [[]])[0], res.get("distances", [[]])[0]
    if not ids:
        return []
    placeholders = ",".join(["?"] * len(ids))
    with database.db.cursor() as cur:
        cur.execute(
            f"SELECT f.hash, f.path, i.make, i.model, i.width, i.height, i.latitude, i.longitude, i.location_city, i.location_country "
            + f"FROM files f JOIN images i ON f.id = i.file_id "
            + f"WHERE f.hash IN ({placeholders})",
            ids,
        )
        rows = cur.fetchall()
    rows_dict = {row[0]: row for row in rows}
    return [
        {
            "hash": h,
            "distance": dist,
            "path": rows_dict.get(h)[1],
            "make": rows_dict.get(h)[2],
            "model": rows_dict.get(h)[3],
            "resolution": (
                f"{rows_dict.get(h)[4]}x{rows_dict.get(h)[5]}"
                if rows_dict.get(h)[4] and rows_dict.get(h)[5]
                else None
            ),
            "city": rows_dict.get(h)[8],
            "country": rows_dict.get(h)[9],
        }
        for h, dist in zip(ids, distances)
        if h in rows_dict
    ]


def query_by_metadata(
    make: str = None,
    model: str = None,
    country: str = None,
    city: str = None,
    min_width: int = None,
    min_height: int = None,
    has_gps: bool = None,
    page: int = 1,
    page_size: int = 10,
) -> List[Dict]:
    conditions, params = [], []
    if make:
        conditions.append("LOWER(i.make) LIKE ?")
        params.append(f"%{make.lower()}%")
    if model:
        conditions.append("LOWER(i.model) LIKE ?")
        params.append(f"%{model.lower()}%")
    if country:
        conditions.append("LOWER(i.location_country) LIKE ?")
        params.append(f"%{country.lower()}%")
    if city:
        conditions.append("LOWER(i.location_city) LIKE ?")
        params.append(f"%{city.lower()}%")
    if min_width:
        conditions.append("i.width >= ?")
        params.append(min_width)
    if min_height:
        conditions.append("i.height >= ?")
        params.append(min_height)
    if has_gps is not None:
        conditions.append(
            "i.latitude IS NOT NULL AND i.longitude IS NOT NULL"
            if has_gps
            else "i.latitude IS NULL OR i.longitude IS NULL"
        )
    where_clause = " AND ".join(conditions) if conditions else "1=1"
    with database.db.cursor() as cur:
        cur.execute(
            f"SELECT f.hash, f.path, i.make, i.model, i.width, i.height, i.latitude, i.longitude, i.location_city, i.location_country, i.datetime_original "
            + f"FROM files f JOIN images i ON f.id = i.file_id "
            + f"WHERE {where_clause} LIMIT {page_size} OFFSET {(page - 1) * page_size}",
            params,
        )
        rows = cur.fetchall()
    return [
        {
            "path": r[1],
            "make": r[2],
            "model": r[3],
            "resolution": f"{r[4]}x{r[5]}" if r[4] and r[5] else None,
            "city": r[8],
            "country": r[9],
            "datetime": r[10],
        }
        for r in rows
    ]


if __name__ == "__main__":
    import argparse, pprint

    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", nargs="+", help="Paths to files/dirs to (re)scan")
    ap.add_argument("--text", help="Text query")
    ap.add_argument("--image", help="Image query path")
    ap.add_argument("--query-make", help="Query by camera make")
    ap.add_argument("--query-country", help="Query by country")
    ap.add_argument("--query-city", help="Query by city")
    args = ap.parse_args()
    if args.scan:
        scan_images(args.scan, prune=True, verbose=True)
    if args.text:
        print("\n🔍 Text search results:")
        pprint.pp(search_by_text(args.text))
    if args.image:
        print("\n🔍 Image search results:")
        pprint.pp(search_by_image(args.image))
    if args.query_make or args.query_country or args.query_city:
        print("\n📊 Metadata query results:")
        pprint.pp(
            query_by_metadata(
                make=args.query_make, country=args.query_country, city=args.query_city
            )
        )
