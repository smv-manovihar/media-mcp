import os
import shutil
from pathlib import Path
from datetime import datetime
from typing import Union, List, Dict
from mcp.server.fastmcp import FastMCP

from config.settings import load_config_loud
import utils.image_search_utils as image_utils

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
    - max_chars (optional): Maximum characters to read per file. Defaults to 5000.
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
    path: Union[str, List[str]],
    content: str,
    append: bool = False,
    overwrite: bool = False,
    full_path: bool = False,
):
    """
    Write text content to file(s) (overwrite if exists). Supports batch writing the same content to multiple files.
    Arguments:
    - path: The path(s) to the file(s) to write to (str or list of str).
    - content: The text content to write to the file(s).
    - append (optional): If True, appends to the file(s) instead of overwriting. Defaults to False.
    - overwrite (optional): If True, overwrites the file(s) if they exist. Defaults to False.
    - full_path (optional): If True, returns absolute paths. Defaults to False (relative).
    Returns:
    - A list of dictionaries indicating success or failure for each file.
    """
    try:
        file_paths = safe_paths(path)
        results = []
        for file_path in file_paths:
            if (not overwrite) and file_path.exists():
                results.append({"error": f"File already exists at {file_path}"})
                continue
            file_path.parent.mkdir(parents=True, exist_ok=True)
            mode = "a" if append else "w"
            try:
                with file_path.open(mode, encoding="utf-8") as f:
                    f.write(content)
                results.append(
                    {
                        "success": True,
                        "path": str(file_path if full_path else file_path.name),
                    }
                )
            except Exception as e:
                results.append({"error": str(e)})
        return {"results": results}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool("delete_file")
def delete_file(path: Union[str, List[str]], full_path: bool = False):
    """
    Delete file(s). Supports batch deletion if path is a list.
    Arguments:
    - path: The path(s) to the file(s) to delete (str or list of str).
    - full_path (optional): If True, returns absolute paths. Defaults to False (relative).
    Returns:
    - A list of dictionaries indicating success or failure for each file.
    """
    try:
        file_paths = safe_paths(path)
        results = []
        for file_path in file_paths:
            if not file_path.exists():
                results.append({"error": f"File not found at {file_path}"})
                continue
            if not file_path.is_file():
                results.append(
                    {
                        "error": f"Path is a directory, not a file. Use a directory deletion tool."
                    }
                )
                continue
            try:
                file_path.unlink()
                results.append(
                    {
                        "success": True,
                        "path": str(file_path if full_path else file_path.name),
                    }
                )
            except Exception as e:
                results.append({"error": str(e)})
        return {"results": results}
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


@mcp.tool("delete_directory")
def delete_directory(path: Union[str, List[str]], full_path: bool = False):
    """
    Delete empty directory(ies). Supports batch deletion if path is a list.
    Arguments:
    - path: The path(s) to the directory(ies) to delete (str or list of str).
    - full_path (optional): If True, returns absolute paths. Defaults to False (relative).
    Returns:
    - A list of dictionaries indicating success or failure for each directory.
    """
    try:
        dir_paths = safe_paths(path)
        results = []
        for dir_path in dir_paths:
            try:
                dir_path.rmdir()
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


@mcp.tool("delete_directory_recursive")
def delete_directory_recursive(path: Union[str, List[str]], full_path: bool = False):
    """
    Delete directory(ies) and all of their contents (⚠️ irreversible). Supports batch if path is a list.
    Arguments:
    - path: The path(s) to the directory(ies) to delete (str or list of str).
    - full_path (optional): If True, returns absolute paths. Defaults to False (relative).
    Returns:
    - A list of dictionaries indicating success or failure for each directory.
    """
    try:
        dir_paths = safe_paths(path)
        results = []
        for dir_path in dir_paths:
            try:
                shutil.rmtree(dir_path)
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


@mcp.tool("copy_file")
def copy_file(
    src: Union[str, List[str]], dest: Union[str, Path], full_path: bool = False
):
    """
    Copy file(s) to a new location. If src is a list, copies all to dest directory.
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
            if not dest_path.is_dir():
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
                shutil.copy2(src_path, final_dest)
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


@mcp.tool("move_file")
def move_file(
    src: Union[str, List[str]], dest: Union[str, Path], full_path: bool = False
):
    """
    Move (or rename) file(s). If src is a list, moves all to dest directory.
    Arguments:
    - src: The path(s) to the file(s) to move (str or list of str).
    - dest: The path to the new location or directory for the file(s).
    - full_path (optional): If True, returns absolute paths. Defaults to False (relative).
    Returns:
    - A list of dictionaries indicating success or failure for each move operation.
    """
    try:
        src_paths = safe_paths(src)
        dest_path = safe_path(dest)
        if len(src_paths) > 1:
            if not dest_path.is_dir():
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
                shutil.move(src_path, final_dest)
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


@mcp.tool("batch_move_files")
def batch_move_files(moves: List[Dict[str, str]], full_path: bool = False):
    """
    Batch move multiple files to their individual destinations. Each move is specified as a dict with 'src' and 'dest' keys.
    Arguments:
    - moves: A list of dictionaries, each containing 'src' (source path) and 'dest' (destination path).
    - full_path (optional): If True, returns absolute paths. Defaults to False (relative).
    Returns:
    - A list of dictionaries indicating success or failure for each move operation.
    """
    try:
        results = []
        for move in moves:
            src = move.get("src")
            dest = move.get("dest")
            if not src or not dest:
                results.append({"error": "Missing 'src' or 'dest' in move dict"})
                continue
            try:
                src_path = safe_path(src)
                dest_path = safe_path(dest)
                if not src_path.is_file():
                    results.append({"error": f"{src_path} is not a file"})
                    continue
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(src_path, dest_path)
                results.append(
                    {
                        "success": True,
                        "source": str(src_path if full_path else src_path.name),
                        "destination": str(dest_path if full_path else dest_path.name),
                    }
                )
            except Exception as e:
                results.append({"error": str(e)})
        return {"results": results}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool("move_directory")
def move_directory(
    src: Union[str, List[str]], dest: Union[str, Path], full_path: bool = False
):
    """
    Move (or rename) directory(ies). If src is a list, moves all to dest directory.
    Arguments:
    - src: The path(s) to the directory(ies) to move (str or list of str).
    - dest: The path to the new location or directory for the directory(ies).
    - full_path (optional): If True, returns absolute paths. Defaults to False (relative).
    Returns:
    - A list of dictionaries indicating success or failure for each move operation.
    """
    try:
        src_paths = safe_paths(src)
        dest_path = safe_path(dest)
        if len(src_paths) > 1:
            if not dest_path.is_dir():
                dest_path.mkdir(parents=True, exist_ok=True)
        results = []
        for src_path in src_paths:
            if not src_path.is_dir():
                results.append({"error": f"{src_path} is not a directory"})
                continue
            try:
                final_dest = (
                    dest_path / src_path.name if dest_path.is_dir() else dest_path
                )
                final_dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(src_path, final_dest)
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


def to_relative(base: Path, target: Path) -> str:
    """Convert absolute path to relative (from base)."""
    try:
        return str(target.relative_to(base))
    except ValueError:
        return target.name


@mcp.tool("search_files")
def search_files(
    path: str,
    name: str = None,
    extension: str = None,
    recursive: bool = True,
    max_depth: int = 3,
    full_path: bool = False,
):
    """
    Search for files inside a directory.
    Arguments:
    - path: The directory to start the search from. Must be within the sandbox.
    - name (optional): Match a substring in the filename (case-insensitive).
    - extension (optional): Filter by file extension (e.g., '.txt', 'py').
    - recursive (optional): If True, searches subdirectories. Defaults to True.
    - max_depth (optional): Limits how deep the recursive search goes. Defaults to 3.
    - full_path (optional): If True, returns absolute paths. Defaults to False (relative).
    Returns:
    - A list of dictionaries containing the name, type, and path of each file.
    """
    try:
        base = safe_path(path)
        if not base.is_dir():
            return {"error": f"{path} is not a valid directory"}

        results = []
        if not recursive:
            for p in base.iterdir():
                if p.is_file():
                    if name and name.lower() not in p.name.lower():
                        continue
                    if extension and not p.name.lower().endswith(extension.lower()):
                        continue
                    results.append(str(p if full_path else p.name))
        else:
            for dirpath, _, filenames in os.walk(base):
                current_depth = len(Path(dirpath).relative_to(base).parts)
                if current_depth >= max_depth:
                    continue  # Prune search depth

                for filename in filenames:
                    if name and name.lower() not in filename.lower():
                        continue
                    if extension and not filename.lower().endswith(extension.lower()):
                        continue

                    full_p = Path(dirpath) / filename
                    results.append(
                        str(full_p if full_path else to_relative(base, full_p))
                    )

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
    return [str(p[2]) for p in res]


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
    return {"results": [str(p[2]) for p in res]}


if __name__ == "__main__":
    try:
        mcp.run(transport="streamable-http")
    except ConnectionResetError:
        pass
    except Exception as e:
        print(f"Server error: {e}")
