from io import BytesIO

from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.config import Settings
from app.main import create_app
from app import service as service_module


def test_end_to_end_api(tmp_path):
    app = create_app(Settings(db_path=str(tmp_path / "api.db")))
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["reranker_active"] is False
        assert health.json()["reranker_can_start"] is True
        assert health.json()["reranker_provider"] == "none"

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


def test_chat_document_scope_is_enforced_by_api(tmp_path):
    app = create_app(Settings(db_path=str(tmp_path / "scope.db")))
    with TestClient(app) as client:
        original = client.post(
            "/api/documents",
            json={"title": "原始审稿", "source": "test", "content": "原始审稿意见质疑理论假设，并要求补充实验设计与效率对比。"},
        )
        assert original.status_code == 200
        original_id = original.json()["document_id"]
        client.post(
            "/api/documents",
            json={"title": "作者回复", "source": "test", "content": "作者 rebuttal 回应了理论假设，并补充了实验设计与效率对比。"},
        )

        response = client.post(
            "/api/chat",
            json={"query": "理论假设与实验设计的意见是什么？", "document_ids": [original_id]},
        )

        assert response.status_code == 200
        citations = response.json()["citations"]
        assert citations
        assert all(item["document_id"] == original_id for item in citations)


def test_api_uploads_xlsx_with_sheet_row_citation(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "实验结果"
    sheet.append(["方法", "准确率"])
    sheet.append(["Holo", 85.43])
    buffer = BytesIO()
    workbook.save(buffer)

    app = create_app(Settings(db_path=str(tmp_path / "xlsx.db")))
    with TestClient(app) as client:
        uploaded = client.post(
            "/api/documents/upload",
            files={"file": ("holo-results.xlsx", buffer.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert uploaded.status_code == 200

        answered = client.post("/api/chat", json={"query": "Holo 的准确率是多少？"})
        assert answered.status_code == 200
        citation = answered.json()["citations"][0]
        assert citation["filename"] == "holo-results.xlsx"
        assert citation["section"] == "工作表：实验结果｜行 1-2"


def test_optional_api_key_protects_api_routes(tmp_path):
    app = create_app(Settings(db_path=str(tmp_path / "secured.db"), app_api_key="test-secret"))
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/documents").status_code == 401
        assert client.get("/api/documents", headers={"X-API-Key": "test-secret"}).status_code == 200


def test_reranker_toggle_reports_force_disabled_configuration(tmp_path):
    app = create_app(Settings(db_path=str(tmp_path / "disabled-reranker.db"), reranker_provider="none"))
    with TestClient(app) as client:
        response = client.post("/api/reranker/toggle")
        assert response.status_code == 409
        assert "disabled" in response.json()["detail"]


def test_reranker_toggle_can_lazy_start_cpu_mode(monkeypatch, tmp_path):
    calls = []

    class FakeReranker:
        def score(self, query, passages):
            return [0.0] * len(passages)

    def fake_build(_settings, allow_cpu=False):
        calls.append(allow_cpu)
        return FakeReranker() if allow_cpu else None

    monkeypatch.setattr(service_module, "build_reranker", fake_build)
    app = create_app(Settings(db_path=str(tmp_path / "manual-cpu-reranker.db"), reranker_provider="auto"))
    with TestClient(app) as client:
        assert client.get("/health").json()["reranker_available"] is False
        enabled = client.post("/api/reranker/toggle")
        assert enabled.status_code == 200
        assert enabled.json()["active"] is True
        disabled = client.post("/api/reranker/toggle")
        assert disabled.status_code == 200
        assert disabled.json()["active"] is False
    assert calls == [False, True]


def test_web_ui_exposes_upload_and_citation_surfaces(tmp_path):
    app = create_app(Settings(db_path=str(tmp_path / "web.db")))
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "uploadSelectedFiles" in page.text
        assert "可多选；选择后自动上传并解析" in page.text
        assert "multiple" in page.text
        assert "检索范围" in page.text
        assert "scope-document" in page.text
        assert "reranker-toggle" in page.text
        assert "/api/reranker/toggle" in page.text
        assert "page-aware citations" not in page.text  # UI stays Chinese-facing
        assert "选择 PDF / DOCX / XLSX / Markdown / TXT" in page.text
