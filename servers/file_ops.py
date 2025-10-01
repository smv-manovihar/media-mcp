import os
import shutil
from pathlib import Path
from datetime import datetime, timezone
from typing import Union, List, Dict
from mcp.server.fastmcp import FastMCP

from config.settings import load_config_loud
from utils import database
from helpers import helpers
import utils.image_search_utils as image_utils
import utils.fileops_utils as file_utils

mcp = FastMCP("file_management", port=8000)

config = load_config_loud()
# --- Create the directories if they don't exist ---
print("Allowed paths:")
for p in config["allowed_paths"]:
    print(f"- 📁 {p}")
    p.mkdir(exist_ok=True)

print("Media indexed paths:")
for p in config["media_index_allowed_paths"]:
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
        f"Access denied: {resolved_path} is not within any allowed sandbox directory."
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
    Get the list of allowed directories, You are only allowed in these paths and their subdirectories.
    Returns:
    - A list of strings representing the paths.
    """
    return {
        "allowed_paths": [str(p) for p in config["allowed_paths"]],
        "media_indexed_paths": [str(p) for p in config["media_index_allowed_paths"]],
    }


@mcp.tool("current_directory")
def current_directory():
    """
    Get the primary working directory for the agent.
    Returns the first path from the list of allowed sandbox directories.
    """
    try:
        cwd = safe_path(str(Path.cwd()))
        return {"path": str(cwd)}
    except Exception:
        return {"error": "Current directory is not within the allowed paths."}


@mcp.tool("list_directory")
def list_directory(path: str, full_path: bool = False):
    """
    List immediate contents of a directory (non-recursive).
    Arguments:
    - path: The path to the directory to list.
    - full_path (optional): If True, returns absolute paths. Defaults to False (relative).
    Returns:
    - A list of dictionaries containing the name and type of each item.
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
    Create directory(ies) (including parents). Supports batch creation if path is a list.
    Arguments:
    - path: The path(s) to the directory(ies) to create (str or list of str).
    - full_path (optional): If True, returns absolute paths. Defaults to False (relative).
    Returns:
    - A list of dictionaries indicating success or failure for each directory.
    """
    try:
        dir_paths = safe_paths(path)
        results = []
        for dir_path in dir_paths:
            try:
                dir_path.mkdir(parents=True, exist_ok=True)
                results.append(
                    {
                        "success": True,
                        "path": str(dir_path if full_path else dir_path.name),
                    }
                )
            except Exception as e:
                results.append({"error": str(e)})
        return {"results": results}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool("read_file")
def read_file(
    path: Union[str, List[str]], full_path: bool = False, max_chars: int = 1000
):
    """
    Read file(s)' contents as text. Supports batch reading if path is a list.
    Limits the content to max_chars per file to avoid exceeding context length.
    Arguments:
    - path: The path to the file(s) to read (str or list of str).
    - full_path (optional): If True, returns absolute paths. Defaults to False (relative).
    - max_chars (optional): Maximum characters to read per file. Defaults to 1000.
    Returns:
    - A list of dictionaries containing the path and the contents of each file (truncated if needed), or error for invalid ones.
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
    Write text content to a file. This will remove the old DB entry and re-scan the file to get the new hash.
    Arguments:
    - path: The path to the file to write to (str).
    - content: The text content to write to the file.
    - append (optional): If True, appends to the file instead of overwriting. Defaults to False.
    - overwrite (optional): If True, overwrites the file if it exists. Defaults to False.
    - full_path (optional): If True, returns the absolute path. Defaults to False.
    Returns:
    - A dictionary indicating success or failure.
    """
    try:
        file_path = safe_path(path)
        if (not overwrite and not append) and file_path.exists():
            return {
                "error": f"File already exists at {file_path}. Use overwrite=True or append=True."
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
            image_utils.scan_images([str(file_path)], silent=True)
        else:
            file_utils.scan_files([str(file_path)], silent=True)
        return {
            "success": True,
            "path": str(file_path if full_path else file_path.name),
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool("delete")
def delete(
    path: Union[str, List[str]], recursive: bool = False, full_path: bool = False
):
    """
    Deletes file(s) or directory(ies). Supports batch deletion if path is a list.

    - For files, it deletes them directly.
    - For directories, behavior depends on the 'recursive' flag:
      - If recursive=False (default): Deletes ONLY empty directories.
      - If recursive=True: Deletes directories and ALL their contents (⚠️ irreversible).

    Arguments:
    - path: The path(s) to the item(s) to delete (str or list of str).
    - recursive (optional): If True, allows deletion of non-empty directories. Defaults to False.
    - full_path (optional): If True, returns absolute paths. Defaults to False.

    Returns:
    - A dictionary containing a list of results for each deletion.
    """
    try:
        paths_to_delete = safe_paths(path)
        results = []
        for p in paths_to_delete:
            try:
                if not p.exists():
                    results.append({"error": f"Path not found: {p}"})
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

                results.append(
                    {"success": True, "deleted": str(p if full_path else p.name)}
                )

            except OSError as e:
                error_msg = str(e)
                if "Directory not empty" in error_msg and not recursive:
                    error_msg = f"Directory '{p.name}' is not empty. Use recursive=True to delete it."
                results.append({"error": error_msg})
            except Exception as e:
                results.append({"error": str(e)})
        return {"results": results}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool("copy_file")
def copy_file(
    src: Union[str, List[str]], dest: Union[str, Path], full_path: bool = False
):
    """
    Copy file(s) to a new location and updates the database index to the new path.
    If src is a list, copies all to the dest directory.
    Note: This re-indexes the file at the new location; the original path will be removed from the index.
    Arguments:
    - src: The path(s) to the file(s) to copy (str or list of str).
    - dest: The path to the new location or directory for the file(s).
    - full_path (optional): If True, returns absolute paths. Defaults to False (relative).
    Returns:
    - A list of dictionaries indicating success or failure for each copy operation.
    """
    try:
        src_paths = safe_paths(src)
        dest_path = safe_path(dest)
        if len(src_paths) > 1:
            dest_path.mkdir(parents=True, exist_ok=True)

        results = []
        for src_path in src_paths:
            if not src_path.is_file():
                results.append({"error": f"{src_path} is not a file"})
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
                    image_utils.scan_images([str(final_dest)], silent=True)
                else:
                    file_utils.scan_files([str(final_dest)], silent=True)
                results.append(
                    {
                        "success": True,
                        "destination": str(
                            final_dest if full_path else final_dest.name
                        ),
                    }
                )
            except Exception as e:
                results.append({"error": str(e)})
        return {"results": results}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool("move")
def move(src: Union[str, List[str]], dest: Union[str, Path], full_path: bool = False):
    """
    Moves or renames files and directories.

    - To move a single item: move(src='file.txt', dest='dir/')
    - To rename a single item: move(src='old.txt', dest='new.txt')
    - To move multiple items into one directory: move(src=['a.txt', 'b.txt'], dest='dir/')

    Arguments:
    - src: A source path or a list of source paths.
    - dest: The destination path or directory.
    - full_path (optional): If True, returns absolute paths. Defaults to False.

    Returns:
    - A dictionary containing a list of results for each move operation.
    """
    try:
        results = []
        src_paths, dest_path = safe_paths(src), safe_path(dest)

        if len(src_paths) > 1:
            if dest_path.exists() and not dest_path.is_dir():
                return {
                    "error": f"Destination '{dest_path}' must be a directory for multiple sources."
                }
            dest_path.mkdir(parents=True, exist_ok=True)

        for src_path in src_paths:
            if not src_path.exists():
                results.append({"error": f"Source {src_path} does not exist."})
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

                results.append(
                    {
                        "success": True,
                        "source": str(src_path.name),
                        "destination": str(
                            final_dest if full_path else final_dest.name
                        ),
                    }
                )
            except Exception as e:
                results.append({"error": f"Failed to move '{src_path}': {e}"})
        return {"results": results}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool("batch_move")
def batch_move(moves: List[Dict[str, str]], full_path: bool = False):
    """
    Batch moves multiple files or directories to their individual destinations.

    Arguments:
    - moves: A list of dictionaries, each with 'src' and 'dest' keys.
    - full_path (optional): If True, returns absolute paths. Defaults to False.

    Returns:
    - A dictionary containing a list of results for each move operation.
    """
    try:
        results = []
        for item in moves:
            item_src, item_dest = item.get("src"), item.get("dest")
            if not item_src or not item_dest:
                results.append({"error": "Missing 'src' or 'dest' in a batch item."})
                continue
            try:
                src_path, dest_path = safe_path(item_src), safe_path(item_dest)
                if not src_path.exists():
                    results.append({"error": f"Source {src_path} does not exist."})
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

                results.append(
                    {
                        "success": True,
                        "source": str(src_path if full_path else src_path.name),
                        "destination": str(dest_path if full_path else dest_path.name),
                    }
                )
            except Exception as e:
                results.append({"error": f"Failed to move '{item_src}': {e}"})
        return {"results": results}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool("get_file_info")
def get_file_info(path: Union[str, List[str]], full_path: bool = False):
    """
    Get file(s) metadata (size, modified time, created time). Supports batch if path is a list.
    Arguments:
    - path: The path(s) to the file(s) to get metadata for (str or list of str).
    - full_path (optional): If True, returns absolute paths. Defaults to False (relative).
    Returns:
    - A list of dictionaries containing the path, file type, size, modified time, and created time for each.
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
):
    """
    Search for files inside a directory using the FAST database index.
    Arguments:
    - path: The directory to start the search from.
    - name (optional): Match a substring in the filename (case-insensitive).
    - extension (optional): Filter by file extension (e.g., '.txt', 'py').
    - recursive (optional): If True, searches subdirectories. Defaults to True.
    - full_path (optional): If True, returns absolute paths. Defaults to False.
    Returns:
    - A list of matching file paths.
    """
    try:
        base = safe_path(path)
        if not base.is_dir():
            return {"error": f"{path} is not a valid directory"}

        conditions = ["path LIKE ?"]
        params = [f"{base}%"]

        if name:
            conditions.append("path LIKE ?")
            params.append(f"%{name}%")

        if extension:
            if not extension.startswith("."):
                extension = "." + extension
            conditions.append("path LIKE ?")
            params.append(f"%{extension}")

        if not recursive:
            conditions.append("path NOT LIKE ?")
            params.append(f"{base}{os.sep}%{os.sep}%")

        query = f"SELECT path FROM files WHERE {' AND '.join(conditions)} LIMIT 200"

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
    Search for images by text query semantically.
    Arguments:
    - query: The text query to search for.
    - top_k (optional): The number of results to return. Defaults to 5.
    Returns:
    - A list of absolute paths to the images.
    """
    res = image_utils.search_by_text(query, top_k)
    return [str(p["path"]) for p in res]


@mcp.tool("search_by_image")
def search_by_image(path: str, top_k: int = 5):
    """
    Search for similar images by providing the path to an image.
    Arguments:
    - path: The path to the image to search for.
    - top_k (optional): The number of results to return. Defaults to 5.
    Returns:
    - A list containing the absolute paths to the images.
    """
    res = image_utils.search_by_image(path, top_k)
    return {"results": [str(p["path"]) for p in res]}


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
