from __future__ import annotations

import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from docx import Document
from pypdf import PdfReader


SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".md", ".txt"}


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
    return re.sub(r"\n{3,}", "\n\n", text.replace("\r\n", "\n")).strip()


def _markdown_blocks(text: str) -> list[TextBlock]:
    blocks: list[TextBlock] = []
    current_section = ""
    buffer: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            if buffer:
                blocks.append(TextBlock(_normalize("\n".join(buffer)), section=current_section or None))
                buffer = []
            current_section = line.lstrip("# ").strip()
        else:
            buffer.append(line)
    if buffer:
        blocks.append(TextBlock(_normalize("\n".join(buffer)), section=current_section or None))
    return [block for block in blocks if block.content]


def parse_upload(filename: str, payload: bytes, source: str = "upload") -> ParsedDocument:
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError("only PDF, DOCX, Markdown, and TXT files are supported")
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
        text = _normalize("\n".join(paragraph.text for paragraph in document.paragraphs))
        blocks = _markdown_blocks(text)
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        text = _normalize(payload.decode("utf-8", errors="replace"))
        blocks = _markdown_blocks(text) if suffix == ".md" else [TextBlock(text)]
        media_type = "text/markdown" if suffix == ".md" else "text/plain"

    content = _normalize("\n\n".join(block.content for block in blocks))
    if len(content) < 20:
        raise ValueError("no extractable text was found in the uploaded file")
    return ParsedDocument(title, source, filename, media_type, content, blocks)
