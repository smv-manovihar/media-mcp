import os
import shutil
from pathlib import Path
from datetime import datetime, timezone
from typing import Union, List, Dict
from mcp.server.fastmcp import FastMCP

from config.settings import load_config
from utils import database
from helpers import helpers
import utils.image_search_utils as image_utils
import utils.fileops_utils as file_utils

mcp = FastMCP("file_management", port=8000)

config = load_config(verbose=True)

print("Allowed paths:")
for p in config.user_allowed_paths:
    print(f"- 📁 {p}")
    p.mkdir(exist_ok=True)

print("Media indexed paths:")
for p in config.user_media_index_allowed_paths:
    print(f"- 📁 {p}")


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
def list_directory(path: str, full_path: bool = False):
    """
    Lists immediate directory contents (non-recursive).
    Args:
    - path (str, required): Directory path.
    - full_path (bool, optional): Return absolute paths (default: False, relative names).
    Returns: Dict with 'items' list of dicts {'name': str, 'type': 'file'|'directory'}, or 'error'.
    """
    try:
        base = safe_path(path)
        if not base.is_dir():
            return {"error": f"{path} is not a valid directory"}

        items = []
        for p in base.iterdir():
            items.append(
                {
                    "name": str(p if full_path else p.name),
                    "type": "directory" if p.is_dir() else "file",
                }
            )
        return {"items": items}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool("create_directory")
def create_directory(path: Union[str, List[str]], full_path: bool = False):
    """
    Creates directory(ies), including parents. Supports batch via list.
    Args:
    - path (str|List[str], required): Path(s) to create.
    - full_path (bool, optional): Return absolute paths in failed (default: False).
    Returns: {"success": True} on full success, else {"success_count": int, "failed": list of dicts {'path': str, 'error': str}} or {'error': str}.
    """
    try:
        dir_paths = safe_paths(path)
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
    path: Union[str, List[str]], full_path: bool = False, max_chars: int = 1000
):
    """
    Reads text file(s) contents, truncated to max_chars. Supports batch via list.
    Args:
    - path (str|List[str], required): File path(s).
    - full_path (bool, optional): Return absolute paths (default: False).
    - max_chars (int, optional): Max chars per file (default: 1000).
    Returns: Dict with 'results' list of dicts {'path': str, 'content': str} or {'error': str}.
    """
    try:
        file_paths = safe_paths(path)
        results = []
        for file_path in file_paths:
            if not file_path.is_file():
                results.append({"error": f"{file_path} is not a valid file"})
                continue
            try:
                with file_path.open("r", encoding="utf-8") as f:
                    content = f.read()
                    if len(content) > max_chars:
                        content = (
                            content[:max_chars]
                            + f"\n\n[Content truncated to {max_chars} characters for context limit]"
                        )
                    results.append(
                        {
                            "path": str(file_path if full_path else file_path.name),
                            "content": content,
                        }
                    )
            except Exception as e:
                results.append({"error": str(e)})
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
    Gets metadata for file(s)/dir(s): size, modified/created times, type. Supports batch.
    Args:
    - path (str|List[str], required): Path(s) to query.
    - full_path (bool, optional): Return absolute paths (default: False).
    Returns: Dict with 'results' list of dicts {'path': str, 'is_file': bool, 'is_dir': bool, 'size_bytes': int, 'modified': str, 'created': str} or {'error': str}.
    """
    try:
        file_paths = safe_paths(path)
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
    page_size: int = 10,
):
    try:
        base = safe_path(path)
        if not base.is_dir():
            return {"error": f"{path} is not a valid directory"}

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
            # Match paths with exactly one level deeper (avoid double %)
            conditions.append("path NOT LIKE ?")
            params.append(f"{base}{os.sep}%{os.sep}%")

        query = f"""
            SELECT path FROM files
            WHERE {' AND '.join(conditions)}
            LIMIT {page_size} OFFSET {(page - 1) * page_size}
        """

        with database.db.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()

        results = []
        for row in rows:
            p = Path(row[0])
            results.append(str(p if full_path else p.relative_to(base)))

        return {"results": results}

    except Exception as e:
        return {"error": str(e)}


# Image related tools
@mcp.tool("search_image_by_text")
def search_image_by_text(query: str, top_k: int = 5):
    """
    Semantically searches indexed images by text query.
    Args:
    - query (str, required): Text description.
    - top_k (int, optional): Max results (default: 5).
    Returns: List of absolute image paths.
    """
    res = image_utils.search_by_text(query, top_k)
    return [str(p["path"]) for p in res]


@mcp.tool("search_by_image")
def search_by_image(path: str, top_k: int = 5):
    """
    Finds similar indexed images to a given image.
    Args:
    - path (str, required): Query image path.
    - top_k (int, optional): Max results (default: 5).
    Returns: Dict with 'results' list of absolute image paths.
    """
    res = image_utils.search_by_image(path, top_k)
    return {"results": [str(p["path"]) for p in res]}


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
    Searches indexed images by EXIF/metadata filters.
    Args (all optional):
    - make (str): Camera make.
    - model (str): Camera model.
    - country (str): Location country.
    - city (str): Location city.
    - min_width (int): Min image width.
    - min_height (int): Min image height.
    - has_gps (bool): Has GPS data.
    - page (int): Page number (default: 1).
    - page_size (int): Page size (default: 10).
    Returns: List of absolute image paths.
    """
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
    return [str(p["path"]) for p in res]


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
