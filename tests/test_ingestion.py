from io import BytesIO

from docx import Document

from app.ingestion import parse_upload


def test_markdown_parser_preserves_sections():
    parsed = parse_upload(
        "note.md",
        b"# Method\n\nThe method uses hybrid retrieval.\n\n## Evaluation\n\nThe evaluation checks grounded citations.",
    )
    assert parsed.title == "note"
    assert [block.section for block in parsed.blocks] == ["Method", "Evaluation"]


def test_docx_parser_extracts_paragraphs():
    document = Document()
    document.add_heading("Paper Notes", level=1)
    document.add_paragraph("This paragraph records the experimental configuration and outcome.")
    buffer = BytesIO()
    document.save(buffer)

    parsed = parse_upload("paper-notes.docx", buffer.getvalue())
    assert parsed.filename == "paper-notes.docx"
    assert "experimental configuration" in parsed.content
