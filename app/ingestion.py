from __future__ import annotations

import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from docx import Document
from openpyxl import load_workbook
from pypdf import PdfReader


SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".md", ".txt"}


@dataclass(frozen=True)
class TextBlock:
    content: str
    page: int | None = None
    section: str | None = None


@dataclass(frozen=True)
class ParsedDocument:
    title: str
    source: str
    filename: str
    media_type: str
    content: str
    blocks: list[TextBlock]


def _normalize(text: str) -> str:
    # PDF extractors occasionally emit lone UTF-16 surrogates. SQLite's UTF-8
    # encoder rejects them, so normalize them at the ingestion boundary instead
    # of letting one malformed glyph invalidate an otherwise usable document.
    safe_text = text.encode("utf-8", errors="replace").decode("utf-8")
    return re.sub(r"\n{3,}", "\n\n", safe_text.replace("\r\n", "\n")).strip()


def _markdown_blocks(text: str) -> list[TextBlock]:
    blocks: list[TextBlock] = []
    current_section = ""
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer
        if buffer:
            blocks.append(TextBlock(_normalize("\n".join(buffer)), section=current_section or None))
            buffer = []

    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            flush()
            current_section = line.lstrip("# ").strip()
        else:
            buffer.append(line)
    flush()
    return [block for block in blocks if block.content]


def _spreadsheet_blocks(payload: bytes) -> list[TextBlock]:
    """Serialize an XLSX workbook as evidence-bearing table rows.

    Each block records its worksheet and inclusive row range. Formulas are
    preserved as formulas rather than executed; macros are never run. This
    makes spreadsheet evidence inspectable without claiming spreadsheet-agent
    capabilities that the service does not provide.
    """
    workbook = load_workbook(BytesIO(payload), read_only=True, data_only=False)
    blocks: list[TextBlock] = []
    for sheet in workbook.worksheets:
        rows: list[str] = []
        headers: list[str] = []
        start_row: int | None = None
        end_row: int | None = None

        def flush() -> None:
            nonlocal rows, start_row, end_row
            if rows and start_row is not None and end_row is not None:
                section = f"工作表：{sheet.title}｜行 {start_row}-{end_row}"
                blocks.append(TextBlock(_normalize("\n".join(rows)), section=section))
            rows = []
            start_row = None
            end_row = None

        for row_number, cells in enumerate(sheet.iter_rows(), start=1):
            values = ["" if cell.value is None else str(cell.value).strip() for cell in cells]
            if not any(values):
                continue
            if not headers:
                headers = [value or f"列{index}" for index, value in enumerate(values, start=1)]
                continue
            fields = [
                f"{headers[index] if index < len(headers) else f'列{index + 1}'}：{value}"
                for index, value in enumerate(values)
                if value
            ]
            if not fields:
                continue
            rows.append(f"行 {row_number}｜" + "；".join(fields))
            start_row = row_number if start_row is None else start_row
            end_row = row_number
            # Keep blocks compact enough that citations can point to a useful
            # row range before generic text chunking applies its own split.
            if len(rows) >= 40:
                flush()
        flush()
    return blocks


def parse_upload(filename: str, payload: bytes, source: str = "upload") -> ParsedDocument:
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError("only PDF, DOCX, XLSX, Markdown, and TXT files are supported")
    if not payload:
        raise ValueError("uploaded file is empty")

    title = Path(filename).stem[:200] or "untitled"
    if suffix == ".pdf":
        reader = PdfReader(BytesIO(payload))
        blocks = [TextBlock(_normalize(page.extract_text() or ""), page=index) for index, page in enumerate(reader.pages, start=1)]
        blocks = [block for block in blocks if block.content]
        media_type = "application/pdf"
    elif suffix == ".docx":
        document = Document(BytesIO(payload))
        paragraphs = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
        # Resumes, reports, and forms often place all visible content in tables.
        # python-docx does not include table cells in ``document.paragraphs``.
        table_rows = []
        for table in document.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if cells:
                    table_rows.append(" | ".join(cells))
        text = _normalize("\n".join([*paragraphs, *table_rows]))
        blocks = _markdown_blocks(text)
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif suffix == ".xlsx":
        try:
            blocks = _spreadsheet_blocks(payload)
        except Exception as exc:
            raise ValueError("could not read XLSX workbook; encrypted or malformed files are not supported") from exc
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        text = _normalize(payload.decode("utf-8", errors="replace"))
        blocks = _markdown_blocks(text) if suffix == ".md" else [TextBlock(text)]
        media_type = "text/markdown" if suffix == ".md" else "text/plain"

    content = _normalize("\n\n".join(block.content for block in blocks))
    if len(content) < 20:
        raise ValueError("no extractable text was found in the uploaded file")
    return ParsedDocument(title, source, filename, media_type, content, blocks)
