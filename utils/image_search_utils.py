import os, glob, hashlib, sqlite3, threading, torch, chromadb
import warnings
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict
from PIL import Image
import numpy as np
from tqdm import tqdm
from transformers import AutoProcessor, AutoModel
from transformers.utils import logging as hf_logging
from . import exif_utils

MODEL_NAME = "google/siglip-base-patch16-224"
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

BASE_DIR = Path(__file__).parent.parent
DB_DIR = BASE_DIR / "database"
MODEL_CACHE_DIR = BASE_DIR / "models"

CHROMADB_PATH = DB_DIR / "chroma_store"
SQLITE_PATH = str(DB_DIR / "files.sqlite")

DB_DIR.mkdir(parents=True, exist_ok=True)
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

chroma_client = chromadb.PersistentClient(path=str(CHROMADB_PATH))
chroma_coll = chroma_client.get_or_create_collection(
    name="siglip_images", embedding_function=None
)

chroma_lock = threading.Lock()


class SQLiteDB:
    def __init__(self, path: str, timeout: int = 30):
        self.path = path
        self.timeout = timeout
        self.local = threading.local()

    def _create_conn(self):
        conn = sqlite3.connect(self.path, timeout=self.timeout)
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def get_conn(self):
        conn = getattr(self.local, "conn", None)
        if conn is None:
            conn = self._create_conn()
            self.local.conn = conn
        return conn

    @contextmanager
    def cursor(self):
        con = self.get_conn()
        cur = con.cursor()
        try:
            yield cur
            con.commit()
        finally:
            try:
                cur.close()
            except Exception:
                pass

    def close_thread_conn(self):
        conn = getattr(self.local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self.local.conn = None


db = SQLiteDB(SQLITE_PATH)

with db.cursor() as cur:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS images (
            hash TEXT PRIMARY KEY,
            path TEXT,
            added_at TEXT,
            updated_at TEXT,
            
            -- Device/Camera Information
            make TEXT,
            model TEXT,
            software TEXT,
            
            -- Image Properties
            width INTEGER,
            height INTEGER,
            orientation INTEGER,
            datetime_original TEXT,
            datetime_digitized TEXT,
            
            -- Camera Settings
            exposure_time REAL,
            f_number REAL,
            iso INTEGER,
            focal_length REAL,
            flash INTEGER,
            
            -- GPS Coordinates (stored as REAL for efficient queries)
            latitude REAL,
            longitude REAL,
            altitude REAL,
            gps_timestamp TEXT,
            
            -- Reverse Geocoded Location Data
            location_display_name TEXT,
            location_country TEXT,
            location_state TEXT,
            location_city TEXT,
            location_postcode TEXT
        )"""
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_path ON images(path)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_make_model ON images(make, model)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_datetime ON images(datetime_original)")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_location ON images(latitude, longitude)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_country_city ON images(location_country, location_city)"
    )


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


def incremental_scan(scan_paths: List[str]) -> None:
    found = list_images(scan_paths)
    with db.cursor() as cur:
        cur.execute("SELECT hash, path FROM images")
        known: Dict[str, str] = dict(cur.fetchall())

        new_paths, new_hashes, new_metadata = [], [], []
        updated = 0

        for fp in tqdm(found, desc="Scanning images"):
            h = sha256_file(fp)
            abs_fp = os.path.abspath(fp)

            if h in known:
                if abs_fp != known[h]:
                    cur.execute(
                        "UPDATE images SET path=?, updated_at=? WHERE hash=?",
                        (abs_fp, datetime.utcnow().isoformat(), h),
                    )
                    updated += 1
            else:
                new_paths.append(fp)
                new_hashes.append(h)
                metadata = exif_utils.get_exif_data(fp)
                if "error" in metadata:
                    print(
                        f"Warning: Could not read EXIF for {fp}. Indexing without metadata."
                    )
                    new_metadata.append({})
                else:
                    new_metadata.append(metadata)

        if new_paths:
            print(f"Embedding {len(new_paths)} new images...")
            vecs = embed(new_paths)
            if vecs.shape[0] != 0:
                with chroma_lock:
                    chroma_coll.add(ids=new_hashes, embeddings=vecs)

            now = datetime.now(timezone.utc).isoformat()

            cur.executemany(
                """INSERT INTO images VALUES 
                (?,?,?,?, ?,?,?, ?,?,?,?,?, ?,?,?,?,?, ?,?,?,?, ?,?,?,?,?)""",
                [
                    (
                        h,
                        os.path.abspath(p),
                        now,
                        now,
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
                    )
                    for h, p, meta in zip(new_hashes, new_paths, new_metadata)
                ],
            )
            print(f"✓ Added {len(new_paths)} new images")

        existing_files = set(map(os.path.abspath, found))
        cur.execute("SELECT hash, path FROM images")
        deletions = []
        for h, p in cur.fetchall():
            if p not in existing_files and not os.path.exists(p):
                deletions.append(h)

        if deletions:
            with chroma_lock:
                chroma_coll.delete(ids=deletions)
            cur.executemany(
                "DELETE FROM images WHERE hash=?", [(h,) for h in deletions]
            )
            print(f"✓ Removed {len(deletions)} deleted images")

        if updated:
            print(f"✓ Updated {updated} moved images")


def incremental_scan_silent(scan_paths: List[str]):
    found = list_images(scan_paths)
    new_paths, new_hashes, new_metadata = [], [], []
    updated = 0
    deletions = []
    skipped_for_exif_error = 0

    with db.cursor() as cur:
        cur.execute("SELECT hash, path FROM images")
        known: Dict[str, str] = dict(cur.fetchall())

        for fp in found:
            h = sha256_file(fp)
            abs_fp = os.path.abspath(fp)
            if h in known:
                if abs_fp != known[h]:
                    cur.execute(
                        "UPDATE images SET path=?, updated_at=? WHERE hash=?",
                        (abs_fp, datetime.utcnow().isoformat(), h),
                    )
                    updated += 1
            else:
                new_paths.append(fp)
                new_hashes.append(h)
                metadata = exif_utils.get_exif_data(fp)
                if "error" in metadata:
                    skipped_for_exif_error += 1
                    new_metadata.append({})
                else:
                    new_metadata.append(metadata)

        if new_paths:
            vecs = embed(new_paths)
            if vecs.shape[0] != 0:
                with chroma_lock:
                    # MODIFICATION START: Add all new images to ChromaDB
                    chroma_coll.add(ids=new_hashes, embeddings=vecs)
                    # MODIFICATION END

            now = datetime.now(timezone.utc).isoformat()
            # MODIFICATION START: Insert all new images to SQLite
            cur.executemany(
                """INSERT INTO images VALUES 
                (?,?,?,?, ?,?,?, ?,?,?,?,?, ?,?,?,?,?, ?,?,?,?, ?,?,?,?,?)""",
                [
                    (
                        h,
                        os.path.abspath(p),
                        now,
                        now,
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
                    )
                    for h, p, meta in zip(new_hashes, new_paths, new_metadata)
                ],
            )

        existing_files = set(map(os.path.abspath, found))
        cur.execute("SELECT hash, path FROM images")
        for h, p in cur.fetchall():
            if p not in existing_files and not os.path.exists(p):
                deletions.append(h)

        if deletions:
            with chroma_lock:
                chroma_coll.delete(ids=deletions)
            cur.executemany(
                "DELETE FROM images WHERE hash=?", [(h,) for h in deletions]
            )

    return {
        "success": True,
        "total_media_count": len(found),
        "new_media_count": len(new_paths),
        "updated_media_count": updated,
        "deleted_media_count": len(deletions),
        "skipped_media_count": skipped_for_exif_error,
    }


def search_by_text(query: str, top_k: int = 5):
    inputs = processor(text=[query], return_tensors="pt", padding="max_length").to(
        DEVICE
    )
    with torch.no_grad():
        vec = model.get_text_features(**inputs).cpu().numpy()
    with chroma_lock:
        res = chroma_coll.query(query_embeddings=vec, n_results=top_k)
    ids = res.get("ids", [[]])[0]
    distances = res.get("distances", [[]])[0]
    if not ids:
        return []
    placeholders = ",".join(["?"] * len(ids))
    with db.cursor() as cur:
        cur.execute(
            f"""SELECT hash, path, make, model, width, height, 
            latitude, longitude, location_city, location_country 
            FROM images WHERE hash IN ({placeholders})""",
            ids,
        )
        rows = cur.fetchall()

    results = []
    # Reorder SQL results to match ChromaDB distance order
    rows_dict = {row[0]: row for row in rows}
    for h, dist in zip(ids, distances):
        row = rows_dict.get(h)
        if row:
            results.append(
                {
                    "hash": row[0],
                    "distance": dist,
                    "path": row[1],
                    "make": row[2],
                    "model": row[3],
                    "resolution": f"{row[4]}x{row[5]}" if row[4] and row[5] else None,
                    "latitude": row[6],
                    "longitude": row[7],
                    "city": row[8],
                    "country": row[9],
                }
            )
    return results


def search_by_image(path: str, top_k: int = 5):
    vec = embed([path])
    if vec.shape[0] == 0:
        return []
    with chroma_lock:
        res = chroma_coll.query(query_embeddings=vec, n_results=top_k)
    ids = res.get("ids", [[]])[0]
    distances = res.get("distances", [[]])[0]
    if not ids:
        return []
    placeholders = ",".join(["?"] * len(ids))
    with db.cursor() as cur:
        cur.execute(
            f"""SELECT hash, path, make, model, width, height, 
            latitude, longitude, location_city, location_country 
            FROM images WHERE hash IN ({placeholders})""",
            ids,
        )
        rows = cur.fetchall()

    results = []
    # Reorder SQL results to match ChromaDB distance order
    rows_dict = {row[0]: row for row in rows}
    for h, dist in zip(ids, distances):
        row = rows_dict.get(h)
        if row:
            results.append(
                {
                    "hash": row[0],
                    "distance": dist,
                    "path": row[1],
                    "make": row[2],
                    "model": row[3],
                    "resolution": f"{row[4]}x{row[5]}" if row[4] and row[5] else None,
                    "latitude": row[6],
                    "longitude": row[7],
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
) -> List[Dict]:
    """Query images by EXIF metadata"""
    conditions = []
    params = []

    if make:
        conditions.append("make LIKE ?")
        params.append(f"%{make}%")
    if model:
        conditions.append("model LIKE ?")
        params.append(f"%{model}%")
    if country:
        conditions.append("location_country LIKE ?")
        params.append(f"%{country}%")
    if city:
        conditions.append("location_city LIKE ?")
        params.append(f"%{city}%")
    if min_width:
        conditions.append("width >= ?")
        params.append(min_width)
    if min_height:
        conditions.append("height >= ?")
        params.append(min_height)
    if has_gps is not None:
        if has_gps:
            conditions.append("latitude IS NOT NULL AND longitude IS NOT NULL")
        else:
            conditions.append("latitude IS NULL OR longitude IS NULL")

    where_clause = " AND ".join(conditions) if conditions else "1=1"

    with db.cursor() as cur:
        cur.execute(
            f"""SELECT hash, path, make, model, width, height, 
            latitude, longitude, location_city, location_country, datetime_original
            FROM images WHERE {where_clause} LIMIT 100""",
            params,
        )
        rows = cur.fetchall()

    results = []
    for row in rows:
        results.append(
            {
                "hash": row[0],
                "path": row[1],
                "make": row[2],
                "model": row[3],
                "resolution": f"{row[4]}x{row[5]}" if row[4] and row[5] else None,
                "latitude": row[6],
                "longitude": row[7],
                "city": row[8],
                "country": row[9],
                "datetime": row[10],
            }
        )

    return results


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
        incremental_scan(args.scan)
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
