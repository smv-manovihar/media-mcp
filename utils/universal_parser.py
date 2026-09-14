import os
import csv
import json
import zipfile
import tarfile
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from PIL import Image

try:
    import pypdf
except ImportError:
    pypdf = None

try:
    import docx
except ImportError:
    docx = None

try:
    import openpyxl
except ImportError:
    openpyxl = None


class UniversalParser:
    """
    A robust, unified parser that extracts human-readable text and structured metadata
    from documents (PDF, DOCX, XLSX, PPTX), data (CSV, JSON, IPYNB), code, archives,
    and media files without returning binary garbage.
    """

    @staticmethod
    def parse_file(
        file_path: Path,
        max_chars: int = 3000,
        skip_chars: int = 0,
        page: int = 1,
    ) -> Dict[str, Any]:
        """
        Parses any file into a structured dictionary containing clean text content
        and rich metadata.
        """
        if not file_path.exists():
            return {"error": f"File not found: {file_path.name}"}

        suffix = file_path.suffix.lower()
        file_size = file_path.stat().st_size

        try:
            if suffix == ".pdf":
                return UniversalParser._parse_pdf(file_path, max_chars, page)
            elif suffix == ".docx":
                return UniversalParser._parse_docx(file_path, max_chars, skip_chars)
            elif suffix in (".xlsx", ".xls"):
                return UniversalParser._parse_excel(file_path, max_chars)
            elif suffix == ".pptx":
                return UniversalParser._parse_pptx(file_path, max_chars)
            elif suffix in (".csv", ".tsv"):
                return UniversalParser._parse_delimited(file_path, max_chars, delimiter="," if suffix == ".csv" else "\t")
            elif suffix == ".ipynb":
                return UniversalParser._parse_notebook(file_path, max_chars)
            elif suffix in (".json", ".jsonl"):
                return UniversalParser._parse_json(file_path, max_chars)
            elif suffix in (".yaml", ".yml", ".toml"):
                return UniversalParser._parse_text_with_meta(file_path, max_chars, skip_chars, file_type="config")
            elif suffix in (".zip", ".tar", ".gz", ".tgz"):
                return UniversalParser._parse_archive(file_path)
            elif suffix in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"):
                return UniversalParser._parse_image_meta(file_path)
            elif suffix in (".mp4", ".mov", ".avi", ".mkv", ".webm", ".mp3", ".wav", ".ogg", ".flac"):
                return UniversalParser._parse_media_meta(file_path)
            else:
                # Text or binary fallback
                return UniversalParser._parse_text_or_binary(file_path, max_chars, skip_chars)

        except Exception as e:
            return {
                "path": file_path.name,
                "error": f"Failed to parse {file_path.name}: {str(e)}",
                "total_bytes": file_size,
            }

    @staticmethod
    def _parse_pdf(file_path: Path, max_chars: int, page: int) -> Dict[str, Any]:
        if pypdf is None:
            return {"path": file_path.name, "error": "pypdf library is not installed"}

        reader = pypdf.PdfReader(str(file_path))
        num_pages = len(reader.pages)
        meta = reader.metadata or {}

        # If page requested is within bounds
        target_page_idx = max(0, min(page - 1, num_pages - 1))
        page_obj = reader.pages[target_page_idx]
        extracted_text = page_obj.extract_text() or "(No readable text on this page)"

        header = f"[PDF Document: {file_path.name} | Page {target_page_idx + 1} of {num_pages}]\n"
        if meta.title:
            header += f"Title: {meta.title}\n"
        if meta.author:
            header += f"Author: {meta.author}\n"
        header += "---\n"

        content = (header + extracted_text)[:max_chars]
        is_truncated = len(extracted_text) > max_chars

        return {
            "path": file_path.name,
            "format": "PDF",
            "content": content,
            "page": target_page_idx + 1,
            "total_pages": num_pages,
            "has_more": target_page_idx + 1 < num_pages,
            "is_truncated": is_truncated,
            "total_bytes": file_path.stat().st_size,
        }

    @staticmethod
    def _parse_docx(file_path: Path, max_chars: int, skip_chars: int) -> Dict[str, Any]:
        if docx is None:
            return {"path": file_path.name, "error": "python-docx library is not installed"}

        doc = docx.Document(str(file_path))
        paragraphs = []
        for p in doc.paragraphs:
            if p.text.strip():
                paragraphs.append(p.text.strip())

        for table in doc.tables:
            table_rows = []
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells]
                table_rows.append(" | ".join(cells))
            if table_rows:
                paragraphs.append("\n[Table]\n" + "\n".join(table_rows))

        full_text = "\n\n".join(paragraphs)
        sliced = full_text[skip_chars : skip_chars + max_chars]
        is_truncated = len(full_text) > (skip_chars + max_chars)

        return {
            "path": file_path.name,
            "format": "Word (DOCX)",
            "content": f"[Word Document: {file_path.name} | Total Paragraphs: {len(doc.paragraphs)}]\n---\n{sliced}",
            "is_truncated": is_truncated,
            "total_bytes": file_path.stat().st_size,
            "next_offset": skip_chars + len(sliced) if is_truncated else None,
        }

    @staticmethod
    def _parse_excel(file_path: Path, max_chars: int) -> Dict[str, Any]:
        if openpyxl is None:
            return {"path": file_path.name, "error": "openpyxl library is not installed"}

        wb = openpyxl.load_workbook(str(file_path), read_only=True, data_only=True)
        sheet_names = wb.sheetnames

        sections = [f"[Excel Workbook: {file_path.name} | Sheets: {', '.join(sheet_names)}]"]

        # Preview active or first sheet
        active_sheet = wb.active or wb[sheet_names[0]]
        sections.append(f"\n--- Sheet: '{active_sheet.title}' (Top Preview) ---")

        rows_preview = []
        try:
            for r_idx, row in enumerate(active_sheet.iter_rows(values_only=True), 1):
                if r_idx > 25:  # Top 25 rows
                    sections.append(f"... (Additional rows omitted. Total inspected: {r_idx})")
                    break
                cells = [str(val) if val is not None else "" for val in row]
                if any(cells):
                    rows_preview.append(" | ".join(cells))

            sections.append("\n".join(rows_preview))
            full_text = "\n".join(sections)[:max_chars]
        finally:
            wb.close()

        return {
            "path": file_path.name,
            "format": "Excel (XLSX)",
            "content": full_text,
            "sheets": sheet_names,
            "total_bytes": file_path.stat().st_size,
        }

    @staticmethod
    def _parse_pptx(file_path: Path, max_chars: int) -> Dict[str, Any]:
        # Parse pptx using standard library zipfile XML reading
        slides_text = []
        with zipfile.ZipFile(file_path, "r") as z:
            slide_names = sorted(
                [n for n in z.namelist() if n.startswith("ppt/slides/slide") and n.endswith(".xml")],
                key=lambda x: int("".join(filter(str.isdigit, x)) or 0),
            )
            for idx, s_name in enumerate(slide_names, 1):
                try:
                    xml_content = z.read(s_name).decode("utf-8", errors="ignore")
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(xml_content, "xml")
                    texts = [t.get_text() for t in soup.find_all("a:t") if t.get_text()]
                    if texts:
                        slides_text.append(f"[Slide {idx}]\n" + "\n".join(texts))
                except Exception:
                    continue

        header = f"[PowerPoint Presentation: {file_path.name} | Total Slides: {len(slides_text)}]\n---\n"
        full_content = (header + "\n\n".join(slides_text))[:max_chars]

        return {
            "path": file_path.name,
            "format": "PowerPoint (PPTX)",
            "content": full_content,
            "total_slides": len(slides_text),
            "total_bytes": file_path.stat().st_size,
        }

    @staticmethod
    def _parse_delimited(file_path: Path, max_chars: int, delimiter: str = ",") -> Dict[str, Any]:
        rows = []
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f, delimiter=delimiter)
            for idx, row in enumerate(reader):
                if idx >= 30:
                    break
                rows.append(" | ".join(row))

        table_preview = "\n".join(rows)
        header = f"[Delimited Data: {file_path.name}]\n---\n"
        content = (header + table_preview)[:max_chars]

        return {
            "path": file_path.name,
            "format": "CSV/TSV",
            "content": content,
            "total_bytes": file_path.stat().st_size,
        }

    @staticmethod
    def _parse_notebook(file_path: Path, max_chars: int) -> Dict[str, Any]:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            nb = json.load(f)

        cells = nb.get("cells", [])
        formatted = [f"[Jupyter Notebook: {file_path.name} | Total Cells: {len(cells)}]\n---"]

        for idx, cell in enumerate(cells, 1):
            cell_type = cell.get("cell_type", "code")
            source = "".join(cell.get("source", []))
            if cell_type == "markdown":
                formatted.append(f"\n[Cell {idx} (Markdown)]\n{source}")
            elif cell_type == "code":
                formatted.append(f"\n[Cell {idx} (Code)]\n```python\n{source}\n```")
                # Include textual outputs if small
                outputs = cell.get("outputs", [])
                out_texts = []
                for out in outputs:
                    if "text" in out:
                        out_texts.append("".join(out["text"]))
                if out_texts:
                    formatted.append("Output:\n" + "\n".join(out_texts[:2]))

        full_content = "\n".join(formatted)[:max_chars]

        return {
            "path": file_path.name,
            "format": "Jupyter Notebook (IPYNB)",
            "content": full_content,
            "total_cells": len(cells),
            "total_bytes": file_path.stat().st_size,
        }

    @staticmethod
    def _parse_json(file_path: Path, max_chars: int) -> Dict[str, Any]:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            try:
                data = json.load(f)
                if isinstance(data, dict):
                    summary = f"JSON Object with keys: {list(data.keys())[:20]}"
                elif isinstance(data, list):
                    summary = f"JSON Array with {len(data)} items"
                else:
                    summary = f"JSON Value: {type(data).__name__}"
                formatted = json.dumps(data, indent=2)
                content = f"[JSON File: {file_path.name} | {summary}]\n---\n{formatted}"[:max_chars]
            except Exception:
                f.seek(0)
                content = f.read(max_chars)

        return {
            "path": file_path.name,
            "format": "JSON",
            "content": content,
            "total_bytes": file_path.stat().st_size,
        }

    @staticmethod
    def _parse_archive(file_path: Path) -> Dict[str, Any]:
        files_list = []
        suffix = file_path.suffix.lower()

        if suffix == ".zip":
            with zipfile.ZipFile(file_path, "r") as z:
                for info in z.infolist()[:50]:
                    files_list.append(f"- {info.filename} ({info.file_size:,} bytes)")
        elif suffix in (".tar", ".gz", ".tgz"):
            with tarfile.open(file_path, "r:*") as t:
                for member in t.getmembers()[:50]:
                    files_list.append(f"- {member.name} ({member.size:,} bytes)")

        content = f"[Archive: {file_path.name} | Files Listed: {len(files_list)}]\n---\n" + "\n".join(files_list)

        return {
            "path": file_path.name,
            "format": "Archive",
            "content": content,
            "total_bytes": file_path.stat().st_size,
        }

    @staticmethod
    def _parse_image_meta(file_path: Path) -> Dict[str, Any]:
        with Image.open(file_path) as img:
            w, h = img.size
            mode = img.mode
            fmt = img.format

        content = (
            f"[Image File: {file_path.name}]\n"
            f"- Dimensions: {w} x {h}\n"
            f"- Format: {fmt}\n"
            f"- Color Mode: {mode}\n"
            f"- Size: {file_path.stat().st_size:,} bytes\n"
            f"Use 'inspect_image' tool for detailed visual and EXIF analysis."
        )

        return {
            "path": file_path.name,
            "format": f"Image ({fmt})",
            "content": content,
            "dimensions": f"{w}x{h}",
            "total_bytes": file_path.stat().st_size,
        }

    @staticmethod
    def _parse_media_meta(file_path: Path) -> Dict[str, Any]:
        suffix = file_path.suffix.lower()
        content = (
            f"[Media File: {file_path.name}]\n"
            f"- Type: {suffix[1:].upper()}\n"
            f"- File Size: {file_path.stat().st_size:,} bytes\n"
            f"Media file can be rendered directly in the chat window."
        )
        return {
            "path": file_path.name,
            "format": f"Media ({suffix[1:].upper()})",
            "content": content,
            "total_bytes": file_path.stat().st_size,
        }

    @staticmethod
    def _parse_text_or_binary(file_path: Path, max_chars: int, skip_chars: int) -> Dict[str, Any]:
        file_size = file_path.stat().st_size
        # Sample first 512 bytes to check if binary
        with open(file_path, "rb") as f:
            chunk = f.read(512)

        is_binary = b"\x00" in chunk

        if is_binary:
            hex_sample = chunk[:64].hex(" ")
            content = (
                f"[Binary File: {file_path.name} | Size: {file_size:,} bytes]\n"
                f"Binary data detected. File signature preview (hex):\n{hex_sample}..."
            )
            return {
                "path": file_path.name,
                "format": "Binary",
                "content": content,
                "total_bytes": file_size,
                "is_binary": True,
            }

        # Clean text file
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            if skip_chars > 0:
                f.seek(skip_chars)
            text_data = f.read(max_chars)
            is_truncated = f.read(1) != ""

        return {
            "path": file_path.name,
            "format": "Text / Code",
            "content": text_data,
            "total_bytes": file_size,
            "is_truncated": is_truncated,
            "next_offset": skip_chars + len(text_data) if is_truncated else None,
        }

    @staticmethod
    def _parse_text_with_meta(file_path: Path, max_chars: int, skip_chars: int, file_type: str) -> Dict[str, Any]:
        res = UniversalParser._parse_text_or_binary(file_path, max_chars, skip_chars)
        res["format"] = file_type.upper()
        return res
