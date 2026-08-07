from io import BytesIO

from docx import Document

from app.ingestion import parse_upload
from app.config import Settings


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


def test_settings_loads_local_dotenv_without_overriding_process_env(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "EMBEDDING_PROVIDER=fastembed\nRESEARCHFLOW_APP_API_KEY=from-dotenv\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RESEARCHFLOW_APP_API_KEY", "from-process")

    settings = Settings.from_env()

    assert settings.embedding_provider == "fastembed"
    assert settings.app_api_key == "from-process"
