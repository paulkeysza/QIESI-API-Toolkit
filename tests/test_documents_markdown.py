import base64

from fastapi.testclient import TestClient

import main


client = TestClient(main.app)


def test_openapi_includes_server_url():
    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert response.json()["servers"][0]["url"].startswith("http://127.0.0.1:")


def test_openapi_exposes_k2_json_contract():
    response = client.get("/openapi.json")
    operation = response.json()["paths"]["/documents/markdown/k2"]["post"]

    assert operation["operationId"] == "convert_document_to_markdown_k2"
    assert "application/json" in operation["requestBody"]["content"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/DocumentMarkdownResponse"
    )


def test_documents_markdown_requires_file_or_payload():
    response = client.post("/documents/markdown")

    assert response.status_code == 400
    assert response.json()["detail"] == "No file upload or JSON base64 payload provided."


def test_documents_markdown_rejects_unsupported_file_type():
    response = client.post(
        "/documents/markdown",
        files={"file": ("report.exe", b"not a document", "application/octet-stream")},
    )

    assert response.status_code == 415
    assert "Unsupported file type" in response.json()["detail"]


def test_documents_markdown_rejects_empty_upload():
    response = client.post(
        "/documents/markdown",
        files={"file": ("report.pdf", b"", "application/pdf")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Uploaded file is empty."


def test_documents_markdown_rejects_invalid_pdf_signature():
    response = client.post(
        "/documents/markdown",
        files={"file": ("report.pdf", b"plain text", "application/pdf")},
    )

    assert response.status_code == 400
    assert "does not look like a valid PDF" in response.json()["detail"]


def test_documents_markdown_rejects_oversized_upload(monkeypatch):
    monkeypatch.setenv("QIESI_MAX_UPLOAD_BYTES", "8")

    response = client.post(
        "/documents/markdown",
        files={"file": ("report.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")},
    )

    assert response.status_code == 413
    assert "Maximum supported upload size is 8 bytes" in response.json()["detail"]


def test_documents_markdown_upload_success(monkeypatch):
    def fake_convert(file_bytes, file_name, content_type, use_ocr=False):
        assert file_bytes.startswith(b"%PDF-")
        assert file_name == "incident-report.pdf"
        assert content_type == "application/pdf"
        assert use_ocr is False
        return {
            "fileName": file_name,
            "contentType": content_type,
            "markdown": "# Incident Report\n\nConverted text.",
            "textLength": 35,
            "success": True,
        }

    monkeypatch.setattr(main, "_convert_document_to_markdown", fake_convert)

    response = client.post(
        "/documents/markdown",
        files={"file": ("incident-report.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")},
    )

    assert response.status_code == 200
    assert response.json()["markdown"] == "# Incident Report\n\nConverted text."
    assert response.json()["success"] is True


def test_documents_markdown_base64_json_success(monkeypatch):
    def fake_convert(file_bytes, file_name, content_type, use_ocr=False):
        assert file_bytes == b"%PDF-1.4\n%%EOF"
        assert file_name == "incident-report.pdf"
        assert content_type is None
        return {
            "fileName": file_name,
            "contentType": "application/pdf",
            "markdown": "# Base64 report",
            "textLength": 15,
            "success": True,
        }

    monkeypatch.setattr(main, "_convert_document_to_markdown", fake_convert)
    encoded = base64.b64encode(b"%PDF-1.4\n%%EOF").decode("ascii")

    response = client.post(
        "/documents/markdown",
        json={
            "fileName": "incident-report.pdf",
            "fileContentBase64": encoded,
        },
    )

    assert response.status_code == 200
    assert response.json()["markdown"] == "# Base64 report"


def test_documents_markdown_k2_success(monkeypatch):
    def fake_convert(file_bytes, file_name, content_type, use_ocr=False):
        assert file_bytes == b"%PDF-1.4\n%%EOF"
        assert file_name == "incident-report.pdf"
        assert content_type is None
        return {
            "fileName": file_name,
            "contentType": "application/pdf",
            "markdown": "# K2 report",
            "textLength": 11,
            "success": True,
        }

    monkeypatch.setattr(main, "_convert_document_to_markdown", fake_convert)
    encoded = base64.b64encode(b"%PDF-1.4\n%%EOF").decode("ascii")

    response = client.post(
        "/documents/markdown/k2",
        json={"fileName": "incident-report.pdf", "fileContentBase64": encoded},
    )

    assert response.status_code == 200
    assert response.json()["markdown"] == "# K2 report"
    assert response.json()["textLength"] == 11


def test_documents_markdown_rejects_incomplete_json_payload():
    response = client.post(
        "/documents/markdown",
        json={"fileName": "incident-report.pdf"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "JSON requests require fileName and fileContentBase64 values."
