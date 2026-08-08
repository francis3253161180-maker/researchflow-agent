from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_end_to_end_api(tmp_path):
    app = create_app(Settings(db_path=str(tmp_path / "api.db")))
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200

        created = client.post(
            "/api/documents",
            json={
                "title": "Agent 测试文档",
                "source": "unit-test",
                "content": "LangGraph 使用状态、节点和边组织 Agent 工作流，并支持条件路由。",
            },
        )
        assert created.status_code == 200
        assert created.json()["chunks"] == 1

        answered = client.post("/api/chat", json={"query": "LangGraph 如何组织工作流？"})
        assert answered.status_code == 200
        payload = answered.json()
        assert payload["verified"] is True
        assert payload["citations"]

        run = client.get(f"/api/runs/{payload['run_id']}")
        assert run.status_code == 200
        assert run.json()["route"] == "rag"


def test_upload_lists_and_deletes_document(tmp_path):
    app = create_app(Settings(db_path=str(tmp_path / "upload.db")))
    content = "# ResearchFlow\n\nLangGraph uses explicit state, nodes, and conditional edges. The system validates citations before persisting an answer."
    with TestClient(app) as client:
        uploaded = client.post(
            "/api/documents/upload?source=unit-upload",
            files={"file": ("research-note.md", content.encode("utf-8"), "text/markdown")},
        )
        assert uploaded.status_code == 200
        document_id = uploaded.json()["document_id"]

        documents = client.get("/api/documents")
        assert documents.status_code == 200
        assert documents.json()[0]["filename"] == "research-note.md"
        assert documents.json()[0]["chunks"] == 1

        answered = client.post("/api/chat", json={"query": "How are citations validated?"})
        citation = answered.json()["citations"][0]
        assert citation["filename"] == "research-note.md"
        assert citation["section"] == "ResearchFlow"

        deleted = client.delete(f"/api/documents/{document_id}")
        assert deleted.status_code == 204
        assert client.get("/api/documents").json() == []


def test_optional_api_key_protects_api_routes(tmp_path):
    app = create_app(Settings(db_path=str(tmp_path / "secured.db"), app_api_key="test-secret"))
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/documents").status_code == 401
        assert client.get("/api/documents", headers={"X-API-Key": "test-secret"}).status_code == 200


def test_web_ui_exposes_upload_and_citation_surfaces(tmp_path):
    app = create_app(Settings(db_path=str(tmp_path / "web.db")))
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "uploadSelectedFile" in page.text
        assert "选择后自动上传并解析" in page.text
        assert "page-aware citations" not in page.text  # UI stays Chinese-facing
        assert "选择 PDF / DOCX / Markdown / TXT" in page.text
