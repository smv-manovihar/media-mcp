import os, torch, warnings
import numpy as np
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any

from PIL import Image
from tqdm import tqdm
from transformers import AutoProcessor, AutoModel
from transformers.utils import logging as hf_logging

from utils import exif_utils, database
from helpers.helpers import sha256_file, list_images, get_file_type
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


import threading

_model_lock = threading.Lock()
_processor = None
_model = None


def load_model():
    """Load model into project-local cache dir"""
    proc = AutoProcessor.from_pretrained(
        MODEL_NAME, cache_dir=str(MODEL_CACHE_DIR)
    )
    mod = (
        AutoModel.from_pretrained(MODEL_NAME, cache_dir=str(MODEL_CACHE_DIR))
        .to(DEVICE)
        .eval()
    )
    return proc, mod


def get_model_and_processor():
    """Lazily load SigLIP model and processor in a thread-safe manner."""
    global _processor, _model
    if _processor is None or _model is None:
        with _model_lock:
            if _processor is None or _model is None:
                _processor, _model = load_model()
    return _processor, _model


def __getattr__(name):
    if name in ("processor", "model"):
        p, m = get_model_and_processor()
        return p if name == "processor" else m
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


def _extract_tensor(output: Any) -> torch.Tensor:
    """Safely extracts raw feature tensor whether transformers returns a Tensor or BaseModelOutputWithPooling."""
    if isinstance(output, torch.Tensor):
        return output
    if hasattr(output, "pooler_output") and output.pooler_output is not None:
        return output.pooler_output
    if hasattr(output, "last_hidden_state") and output.last_hidden_state is not None:
        return output.last_hidden_state[:, 0]
    if isinstance(output, dict):
        if output.get("pooler_output") is not None:
            return output["pooler_output"]
        if output.get("last_hidden_state") is not None:
            return output["last_hidden_state"][:, 0]
    if isinstance(output, (tuple, list)) and len(output) > 1 and isinstance(output[1], torch.Tensor):
        return output[1]
    if isinstance(output, (tuple, list)) and len(output) > 0 and isinstance(output[0], torch.Tensor):
        return output[0]
    return output


def embed(paths: List[str], batch: int = 32) -> np.ndarray:
    embs = []
    for i in range(0, len(paths), batch):
        imgs = []
        for p in paths[i : i + batch]:
            img = Image.open(p)
            # Convert palette images with transparency to RGBA to avoid PIL warnings
            if img.mode == "P" and "transparency" in img.info:
                img = img.convert("RGBA")
            img = img.convert("RGB")
            imgs.append(img)
        proc, mod = get_model_and_processor()
        inputs = proc(images=imgs, return_tensors="pt").to(DEVICE)
        with torch.no_grad():
            output = mod.get_image_features(**inputs)
            tensor = _extract_tensor(output)
            vec = tensor.cpu().numpy()
            # L2-normalize vectors for cosine similarity
            norms = np.maximum(np.linalg.norm(vec, axis=-1, keepdims=True), 1e-12)
            vec = vec / norms
        embs.append(vec)
        for im in imgs:
            try:
                im.close()
            except Exception:
                pass
    if embs:
        return np.vstack(embs)
    _, mod = get_model_and_processor()
    hidden = getattr(mod, "config", None)
    hidden_size = (
        getattr(hidden, "hidden_size", None)
        or getattr(getattr(hidden, "vision_config", None), "hidden_size", 768)
        or 768
    )
    return np.zeros((0, hidden_size))


def scan_images(
    scan_paths: List[str],
    verbose: bool = False,
    prune: bool = False,
    progress_callback: Any = None,
    cancel_event: Any = None,
) -> Dict:
    """
    Scan images under the provided scan_paths, updating the database with metadata and embeddings.

    Args:
        scan_paths: List of file or directory paths to scan for images.
        verbose: If True, shows progress bars and prints status messages.
        prune: If True, performs a global prune of the database, removing image records that are deleted or now excluded.

    Returns:
        Dict with scan results: success, counts for total, new, updated, deleted, skipped, and excluded images.
    """
    if scan_paths is None:
        scan_paths = []
    scan_paths_abs = [os.path.abspath(p) for p in scan_paths]
    config = load_config()

    if not scan_paths_abs and not prune:
        return {
            "success": True,
            "total_media_count": 0,
            "new_media_count": 0,
            "updated_media_count": 0,
            "deleted_media_count": 0,
            "skipped_media_count": 0,
            "excluded_media_count": 0,
        }

    all_images = list_images(scan_paths_abs) if scan_paths_abs else []
    found_paths_on_disk = {os.path.abspath(fp) for fp in all_images}

    allowed_images = []
    excluded_count = 0
    for fp in all_images:
        abs_fp = os.path.abspath(fp)
        try:
            if should_exclude(Path(abs_fp), config):
                excluded_count += 1
                continue
        except Exception as e:
            if verbose:
                print(f"⚠️ Skipped image {fp}: Exclusion check failed - {str(e)}")
            excluded_count += 1
            continue
        allowed_images.append(fp)

    new_paths, new_hashes, new_metadata = [], [], []
    updated = 0
    skipped_for_error = 0
    reindexed_count = 0
    pruned_deleted = 0
    pruned_excluded = 0

    now = datetime.now(timezone.utc).isoformat()

    if progress_callback:
        progress_callback(0, len(allowed_images) if allowed_images else 1, "Checking records to prune...")
    if cancel_event and cancel_event.is_set():
        return {"success": False, "cancelled": True}

    with database.db.cursor() as cur:
        # Fetch all image records once
        cur.execute("SELECT hash, path, id FROM files WHERE file_type = 'image'")
        db_records = cur.fetchall()
        known_hashes = {rec[0]: (rec[1], rec[2]) for rec in db_records}
        known_paths = {rec[1]: (rec[0], rec[2]) for rec in db_records}
        db_paths = set(known_paths.keys())

        # Pruning: global deletions + local/global exclusions
        to_prune: List[tuple[str, str]] = []
        if prune:
            # Global prune: check all DB records
            for h, (p, file_id) in known_hashes.items():
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
            candidates_deleted = db_paths - found_paths_on_disk
            for path in candidates_deleted:
                if not os.path.exists(path):
                    h = known_paths[path][0]
                    to_prune.append((h, "deleted"))

            # Local exclusions: only on paths found in scan
            local_paths_to_check = db_paths & found_paths_on_disk
            for path in local_paths_to_check:
                try:
                    if should_exclude(Path(path), config):
                        h = known_paths[path][0]
                        to_prune.append((h, "excluded"))
                except Exception:
                    h = known_paths[path][0]
                    to_prune.append((h, "excluded"))

        if to_prune:
            hashes_to_delete = list(set(h for h, _ in to_prune))
            with database.chroma_lock:
                try:
                    database.chroma_coll.delete(ids=hashes_to_delete)
                except Exception as e:
                    if verbose:
                        print(f"⚠️ Warning: Failed to delete vectors from Chroma: {e}")
            try:
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
            except Exception as e:
                if verbose:
                    print(f"⚠️ Error during pruning: {e}")
                return {"success": False, "error": f"Pruning failed: {str(e)}"}

        # Update in-memory known maps
        for h, _ in to_prune:
            p, _ = known_hashes.pop(h, (None, None))
            if p:
                known_paths.pop(p, None)

        # Scan allowed images
        total_allowed = len(allowed_images)
        iterable = tqdm(allowed_images, desc="Scanning images", disable=not verbose)
        for idx, fp in enumerate(iterable, 1):
            if cancel_event and cancel_event.is_set():
                if verbose:
                    print("Scan cancelled by user.")
                break
            if progress_callback and (idx % 3 == 0 or idx == total_allowed):
                progress_callback(
                    idx,
                    total_allowed + (len(new_paths) if new_paths else 1),
                    f"Scanning metadata: {Path(fp).name} ({idx}/{total_allowed})",
                )
            try:
                abs_fp = os.path.abspath(fp)
                h = sha256_file(fp)
                file_size = os.path.getsize(fp)
            except (
                FileNotFoundError,
                IsADirectoryError,
                PermissionError,
                OSError,
            ) as e:
                if verbose:
                    print(f"⚠️ Skipped image {fp}: {str(e)}")
                skipped_for_error += 1
                continue

            try:
                metadata = exif_utils.get_exif_data(fp)
                if "error" in metadata:
                    if verbose:
                        print(f"⚠️ Skipped image {fp}: EXIF read error")
                    skipped_for_error += 1
                    metadata = {}  # Proceed with empty metadata
            except Exception as e:
                if verbose:
                    print(f"⚠️ Skipped image {fp}: EXIF processing error - {str(e)}")
                skipped_for_error += 1
                metadata = {}
                continue

            if abs_fp in known_paths:
                # Case 1: Path known, check if modified
                known_hash, file_id = known_paths[abs_fp]
                if h != known_hash:
                    try:
                        cur.execute(
                            "UPDATE files SET hash=?, file_size=?, updated_at=? WHERE path=?",
                            (h, file_size, now, abs_fp),
                        )
                        # Update images table with new metadata
                        cur.execute("DELETE FROM images WHERE file_id=?", (file_id,))
                        cur.execute(
                            """INSERT INTO images (file_id, make, model, software,
                            width, height, orientation, datetime_original, datetime_digitized,
                            exposure_time, f_number, iso, focal_length, flash,
                            latitude, longitude, altitude, gps_timestamp,
                            location_display_name, location_country, location_state, location_city, location_postcode)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                file_id,
                                metadata.get("make"),
                                metadata.get("model"),
                                metadata.get("software"),
                                metadata.get("width"),
                                metadata.get("height"),
                                metadata.get("orientation"),
                                metadata.get("datetime_original"),
                                metadata.get("datetime_digitized"),
                                metadata.get("exposure_time"),
                                metadata.get("f_number"),
                                metadata.get("iso"),
                                metadata.get("focal_length"),
                                metadata.get("flash"),
                                metadata.get("latitude"),
                                metadata.get("longitude"),
                                metadata.get("altitude"),
                                metadata.get("gps_timestamp"),
                                metadata.get("location_display_name"),
                                metadata.get("location_country"),
                                metadata.get("location_state"),
                                metadata.get("location_city"),
                                metadata.get("location_postcode"),
                            ),
                        )
                        updated += 1
                    except Exception as e:
                        if verbose:
                            print(f"⚠️ Failed to update image {fp}: {str(e)}")
                        skipped_for_error += 1
                        continue
            elif h in known_hashes:
                # Case 2: Hash known, file moved
                try:
                    cur.execute(
                        "UPDATE files SET path=?, updated_at=? WHERE hash=?",
                        (abs_fp, now, h),
                    )
                    updated += 1
                except Exception as e:
                    if verbose:
                        print(f"⚠️ Failed to update moved image {fp}: {str(e)}")
                    skipped_for_error += 1
                    continue
            else:
                # Case 3: New image
                new_paths.append(fp)
                new_hashes.append(h)
                new_metadata.append(metadata)

        # Bulk insert new images
        if new_paths:
            total_new = len(new_paths)
            if progress_callback:
                progress_callback(
                    total_allowed,
                    total_allowed + total_new,
                    f"Generating SigLIP embeddings for {total_new} new images...",
                )
            try:
                if verbose:
                    print(f"Embedding {len(new_paths)} new images...")
                vecs = embed(new_paths)
                if getattr(vecs, "shape", (0,))[0] != len(new_paths):
                    if verbose:
                        print(f"⚠️ Embedding failed for some or all new images")
                    skipped_for_error += len(new_paths)
                else:
                    with database.chroma_lock:
                        try:
                            # Deduplicate in case identical images exist in the batch
                            unique_ids = []
                            unique_vecs = []
                            seen_ids = set()
                            for uh, uv in zip(new_hashes, vecs):
                                if uh not in seen_ids:
                                    seen_ids.add(uh)
                                    unique_ids.append(uh)
                                    unique_vecs.append(uv)
                            if unique_ids:
                                database.chroma_coll.upsert(
                                    ids=unique_ids, embeddings=np.array(unique_vecs)
                                )
                        except Exception as e:
                            if verbose:
                                print(f"⚠️ Failed to add embeddings to Chroma: {e}")
                            skipped_for_error += len(new_paths)
                            new_paths.clear()
                            new_hashes.clear()
                            new_metadata.clear()
                    for h, p, meta in zip(new_hashes, new_paths, new_metadata):
                        try:
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
                        except Exception as e:
                            if verbose:
                                print(f"⚠️ Failed to insert image {p}: {str(e)}")
                            skipped_for_error += 1
                            continue
                    if verbose:
                        print(f"✓ Added {len(new_paths)} new images")
            except Exception as e:
                if verbose:
                    print(f"⚠️ Error embedding new images: {e}")
                skipped_for_error += len(new_paths)
                new_paths.clear()
                new_hashes.clear()
                new_metadata.clear()

        if updated and verbose:
            print(f"✓ Updated {updated} moved or modified images")

        # Reindex images missing from images table
        cur.execute("SELECT file_id FROM images")
        existing_image_file_ids = set(r[0] for r in cur.fetchall())
        missing_file_rows = [
            (h, os.path.abspath(path), file_id)
            for h, (path, file_id) in known_hashes.items()
            if file_id not in existing_image_file_ids and os.path.exists(path)
        ]

        if missing_file_rows:
            try:
                if verbose:
                    print(f"Found {len(missing_file_rows)} images to re-index...")
                reindex_paths = []
                reindex_hashes = []
                reindex_file_ids = []
                reindex_metadata = []
                for h, p, file_id in missing_file_rows:
                    # Verify still an image
                    if get_file_type(p) != "image":
                        if verbose:
                            print(f"⚠️ Skipped reindexing {p}: Not an image")
                        continue
                    try:
                        metadata = exif_utils.get_exif_data(p)
                        if "error" in metadata:
                            if verbose:
                                print(f"⚠️ Skipped reindexing {p}: EXIF read error")
                            skipped_for_error += 1
                            metadata = {}
                        reindex_paths.append(p)
                        reindex_hashes.append(h)
                        reindex_file_ids.append(file_id)
                        reindex_metadata.append(metadata)
                    except Exception as e:
                        if verbose:
                            print(f"⚠️ Skipped reindexing {p}: EXIF error - {str(e)}")
                        skipped_for_error += 1
                        continue

                if reindex_paths:
                    vecs = embed(reindex_paths)
                    if getattr(vecs, "shape", (0,))[0] != len(reindex_paths):
                        if verbose:
                            print(f"⚠️ Embedding failed for some re-indexed images")
                        skipped_for_error += len(reindex_paths)
                    else:
                        with database.chroma_lock:
                            try:
                                unique_ids = []
                                unique_vecs = []
                                seen_ids = set()
                                for uh, uv in zip(reindex_hashes, vecs):
                                    if uh not in seen_ids:
                                        seen_ids.add(uh)
                                        unique_ids.append(uh)
                                        unique_vecs.append(uv)
                                if unique_ids:
                                    database.chroma_coll.upsert(
                                        ids=unique_ids, embeddings=np.array(unique_vecs)
                                    )
                            except Exception as e:
                                if verbose:
                                    print(f"⚠️ Failed to add reindex embeddings: {e}")
                                skipped_for_error += len(reindex_paths)
                                reindex_paths.clear()
                                reindex_hashes.clear()
                                reindex_file_ids.clear()
                                reindex_metadata.clear()
                        for p, h, file_id, meta in zip(
                            reindex_paths,
                            reindex_hashes,
                            reindex_file_ids,
                            reindex_metadata,
                        ):
                            try:
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
                            except Exception as e:
                                if verbose:
                                    print(f"⚠️ Failed to reindex image {p}: {str(e)}")
                                skipped_for_error += 1
                                continue
                        if verbose:
                            print(f"✓ Re-indexed {reindexed_count} images")
            except Exception as e:
                if verbose:
                    print(f"⚠️ Error during reindexing: {e}")
                skipped_for_error += len(missing_file_rows)

    return {
        "success": True,
        "total_media_count": len(all_images),
        "new_media_count": len(new_paths),
        "updated_media_count": updated,
        "deleted_media_count": pruned_deleted,
        "skipped_media_count": skipped_for_error,
        "excluded_media_count": excluded_count + pruned_excluded,
    }


def search_by_text(query: str, top_k: int = 5, min_score: float = None):
    proc, mod = get_model_and_processor()
    inputs = proc(text=[query], return_tensors="pt", padding="max_length").to(
        DEVICE
    )
    with torch.no_grad():
        output = mod.get_text_features(**inputs)
        tensor = _extract_tensor(output)
        vec = tensor.cpu().numpy()
        # L2-normalize query vector
        norms = np.maximum(np.linalg.norm(vec, axis=-1, keepdims=True), 1e-12)
        vec = vec / norms

    with database.chroma_lock:
        res = database.chroma_coll.query(query_embeddings=vec, n_results=top_k)
    ids, distances = res.get("ids", [[]])[0], res.get("distances", [[]])[0]
    if not ids:
        return []
    placeholders = ",".join(["?"] * len(ids))
    with database.db.cursor() as cur:
        cur.execute(
            f"SELECT f.hash, f.path, i.make, i.model, i.width, i.height, i.latitude, i.longitude, i.location_city, i.location_country "
            + f"FROM files f LEFT JOIN images i ON f.id = i.file_id "
            + f"WHERE f.hash IN ({placeholders})",
            ids,
        )
        rows = cur.fetchall()
    rows_dict = {row[0]: row for row in rows}

    results = []
    for h, dist in zip(ids, distances):
        if h in rows_dict:
            similarity = round(float(1.0 - dist), 4)
            if min_score is not None and similarity < min_score:
                continue
            row = rows_dict[h]
            results.append(
                {
                    "hash": h,
                    "distance": round(float(dist), 4),
                    "similarity": similarity,
                    "path": row[1],
                    "make": row[2],
                    "model": row[3],
                    "resolution": (
                        f"{row[4]}x{row[5]}"
                        if row[4] and row[5]
                        else None
                    ),
                    "city": row[8],
                    "country": row[9],
                }
            )
    return results


def search_by_image(path: str, top_k: int = 5, min_score: float = None):
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
            + f"FROM files f LEFT JOIN images i ON f.id = i.file_id "
            + f"WHERE f.hash IN ({placeholders})",
            ids,
        )
        rows = cur.fetchall()
    rows_dict = {row[0]: row for row in rows}

    results = []
    for h, dist in zip(ids, distances):
        if h in rows_dict:
            similarity = round(float(1.0 - dist), 4)
            if min_score is not None and similarity < min_score:
                continue
            row = rows_dict[h]
            results.append(
                {
                    "hash": h,
                    "distance": round(float(dist), 4),
                    "similarity": similarity,
                    "path": row[1],
                    "make": row[2],
                    "model": row[3],
                    "resolution": (
                        f"{row[4]}x{row[5]}"
                        if row[4] and row[5]
                        else None
                    ),
                    "city": row[8],
                    "country": row[9],
                }
            )
    return results


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
