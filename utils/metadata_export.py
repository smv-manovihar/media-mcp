"""Lightweight CSV export helpers for indexed image metadata.

Kept torch-free so Streamlit sidebar can import it without loading SigLIP.

Memory-safe design: exports stream the DB in small OFFSET/LIMIT chunks and
write incrementally, so exporting the full table never loads all rows at
once. ``limit`` is optional (None = all rows).
"""
import csv
import io
import os
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

from utils import database

EXPORT_COLUMNS = [
    "path",
    "hash",
    "file_size",
    "make",
    "model",
    "software",
    "width",
    "height",
    "orientation",
    "datetime_original",
    "datetime_digitized",
    "exposure_time",
    "f_number",
    "iso",
    "focal_length",
    "flash",
    "latitude",
    "longitude",
    "altitude",
    "gps_timestamp",
    "location_display_name",
    "location_country",
    "location_state",
    "location_city",
    "location_postcode",
]

_SELECT_COLS = (
    "f.path, f.hash, f.file_size, "
    "i.make, i.model, i.software, i.width, i.height, i.orientation, "
    "i.datetime_original, i.datetime_digitized, i.exposure_time, i.f_number, "
    "i.iso, i.focal_length, i.flash, i.latitude, i.longitude, i.altitude, "
    "i.gps_timestamp, i.location_display_name, i.location_country, "
    "i.location_state, i.location_city, i.location_postcode"
)

_DEFAULT_CHUNK_SIZE = 2000


def _normalize_limit(limit: Optional[int]) -> Optional[int]:
    """None/blank means no limit; otherwise require a positive int."""
    if limit is None:
        return None
    if isinstance(limit, str):
        limit = limit.strip()
        if not limit:
            return None
    try:
        n = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be a positive integer or empty, got {limit!r}")
    if n <= 0:
        raise ValueError(f"limit must be a positive integer or empty, got {limit!r}")
    return n


def _normalize_chunk_size(chunk_size: int) -> int:
    try:
        n = int(chunk_size or _DEFAULT_CHUNK_SIZE)
    except (TypeError, ValueError):
        n = _DEFAULT_CHUNK_SIZE
    return max(100, min(n, 10000))


def _metadata_where_clause(
    make: str = None,
    model: str = None,
    country: str = None,
    city: str = None,
    min_width: int = None,
    min_height: int = None,
    has_gps: bool = None,
):
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
    return (" AND ".join(conditions) if conditions else "1=1"), params


def count_images_metadata(
    make: str = None,
    model: str = None,
    country: str = None,
    city: str = None,
    min_width: int = None,
    min_height: int = None,
    has_gps: bool = None,
) -> int:
    """Cheap COUNT(*) for the current filter set (used for progress + previews)."""
    where_clause, params = _metadata_where_clause(
        make=make, model=model, country=country, city=city,
        min_width=min_width, min_height=min_height, has_gps=has_gps,
    )
    with database.db.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM files f JOIN images i ON f.id = i.file_id "
            f"WHERE {where_clause}",
            params,
        )
        row = cur.fetchone()
    return int(row[0]) if row else 0


def iter_images_metadata(
    make: str = None,
    model: str = None,
    country: str = None,
    city: str = None,
    min_width: int = None,
    min_height: int = None,
    has_gps: bool = None,
    limit: Optional[int] = None,
    chunk_size: int = _DEFAULT_CHUNK_SIZE,
) -> Iterator[Dict]:
    """Yield metadata row dicts in stable path order, chunk by chunk.

    Only one chunk (~chunk_size rows) is ever held in memory.
    """
    limit = _normalize_limit(limit)
    chunk_size = _normalize_chunk_size(chunk_size)
    where_clause, params = _metadata_where_clause(
        make=make, model=model, country=country, city=city,
        min_width=min_width, min_height=min_height, has_gps=has_gps,
    )
    fetched = 0
    offset = 0
    while True:
        if limit is not None:
            remaining = limit - fetched
            if remaining <= 0:
                break
            take = min(chunk_size, remaining)
        else:
            take = chunk_size
        with database.db.cursor() as cur:
            cur.execute(
                f"SELECT {_SELECT_COLS} "
                "FROM files f JOIN images i ON f.id = i.file_id "
                f"WHERE {where_clause} ORDER BY f.path ASC "
                f"LIMIT {take} OFFSET {offset}",
                params,
            )
            rows = cur.fetchall()
        if not rows:
            break
        for r in rows:
            yield dict(zip(EXPORT_COLUMNS, r))
        fetched += len(rows)
        offset += len(rows)
        if len(rows) < take:
            break


def fetch_images_metadata(
    make: str = None,
    model: str = None,
    country: str = None,
    city: str = None,
    min_width: int = None,
    min_height: int = None,
    has_gps: bool = None,
    limit: Optional[int] = None,
) -> List[Dict]:
    """Collect filtered rows into a list (convenience for small result sets).

    For full-table exports prefer the streaming writers below to avoid
    holding everything in memory.
    """
    return list(
        iter_images_metadata(
            make=make, model=model, country=country, city=city,
            min_width=min_width, min_height=min_height, has_gps=has_gps,
            limit=limit, chunk_size=_DEFAULT_CHUNK_SIZE,
        )
    )


def images_metadata_to_csv(rows: List[Dict]) -> str:
    """Convert an in-memory row list to a CSV string (header + rows)."""
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k) for k in EXPORT_COLUMNS})
    return buf.getvalue()


def write_images_metadata_csv(
    file_obj: Any,
    make: str = None,
    model: str = None,
    country: str = None,
    city: str = None,
    min_width: int = None,
    min_height: int = None,
    has_gps: bool = None,
    limit: Optional[int] = None,
    chunk_size: int = _DEFAULT_CHUNK_SIZE,
    progress_callback: Optional[Callable[[int, Optional[int]], None]] = None,
) -> int:
    """Stream filtered rows into an open text file object. Returns row count."""
    chunk_size = _normalize_chunk_size(chunk_size)
    total: Optional[int] = None
    if progress_callback is not None:
        try:
            total = count_images_metadata(
                make=make, model=model, country=country, city=city,
                min_width=min_width, min_height=min_height, has_gps=has_gps,
            )
            if limit is not None:
                total = min(total, limit)
        except Exception:
            total = None
    writer = csv.DictWriter(file_obj, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    count = 0
    for row in iter_images_metadata(
        make=make, model=model, country=country, city=city,
        min_width=min_width, min_height=min_height, has_gps=has_gps,
        limit=limit, chunk_size=chunk_size,
    ):
        writer.writerow({k: row.get(k) for k in EXPORT_COLUMNS})
        count += 1
        if progress_callback is not None and count % chunk_size == 0:
            try:
                progress_callback(count, total)
            except Exception:
                pass
    if progress_callback is not None:
        try:
            progress_callback(count, total)
        except Exception:
            pass
    return count


def export_images_metadata_to_path(
    output_path: str,
    make: str = None,
    model: str = None,
    country: str = None,
    city: str = None,
    min_width: int = None,
    min_height: int = None,
    has_gps: bool = None,
    limit: Optional[int] = None,
    chunk_size: int = _DEFAULT_CHUNK_SIZE,
    progress_callback: Optional[Callable[[int, Optional[int]], None]] = None,
) -> Dict:
    """Stream filtered metadata to output_path as CSV (atomic, memory-safe).

    Writes to a sibling ``.tmp`` file first, then atomically replaces the
    target so a crash/cancel never leaves a half-written CSV behind.
    Raises on failure (after cleaning up the temp file); the MCP layer
    converts that to an ``{"error": ...}`` dict.
    """
    limit = _normalize_limit(limit)
    out = Path(output_path).expanduser()
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise OSError(f"Cannot create output directory '{out.parent}': {e}")
    tmp = out.parent / (out.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            row_count = write_images_metadata_csv(
                f, make=make, model=model, country=country, city=city,
                min_width=min_width, min_height=min_height, has_gps=has_gps,
                limit=limit, chunk_size=chunk_size,
                progress_callback=progress_callback,
            )
            try:
                f.flush()
                os.fsync(f.fileno())
            except Exception:
                pass
        os.replace(tmp, out)
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        raise
    return {"success": True, "path": str(out), "row_count": row_count}
