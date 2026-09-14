import os
import shutil
from pathlib import Path
from datetime import datetime, timezone
from typing import Union, List, Dict, Any
from mcp.server.fastmcp import FastMCP
from config.settings import load_config
from utils import database
from helpers import helpers
import utils.image_search_utils as image_utils
import utils.fileops_utils as file_utils
from utils.universal_parser import UniversalParser
from utils.deduplication import clean_duplicate_uploads

mcp = FastMCP("file_management", port=8000)

config = load_config(verbose=True)

print("Allowed paths:")
for p in config.user_allowed_paths:
    print(f"- [Dir] {p}")
    p.mkdir(exist_ok=True)

print("Media indexed paths:")
for p in config.user_media_index_allowed_paths:
    print(f"- [Dir] {p}")


def safe_path(path: Union[str, Path]) -> Path:
    """
    Resolves a path and ensures it is within one of the ALLOWED_PATHS.
    """
    if isinstance(path, str):
        resolved_path = Path(path).expanduser().absolute()
    else:
        resolved_path = (
            path.expanduser().absolute()
            if hasattr(path, "expanduser")
            else path.absolute()
        )
    norm_resolved = os.path.normcase(str(resolved_path))
    for p in config["allowed_paths"]:
        norm_p = os.path.normcase(str(p))
        norm_sep = os.path.normcase(os.sep)
        prefix = norm_p
        if not prefix.endswith(norm_sep):
            prefix += norm_sep
        if norm_resolved == norm_p or norm_resolved.startswith(prefix):
            return resolved_path
    raise PermissionError(
        f"Access denied: {resolved_path} is not within any allowed sandbox directory. Please check your Allowed paths for file indexing configuration."
    )


def safe_paths(paths: Union[str, List[str]]) -> List[Path]:
    """
    Resolves multiple paths and ensures they are within allowed directories.
    """
    if isinstance(paths, str):
        return [safe_path(paths)]
    return [safe_path(p) for p in paths]


@mcp.tool("allowed_paths")
def allowed_paths():
    """
    Returns the list of allowed directories (and subdirs) for operations, plus media-indexed paths.
    Returns: Dict with 'allowed_paths' and 'media_indexed_paths' as lists of strings.
    """
    return {
        "allowed_paths": [str(p) for p in config.allowed_paths],
        "media_indexed_paths": [str(p) for p in config.media_index_allowed_paths],
    }


@mcp.tool("current_directory")
def current_directory():
    """
    Returns the primary working directory (first allowed path) as a dict with 'path' key.
    Returns error dict if not in allowed paths.
    """
    try:
        cwd = safe_path(str(Path.cwd()))
        return {"path": str(cwd)}
    except Exception:
        return {"error": "Current directory is not within the allowed paths."}


@mcp.tool("list_directory")
def list_directory(
    path: str,
    full_path: bool = False,
    page: int = 1,
    page_size: int = 30,
    extension: str = None,
    file_type: str = None,
):
    """
    Lists directory contents with pagination and optional filters to protect LLM context.
    Args:
    - path (str, required): Directory path.
    - full_path (bool, optional): Return absolute paths (default: False, relative names).
    - page (int, optional): Page number starting at 1 (default: 1).
    - page_size (int, optional): Max items per page, 1-50 (default: 30).
    - extension (str, optional): Filter by file extension (e.g. '.jpg', 'png', '.txt').
    - file_type (str, optional): Filter by 'file' or 'directory'.
    Returns: Dict with 'items' list, 'page', 'page_size', 'total_items', 'total_pages', 'has_more'.
    """
    try:
        base = safe_path(path)
        if not base.is_dir():
            return {"error": f"'{path}' is not a valid directory"}

        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 50))

        all_entries = []
        for p in sorted(base.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            is_d = p.is_dir()
            t = "directory" if is_d else "file"
            if file_type and file_type.lower() != t:
                continue
            if extension and not is_d:
                ext = extension if extension.startswith(".") else f".{extension}"
                if p.suffix.lower() != ext.lower():
                    continue
            all_entries.append(
                {
                    "name": str(p if full_path else p.name),
                    "type": t,
                }
            )

        total_items = len(all_entries)
        total_pages = max(1, (total_items + page_size - 1) // page_size)
        start_idx = (page - 1) * page_size
        items = all_entries[start_idx : start_idx + page_size]

        return {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total_items": total_items,
            "total_pages": total_pages,
            "has_more": page < total_pages,
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool("create_directory")
def create_directory(path: Union[str, List[str]], full_path: bool = False):
    """
    Creates directory(ies), including parents. Supports batch via list (max 50).
    Args:
    - path (str|List[str], required): Path(s) to create.
    - full_path (bool, optional): Return absolute paths in failed (default: False).
    Returns: {"success": True} on full success, else {"success_count": int, "failed": list of dicts {'path': str, 'error': str}} or {'error': str}.
    """
    try:
        dir_paths = safe_paths(path)
        if len(dir_paths) > 50:
            return {"error": f"Batch limit exceeded ({len(dir_paths)} items). Max 50 items per call."}

        failed = []
        for dir_path in dir_paths:
            try:
                dir_path.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                failed.append(
                    {
                        "path": str(dir_path if full_path else dir_path.name),
                        "error": str(e),
                    }
                )
        if not failed:
            return {"success": True}
        else:
            success_count = len(dir_paths) - len(failed)
            return {"success_count": success_count, "failed": failed}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool("read_file")
def read_file(
    path: Union[str, List[str]],
    full_path: bool = False,
    skip_chars: int = 0,
    max_chars: int = 2000,
    page: int = 1,
) -> Dict[str, Any]:
    """
    Reads file(s) contents cleanly using the Universal Parser.
    Supports PDF, DOCX, XLSX, PPTX, CSV, IPYNB, Code, Text, Archives, and Media metadata.
    Strictly bounded by max_chars to protect LLM context.
    Args:
    - path (str|List[str], required): File path(s). Max 5 files per call.
    - full_path (bool, optional): Return absolute paths (default: False).
    - skip_chars (int, optional): Skip first n chars (default: 0).
    - max_chars (int, optional): Max chars per file, capped at 4000 (default: 2000).
    - page (int, optional): Page number for paginated documents like PDF (default: 1).
    Returns: Dict with 'results' list of dicts {'path': str, 'format': str, 'content': str, 'is_truncated': bool, 'total_bytes': int}.
    """
    try:
        max_chars = max(100, min(int(max_chars), 4000))
        skip_chars = max(0, int(skip_chars))
        page = max(1, int(page))
        file_paths = safe_paths(path)
        if len(file_paths) > 5:
            return {"error": f"Too many files requested ({len(file_paths)}). Max 5 files per call to prevent context overflow."}

        results = []
        for file_path in file_paths:
            if not file_path.is_file():
                results.append({"error": f"'{file_path.name}' is not a valid file"})
                continue
            try:
                parsed = UniversalParser.parse_file(
                    file_path, max_chars=max_chars, skip_chars=skip_chars, page=page
                )
                if not full_path:
                    parsed["path"] = file_path.name
                else:
                    parsed["path"] = str(file_path)
                results.append(parsed)
            except Exception as e:
                results.append({"path": file_path.name, "error": str(e)})
        return {"results": results}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool("write_file")
def write_file(
    path: str,
    content: str,
    append: bool = False,
    overwrite: bool = False,
    full_path: bool = False,
):
    """
    Writes text to file. Requires append or overwrite if file exists.
    Args:
    - path (str, required): File path.
    - content (str, required): Text to write.
    - append (bool, optional): Append if True (default: False).
    - overwrite (bool, optional): Overwrite if True (default: False).
    - full_path (bool, optional): Return absolute path in error (default: False).
    Returns: {"success": True} or {'error': str}.
    """
    try:
        file_path = safe_path(path)
        if (not overwrite and not append) and file_path.exists():
            return {
                "error": f"File already exists at {str(file_path if full_path else file_path.name)}. Use overwrite=True or append=True."
            }

        file_path.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"

        # --- DB UPDATE: Delete old record before writing new content ---
        if file_path.exists():
            with database.db.cursor() as cur:
                cur.execute("DELETE FROM files WHERE path=?", (str(file_path),))

        with file_path.open(mode, encoding="utf-8") as f:
            f.write(content)

        # --- DB UPDATE: Re-scan the single file to add it back with the new hash ---
        file_type = helpers.get_file_type(str(file_path))
        if file_type == "image":
            image_utils.scan_images([str(file_path)])
        else:
            file_utils.scan_files([str(file_path)])
        return {"success": True}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool("delete")
def delete(
    path: Union[str, List[str]], recursive: bool = False, full_path: bool = False
):
    """
    Deletes file(s)/dir(s). For dirs: recursive=True deletes contents (irreversible).
    Args:
    - path (str|List[str], required): Path(s) to delete.
    - recursive (bool, optional): Delete non-empty dirs (default: False, only empty dirs).
    - full_path (bool, optional): Return absolute paths in failed (default: False).
    Returns: {"success": True} on full success, else {"success_count": int, "failed": list of dicts {'path': str, 'error': str}} or {'error': str}.
    """
    try:
        paths_to_delete = safe_paths(path)
        failed = []
        for p in paths_to_delete:
            try:
                if not p.exists():
                    failed.append(
                        {
                            "path": str(p if full_path else p.name),
                            "error": "Path not found",
                        }
                    )
                    continue

                # --- DB UPDATE: Find records to delete before the filesystem operation ---
                path_pattern = str(p) + ("%" if p.is_dir() else "")
                with database.db.cursor() as cur:
                    cur.execute(
                        "SELECT hash, file_type FROM files WHERE path LIKE ?",
                        (path_pattern,),
                    )
                    records_to_delete = cur.fetchall()

                # Filesystem operation
                if p.is_file():
                    p.unlink()
                elif p.is_dir():
                    if recursive:
                        shutil.rmtree(p)
                    else:
                        p.rmdir()
                else:  # Broken symlinks etc.
                    p.unlink()

                # --- DB UPDATE: Perform deletion using the hashes found earlier ---
                if records_to_delete:
                    hashes_to_delete = [rec[0] for rec in records_to_delete]
                    image_hashes = [
                        rec[0] for rec in records_to_delete if rec[1] == "image"
                    ]

                    if image_hashes:
                        with database.chroma_lock:
                            database.chroma_coll.delete(ids=image_hashes)

                    with database.db.cursor() as cur:
                        cur.executemany(
                            "DELETE FROM files WHERE hash=?",
                            [(h,) for h in hashes_to_delete],
                        )

            except OSError as e:
                error_msg = str(e)
                if "Directory not empty" in error_msg and not recursive:
                    error_msg = (
                        f"Directory is not empty. Use recursive=True to delete it."
                    )
                failed.append(
                    {"path": str(p if full_path else p.name), "error": error_msg}
                )
            except Exception as e:
                failed.append(
                    {"path": str(p if full_path else p.name), "error": str(e)}
                )
        if not failed:
            return {"success": True}
        else:
            success_count = len(paths_to_delete) - len(failed)
            return {"success_count": success_count, "failed": failed}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool("copy_file")
def copy_file(
    src: Union[str, List[str]], dest: Union[str, Path], full_path: bool = False
):
    """
    Copies file(s) to dest. For multiple src, dest must be dir.
    Args:
    - src (str|List[str], required): Source file path(s).
    - dest (str|Path, required): Destination path or dir.
    - full_path (bool, optional): Return absolute paths in failed (default: False).
    Returns: {"success": True} on full success, else {"success_count": int, "failed": list of dicts {'src': str, 'error': str}} or {'error': str}.
    """
    try:
        src_paths = safe_paths(src)
        dest_path = safe_path(dest)
        if len(src_paths) > 1:
            dest_path.mkdir(parents=True, exist_ok=True)

        failed = []
        for src_path in src_paths:
            if not src_path.is_file():
                failed.append(
                    {
                        "src": str(src_path if full_path else src_path.name),
                        "error": "Not a file",
                    }
                )
                continue
            try:
                final_dest = (
                    dest_path / src_path.name if dest_path.is_dir() else dest_path
                )
                final_dest.parent.mkdir(parents=True, exist_ok=True)

                # 1. Perform the filesystem copy
                shutil.copy2(src_path, final_dest)

                # 2. Re-scan the new file to update the database
                file_type = helpers.get_file_type(str(final_dest))
                if file_type == "image":
                    image_utils.scan_images([str(final_dest)])
                else:
                    file_utils.scan_files([str(final_dest)])
            except Exception as e:
                failed.append(
                    {
                        "src": str(src_path if full_path else src_path.name),
                        "error": str(e),
                    }
                )
        if not failed:
            return {"success": True}
        else:
            success_count = len(src_paths) - len(failed)
            return {"success_count": success_count, "failed": failed}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool("move")
def move(src: Union[str, List[str]], dest: Union[str, Path], full_path: bool = False):
    """
    Moves/renames file(s)/dir(s). For multiple src, dest must be dir.
    Args:
    - src (str|List[str], required): Source path(s).
    - dest (str|Path, required): Destination path or dir.
    - full_path (bool, optional): Return absolute paths in failed (default: False).
    Returns: {"success": True} on full success, else {"success_count": int, "failed": list of dicts {'src': str, 'error': str}} or {'error': str}.
    """
    try:
        failed = []
        src_paths, dest_path = safe_paths(src), safe_path(dest)

        if len(src_paths) > 1:
            if dest_path.exists() and not dest_path.is_dir():
                return {
                    "error": f"Destination '{dest_path}' must be a directory for multiple sources."
                }
            dest_path.mkdir(parents=True, exist_ok=True)

        for src_path in src_paths:
            if not src_path.exists():
                failed.append(
                    {
                        "src": str(src_path if full_path else src_path.name),
                        "error": "Source does not exist.",
                    }
                )
                continue
            try:
                final_dest = (
                    dest_path / src_path.name if dest_path.is_dir() else dest_path
                )

                # Filesystem operation
                shutil.move(src_path, final_dest)

                # --- DB UPDATE ---
                with database.db.cursor() as cur:
                    # Update all records that start with the old source path (for directory moves)
                    src_pattern = str(src_path) + ("%" if src_path.is_dir() else "")
                    cur.execute(
                        "SELECT path FROM files WHERE path LIKE ?", (src_pattern,)
                    )
                    paths_to_update = cur.fetchall()

                    for old_path_tuple in paths_to_update:
                        old_path = Path(old_path_tuple[0])
                        new_path = str(final_dest / old_path.relative_to(src_path))
                        cur.execute(
                            "UPDATE files SET path=?, updated_at=? WHERE path=?",
                            (
                                new_path,
                                datetime.now(timezone.utc).isoformat(),
                                str(old_path),
                            ),
                        )

            except Exception as e:
                failed.append(
                    {
                        "src": str(src_path if full_path else src_path.name),
                        "error": str(e),
                    }
                )
        if not failed:
            return {"success": True}
        else:
            success_count = len(src_paths) - len(failed)
            return {"success_count": success_count, "failed": failed}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool("batch_move")
def batch_move(moves: List[Dict[str, str]], full_path: bool = False):
    """
    Batch moves files/dirs to individual dests.
    Args:
    - moves (List[Dict[str, str]], required): List of {'src': str, 'dest': str}.
    - full_path (bool, optional): Return absolute paths in failed (default: False).
    Returns: {"success": True} on full success, else {"success_count": int, "failed": list of dicts {'src': str, 'error': str}} or {'error': str}.
    """
    try:
        failed = []
        for item in moves:
            item_src, item_dest = item.get("src"), item.get("dest")
            if not item_src or not item_dest:
                failed.append({"error": "Missing 'src' or 'dest' in a batch item."})
                continue
            try:
                src_path, dest_path = safe_path(item_src), safe_path(item_dest)
                if not src_path.exists():
                    failed.append(
                        {
                            "src": str(src_path if full_path else src_path.name),
                            "error": "Source does not exist.",
                        }
                    )
                    continue

                dest_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(src_path, dest_path)

                # --- DB UPDATE ---
                with database.db.cursor() as cur:
                    # Update all records that start with the old source path (for directory moves)
                    src_pattern = str(src_path) + ("%" if src_path.is_dir() else "")
                    cur.execute(
                        "SELECT path FROM files WHERE path LIKE ?", (src_pattern,)
                    )
                    paths_to_update = cur.fetchall()

                    for old_path_tuple in paths_to_update:
                        old_path = Path(old_path_tuple[0])
                        new_path = str(dest_path / old_path.relative_to(src_path))
                        cur.execute(
                            "UPDATE files SET path=?, updated_at=? WHERE path=?",
                            (
                                new_path,
                                datetime.now(timezone.utc).isoformat(),
                                str(old_path),
                            ),
                        )

            except Exception as e:
                failed.append({"src": item_src, "error": str(e)})
        if not failed:
            return {"success": True}
        else:
            success_count = len(moves) - len(failed)
            return {"success_count": success_count, "failed": failed}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool("get_file_info")
def get_file_info(path: Union[str, List[str]], full_path: bool = False):
    """
    Gets metadata for file(s)/dir(s): size, modified/created times, type. Max 25 paths per call.
    Args:
    - path (str|List[str], required): Path(s) to query.
    - full_path (bool, optional): Return absolute paths (default: False).
    Returns: Dict with 'results' list of dicts {'path': str, 'is_file': bool, 'is_dir': bool, 'size_bytes': int, 'modified': str, 'created': str} or {'error': str}.
    """
    try:
        file_paths = safe_paths(path)
        if len(file_paths) > 25:
            return {"error": f"Too many paths requested ({len(file_paths)}). Max 25 per call."}

        results = []
        for file_path in file_paths:
            if not file_path.exists():
                results.append({"error": f"{file_path} does not exist"})
                continue
            try:
                stat = file_path.stat()
                results.append(
                    {
                        "path": str(file_path if full_path else file_path.name),
                        "is_file": file_path.is_file(),
                        "is_dir": file_path.is_dir(),
                        "size_bytes": stat.st_size,
                        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        "created": datetime.fromtimestamp(stat.st_ctime).isoformat(),
                    }
                )
            except Exception as e:
                results.append({"error": str(e)})
        return {"results": results}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool("search_files")
def search_files(
    path: str,
    name: str = None,
    extension: str = None,
    recursive: bool = True,
    full_path: bool = False,
    page: int = 1,
    page_size: int = 20,
):
    """
    Searches indexed files with filters and pagination to protect LLM context.
    Args:
    - path (str, required): Base directory path.
    - name (str, optional): Substring in filename.
    - extension (str, optional): File extension (e.g. '.jpg', 'png', '.txt').
    - recursive (bool, optional): Search subdirectories (default: True).
    - full_path (bool, optional): Return absolute paths (default: False).
    - page (int, optional): Page number (default: 1).
    - page_size (int, optional): Max results per page, 1-50 (default: 20).
    Returns: Dict with 'results', 'page', 'page_size', 'total_matches', 'total_pages', 'has_more'.
    """
    try:
        base = safe_path(path)
        if not base.is_dir():
            return {"error": f"'{path}' is not a valid directory"}

        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 50))

        conditions = ["LOWER(path) LIKE ?"]
        params = [f"{str(base).lower()}%"]

        if name:
            conditions.append("LOWER(path) LIKE ?")
            params.append(f"%{name.lower()}%")

        if extension:
            if not extension.startswith("."):
                extension = "." + extension
            conditions.append("LOWER(path) LIKE ?")
            params.append(f"%{extension.lower()}")

        if not recursive:
            conditions.append("path NOT LIKE ?")
            params.append(f"{base}{os.sep}%{os.sep}%")

        where_clause = " AND ".join(conditions)

        # Count total matches
        count_query = f"SELECT COUNT(*) FROM files WHERE {where_clause}"
        with database.db.cursor() as cur:
            cur.execute(count_query, params)
            total_matches = cur.fetchone()[0]

            query = f"""
                SELECT path FROM files
                WHERE {where_clause}
                LIMIT {page_size} OFFSET {(page - 1) * page_size}
            """
            cur.execute(query, params)
            rows = cur.fetchall()

        results = []
        for row in rows:
            p = Path(row[0])
            results.append(str(p if full_path else p.relative_to(base)))

        total_pages = max(1, (total_matches + page_size - 1) // page_size)

        return {
            "results": results,
            "page": page,
            "page_size": page_size,
            "total_matches": total_matches,
            "total_pages": total_pages,
            "has_more": page < total_pages,
        }

    except Exception as e:
        return {"error": str(e)}


# Image related tools
@mcp.tool("search_image_by_text")
def search_image_by_text(
    query: str,
    top_k: int = 5,
    page: int = 1,
    page_size: int = 10,
    min_score: float = None,
):
    """
    Semantically searches indexed local images by text description using local vision model.
    Results are bounded and ranked by similarity to protect LLM context.
    Args:
    - query (str, required): Natural description of the image(s) to find.
    - top_k (int, optional): Total top candidates to query from index, max 25 (default: 5).
    - page (int, optional): Page number (default: 1).
    - page_size (int, optional): Items per page, 1-20 (default: 10).
    - min_score (float, optional): Minimum cosine similarity score threshold (0.0 to 1.0).
    Returns: Dict with 'results' (path, similarity score, metadata), 'total_found', 'has_more'.
    """
    try:
        top_k = max(1, min(int(top_k), 25))
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 20))

        res = image_utils.search_by_text(query, top_k=top_k, min_score=min_score)
        total_found = len(res)
        total_pages = max(1, (total_found + page_size - 1) // page_size)
        start_idx = (page - 1) * page_size
        paged_results = res[start_idx : start_idx + page_size]

        return {
            "query": query,
            "results": [
                {
                    "path": str(r["path"]),
                    "similarity": r.get("similarity"),
                    "resolution": r.get("resolution"),
                    "city": r.get("city"),
                    "country": r.get("country"),
                }
                for r in paged_results
            ],
            "page": page,
            "page_size": page_size,
            "total_found": total_found,
            "total_pages": total_pages,
            "has_more": page < total_pages,
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool("search_by_image")
def search_by_image(
    path: str, top_k: int = 5, page: int = 1, page_size: int = 10, min_score: float = None
):
    """
    Finds visually similar indexed images to a reference image.
    Args:
    - path (str, required): Query image path.
    - top_k (int, optional): Total top candidates, max 25 (default: 5).
    - page (int, optional): Page number (default: 1).
    - page_size (int, optional): Items per page, 1-20 (default: 10).
    - min_score (float, optional): Minimum cosine similarity score threshold (0.0 to 1.0).
    Returns: Dict with 'results' list, 'reference_image', 'total_found', 'has_more'.
    """
    try:
        top_k = max(1, min(int(top_k), 25))
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 20))

        res = image_utils.search_by_image(path, top_k=top_k, min_score=min_score)
        total_found = len(res)
        total_pages = max(1, (total_found + page_size - 1) // page_size)
        start_idx = (page - 1) * page_size
        paged_results = res[start_idx : start_idx + page_size]

        return {
            "reference_image": path,
            "results": [
                {
                    "path": str(r["path"]),
                    "similarity": r.get("similarity"),
                    "resolution": r.get("resolution"),
                    "city": r.get("city"),
                    "country": r.get("country"),
                }
                for r in paged_results
            ],
            "page": page,
            "page_size": page_size,
            "total_found": total_found,
            "total_pages": total_pages,
            "has_more": page < total_pages,
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool("search_image_by_metadata")
def search_image_by_metadata(
    make: str = None,
    model: str = None,
    country: str = None,
    city: str = None,
    min_width: int = None,
    min_height: int = None,
    has_gps: bool = None,
    page: int = 1,
    page_size: int = 10,
):
    """
    Searches indexed images by EXIF/metadata filters with pagination.
    Args (all optional):
    - make (str): Camera make.
    - model (str): Camera model.
    - country (str): Location country.
    - city (str): Location city.
    - min_width (int): Min image width.
    - min_height (int): Min image height.
    - has_gps (bool): Has GPS data.
    - page (int): Page number (default: 1).
    - page_size (int): Max results per page, 1-50 (default: 10).
    Returns: Dict with 'results', 'page', 'page_size', 'count', 'has_more'.
    """
    try:
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 50))
        res = image_utils.query_by_metadata(
            make=make,
            model=model,
            country=country,
            city=city,
            min_width=min_width,
            min_height=min_height,
            has_gps=has_gps,
            page=page,
            page_size=page_size,
        )
        return {
            "results": [str(p["path"]) for p in res],
            "page": page,
            "page_size": page_size,
            "count": len(res),
            "has_more": len(res) >= page_size,
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool("export_images_metadata_csv")
def export_images_metadata_csv(
    output_path: str,
    make: str = None,
    model: str = None,
    country: str = None,
    city: str = None,
    min_width: int = None,
    min_height: int = None,
    has_gps: bool = None,
    limit: int = None,
):
    """
    Exports indexed image EXIF/GPS metadata to a CSV file, with optional filters.
    Streams in chunks with an atomic write, so the full table can be exported safely.
    Args:
    - output_path (str, required): Destination CSV path (must be inside an allowed path).
    - make (str, optional): Filter by camera make (substring, case-insensitive).
    - model (str, optional): Filter by camera model.
    - country (str, optional): Filter by location country.
    - city (str, optional): Filter by location city.
    - min_width (int, optional): Min image width.
    - min_height (int, optional): Min image height.
    - has_gps (bool, optional): Only images with GPS (True) or without GPS (False).
    - limit (int, optional): Max rows to export. Omit or null for all matching rows.
    Returns: Dict with 'success', 'path', 'row_count'.
    """
    try:
        dest = safe_path(output_path)
        if dest.suffix.lower() != ".csv":
            return {"error": "output_path must end with '.csv'"}
        if limit is not None:
            limit = int(limit)
            if limit <= 0:
                return {"error": "limit must be a positive integer or omitted"}
        return image_utils.export_images_metadata_to_path(
            str(dest),
            make=make,
            model=model,
            country=country,
            city=city,
            min_width=min_width,
            min_height=min_height,
            has_gps=has_gps,
            limit=limit,
        )
    except Exception as e:
        return {"error": str(e)}


@mcp.tool("inspect_image")
def inspect_image(path: str) -> Dict[str, Any]:
    """
    Visually inspects and describes an image file on disk.
    Extracts dimensions, format, color space, camera EXIF metadata, GPS location,
    dominant color palette, and local SigLIP zero-shot semantic tags.
    Allows text-only LLMs to perceive and accurately describe image contents without raw pixels.
    Args:
    - path (str, required): Path to the image file.
    Returns: Dict with structured visual properties, camera telemetry, geolocation, and semantic tags.
    """
    try:
        file_path = safe_path(path)
        if not file_path.is_file():
            return {"error": f"'{file_path.name}' is not a valid file"}

        from PIL import Image
        from math import gcd
        with Image.open(file_path) as img:
            w, h = img.size
            fmt = img.format or file_path.suffix.upper().replace(".", "")
            mode = img.mode

            d = gcd(w, h)
            simplified_ratio = f"{w // d}:{h // d}" if d > 1 and (w // d) <= 32 else f"{w/h:.2f}:1"
            orientation_desc = "landscape" if w > h else ("portrait" if h > w else "square")

            # Dominant colors
            thumb = img.convert("RGB").resize((40, 40))
            colors = thumb.getcolors(maxcolors=1600)
            dominant_colors = []
            if colors:
                sorted_colors = sorted(colors, key=lambda x: x[0], reverse=True)[:4]
                total_px = sum(c[0] for c in sorted_colors)
                for count, (r, g, b) in sorted_colors:
                    pct = int((count / total_px) * 100)
                    if r > 200 and g > 200 and b > 200:
                        c_name = "White / Light"
                    elif r < 45 and g < 45 and b < 45:
                        c_name = "Black / Dark"
                    elif r > g and r > b:
                        c_name = "Red / Warm Tones" if r > 150 and g < 100 else "Orange / Brown"
                    elif g > r and g > b:
                        c_name = "Green / Foliage"
                    elif b > r and b > g:
                        c_name = "Blue / Sky / Water"
                    elif abs(r - g) < 25 and abs(g - b) < 25:
                        c_name = "Gray / Neutral"
                    else:
                        c_name = "Mixed / Vibrant"
                    dominant_colors.append(f"{c_name} (~{pct}%)")

            # EXIF metadata
            from utils import exif_utils
            raw_exif = exif_utils.get_exif_data(file_path)
            exif_summary = {}
            if raw_exif:
                camera_meta = exif_utils.get_camera_metadata(raw_exif)
                for k, v in camera_meta.items():
                    if v:
                        exif_summary[k] = v

                gps_meta = exif_utils.get_gps_info(raw_exif)
                if gps_meta and "latitude" in gps_meta and "longitude" in gps_meta:
                    exif_summary["gps"] = {
                        "latitude": gps_meta.get("latitude"),
                        "longitude": gps_meta.get("longitude"),
                    }
                    geo = exif_utils.reverse_geocode(gps_meta["latitude"], gps_meta["longitude"])
                    if geo:
                        exif_summary["location_display_name"] = geo.get("display_name")
                        exif_summary["country"] = geo.get("country")
                        exif_summary["city"] = geo.get("city")

            # SigLIP Semantic Perception
            candidate_tags = [
                "outdoor landscape",
                "nature trees and foliage",
                "beach ocean or sea",
                "sunset or sunrise",
                "city street and architecture",
                "person or portrait",
                "screenshot of software code or browser",
                "document invoice or text receipt",
                "food beverage or dining",
                "animal pet dog or cat",
                "vehicle car or transportation",
                "diagram chart or infographic",
                "night city with lights",
                "indoor room or home interior",
            ]
            detected_tags = []
            try:
                import torch
                proc, mod = image_utils.get_model_and_processor()
                proc_inputs = proc(
                    text=candidate_tags, images=img, padding="max_length", return_tensors="pt"
                ).to(image_utils.DEVICE)
                with torch.no_grad():
                    out = mod(**proc_inputs)
                    probs = torch.sigmoid(out.logits_per_image).cpu().squeeze().tolist()

                tag_scores = list(zip(candidate_tags, probs))
                tag_scores.sort(key=lambda x: x[1], reverse=True)
                for tag, score in tag_scores[:5]:
                    if score >= 0.15:
                        detected_tags.append(f"{tag} ({score:.2f})")
            except Exception:
                pass

        return {
            "path": str(file_path),
            "filename": file_path.name,
            "format": fmt,
            "dimensions": f"{w}x{h}",
            "aspect_ratio": f"{simplified_ratio} ({orientation_desc})",
            "file_size_bytes": file_path.stat().st_size,
            "dominant_palette": dominant_colors,
            "detected_visual_concepts": detected_tags,
            "exif_metadata": exif_summary,
            "visual_summary": (
                f"{orientation_desc.capitalize()} {fmt} image ({w}x{h}). "
                f"Dominant palette: {', '.join(dominant_colors[:2]) if dominant_colors else 'N/A'}. "
                f"Visual tags: {', '.join([t.split(' (')[0] for t in detected_tags[:3]]) if detected_tags else 'N/A'}."
            ),
        }
    except Exception as e:
        return {"error": f"Failed to inspect image: {str(e)}"}


@mcp.tool("deduplicate_uploads")
def deduplicate_uploads(directory: str = "uploads") -> Dict[str, Any]:
    """
    Scans the uploads directory, groups files by SHA-256 hash, and removes redundant duplicates.
    Keeps the primary file for each unique hash and reclaims storage space.
    Returns: Dict with unique_files count, duplicates_removed count, and bytes_reclaimed.
    """
    try:
        resolved = safe_path(directory)
        return clean_duplicate_uploads(str(resolved))
    except Exception as e:
        return {"error": str(e)}


def main():
    import argparse
    import pprint

    parser = argparse.ArgumentParser(description="Test file management tools.")

    # Tool selection
    parser.add_argument(
        "--allowed-paths", action="store_true", help="Test allowed_paths()"
    )
    parser.add_argument(
        "--current-dir", action="store_true", help="Test current_directory()"
    )
    parser.add_argument("--list-dir", metavar="PATH", help="Test list_directory(path)")
    parser.add_argument(
        "--create-dir", nargs="+", metavar="PATH", help="Test create_directory(path)"
    )
    parser.add_argument(
        "--read", nargs="+", metavar="PATH", help="Test read_file(path)"
    )
    parser.add_argument("--write", metavar="PATH", help="Path for write_file()")
    parser.add_argument("--delete", nargs="+", metavar="PATH", help="Test delete(path)")
    parser.add_argument(
        "--copy", nargs="+", metavar="SRC", help="Source path(s) for copy_file()"
    )
    parser.add_argument(
        "--move", nargs="+", metavar="SRC", help="Source path(s) for move()"
    )
    parser.add_argument(
        "--batch-move",
        nargs="*",
        metavar="SRC_DEST",
        help="Batch move: src1 dest1 src2 dest2 ...",
    )
    parser.add_argument(
        "--info", nargs="+", metavar="PATH", help="Test get_file_info(path)"
    )
    parser.add_argument("--search", metavar="PATH", help="Base path for search_files()")
    parser.add_argument(
        "--search-img-text", metavar="QUERY", help="Test search_image_by_text(query)"
    )
    parser.add_argument(
        "--search-img-file", metavar="PATH", help="Test search_by_image(path)"
    )

    # Options for tools
    parser.add_argument("--content", help="Content for write_file()")
    parser.add_argument("--dest", help="Destination path for copy or move")
    parser.add_argument("--name", help="Name filter for search_files()")
    parser.add_argument("--extension", help="Extension filter for search_files()")
    parser.add_argument(
        "--top-k", type=int, default=5, help="Number of results for image search"
    )

    # Boolean flags
    parser.add_argument(
        "--full-path", action="store_true", help="Use full paths in results"
    )
    parser.add_argument(
        "--append", action="store_true", help="Append mode for write_file()"
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="Overwrite mode for write_file()"
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Recursive mode for delete() or search_files()",
    )

    args = parser.parse_args()
    result = None

    try:
        if args.allowed_paths:
            result = allowed_paths()
        elif args.current_dir:
            result = current_directory()
        elif args.list_dir:
            result = list_directory(args.list_dir, full_path=args.full_path)
        elif args.create_dir:
            result = create_directory(args.create_dir, full_path=args.full_path)
        elif args.read:
            result = read_file(args.read, full_path=args.full_path)
        elif args.write and args.content is not None:
            result = write_file(
                args.write,
                args.content,
                append=args.append,
                overwrite=args.overwrite,
                full_path=args.full_path,
            )
        elif args.delete:
            result = delete(
                args.delete, recursive=args.recursive, full_path=args.full_path
            )
        elif args.copy and args.dest:
            src = args.copy[0] if len(args.copy) == 1 else args.copy
            result = copy_file(src, args.dest, full_path=args.full_path)
        elif args.move and args.dest:
            src = args.move[0] if len(args.move) == 1 else args.move
            result = move(src, args.dest, full_path=args.full_path)
        elif args.batch_move is not None:
            if len(args.batch_move) % 2 != 0:
                print(
                    "Error: --batch-move requires an even number of arguments (source-destination pairs)."
                )
            else:
                moves = [
                    {"src": args.batch_move[i], "dest": args.batch_move[i + 1]}
                    for i in range(0, len(args.batch_move), 2)
                ]
                result = batch_move(moves, full_path=args.full_path)
        elif args.info:
            result = get_file_info(args.info, full_path=args.full_path)
        elif args.search:
            result = search_files(
                args.search,
                name=args.name,
                extension=args.extension,
                recursive=args.recursive,
                full_path=args.full_path,
            )
        elif args.search_img_text:
            result = search_image_by_text(args.search_img_text, top_k=args.top_k)
        elif args.search_img_file:
            result = search_by_image(args.search_img_file, top_k=args.top_k)
        else:
            parser.print_help()
            return

        if result:
            print("\n--- Function Result ---")
            pprint.pprint(result)

    except Exception as e:
        print(f"\n--- An error occurred ---")
        print(e)
    finally:
        # Clean up the temp directory if you want
        # import time
        # print("\nCleaning up temp directory...")
        # time.sleep(1)
        # shutil.rmtree(TEMP_DIR, ignore_errors=True)
        pass


if __name__ == "__main__":
    try:
        mcp.run(transport="streamable-http")
    except ConnectionResetError:
        pass
    except Exception as e:
        print(f"Server error: {e}")
    # main()
