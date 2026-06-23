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

    assert operation["operationId"] == "document_to_markdown_k2_documents_markdown_k2_post"
    assert "application/json" in operation["requestBody"]["content"]
    response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert response_schema["type"] == "object"
    assert set(response_schema["properties"]) == {
        "fileName",
        "contentType",
        "markdown",
        "markdownText",
        "textLength",
        "success",
    }

    top_level_operation = response.json()["paths"]["/K2-Markdown"]["post"]
    assert top_level_operation["operationId"] == "k2_document_to_markdown_K2_Markdown_post"
    assert "multipart/form-data" in top_level_operation["requestBody"]["content"]
    assert set(
        top_level_operation["responses"]["200"]["content"]["application/json"]["schema"]["properties"]
    ) == {
        "fileName",
        "contentType",
        "markdown",
        "markdownText",
        "textLength",
        "success",
    }


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
            "markdownText": "# Incident Report\n\nConverted text.",
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
            "markdownText": "# Base64 report",
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
            "markdownText": "# K2 report",
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


def test_documents_markdown_k2_accepts_k2_file_xml_wrapper(monkeypatch):
    def fake_convert(file_bytes, file_name, content_type, use_ocr=False):
        assert file_bytes == b"%PDF-1.4\n%%EOF"
        assert file_name == "wrapped-report.pdf"
        assert content_type is None
        return {
            "fileName": file_name,
            "contentType": "application/pdf",
            "markdown": "# Wrapped K2 report",
            "markdownText": "# Wrapped K2 report",
            "textLength": 19,
            "success": True,
        }

    monkeypatch.setattr(main, "_convert_document_to_markdown", fake_convert)
    encoded = base64.b64encode(b"%PDF-1.4\n%%EOF").decode("ascii")
    wrapped = f"""
<files>
  <file encoding="Base64">
    <name>wrapped-report.pdf</name>
    <content>{encoded}</content>
  </file>
</files>
"""

    response = client.post(
        "/documents/markdown/k2",
        json={"fileName": "ignored-name.pdf", "fileContentBase64": wrapped},
    )

    assert response.status_code == 200
    assert response.json()["fileName"] == "wrapped-report.pdf"
    assert response.json()["markdown"] == "# Wrapped K2 report"


def test_documents_markdown_k2_accepts_single_file_xml_root(monkeypatch):
    def fake_convert(file_bytes, file_name, content_type, use_ocr=False):
        assert file_bytes == b"%PDF-1.4\n%%EOF"
        assert file_name == "single-file-root.pdf"
        assert content_type is None
        return {
            "fileName": file_name,
            "contentType": "application/pdf",
            "markdown": "# Single root K2 report",
            "markdownText": "# Single root K2 report",
            "textLength": 23,
            "success": True,
        }

    monkeypatch.setattr(main, "_convert_document_to_markdown", fake_convert)
    encoded = base64.b64encode(b"%PDF-1.4\n%%EOF").decode("ascii")
    wrapped = f"<file><name>single-file-root.pdf</name><content>{encoded}</content></file>"

    response = client.post(
        "/documents/markdown/k2",
        json={"fileName": "ignored-name.pdf", "fileContentBase64": wrapped},
    )

    assert response.status_code == 200
    assert response.json()["fileName"] == "single-file-root.pdf"
    assert response.json()["markdown"] == "# Single root K2 report"


def test_k2_file_xml_endpoint_accepts_raw_xml(monkeypatch):
    def fake_convert(file_bytes, file_name, content_type, use_ocr=False):
        assert file_bytes == b"%PDF-1.4\n%%EOF"
        assert file_name == "raw-xml.pdf"
        assert content_type is None
        return {
            "fileName": file_name,
            "contentType": "application/pdf",
            "markdown": "# Raw XML K2 report",
            "markdownText": "# Raw XML K2 report",
            "textLength": 19,
            "success": True,
        }

    monkeypatch.setattr(main, "_convert_document_to_markdown", fake_convert)
    encoded = base64.b64encode(b"%PDF-1.4\n%%EOF").decode("ascii")
    wrapped = f"<file><name>raw-xml.pdf</name><content>{encoded}</content></file>"

    response = client.post(
        "/K2-Markdown-Xml",
        content=wrapped,
        headers={"Content-Type": "text/plain"},
    )

    assert response.status_code == 200
    assert response.json()["fileName"] == "raw-xml.pdf"
    assert response.json()["markdown"] == "# Raw XML K2 report"


def test_k2_file_xml_endpoint_accepts_json_file_xml_object(monkeypatch):
    def fake_convert(file_bytes, file_name, content_type, use_ocr=False):
        assert file_bytes == b"%PDF-1.4\n%%EOF"
        assert file_name == "k2-json-object.pdf"
        assert content_type is None
        return {
            "fileName": file_name,
            "contentType": "application/pdf",
            "markdown": "# K2 JSON object report",
            "markdownText": "# K2 JSON object report",
            "textLength": 23,
            "success": True,
        }

    monkeypatch.setattr(main, "_convert_document_to_markdown", fake_convert)
    encoded = base64.b64encode(b"%PDF-1.4\n%%EOF").decode("ascii")
    wrapped = f"<file><name>k2-json-object.pdf</name><content>{encoded}</content></file>"

    response = client.post(
        "/K2-Markdown-Xml",
        json={"fileXml": wrapped},
    )

    assert response.status_code == 200
    assert response.json()["fileName"] == "k2-json-object.pdf"
    assert response.json()["markdown"] == "# K2 JSON object report"


def test_k2_file_xml_json_endpoint_accepts_raw_xml(monkeypatch):
    def fake_convert(file_bytes, file_name, content_type, use_ocr=False):
        assert file_bytes == b"%PDF-1.4\n%%EOF"
        assert file_name == "raw-xml-json.pdf"
        assert content_type is None
        return {
            "fileName": file_name,
            "contentType": "application/pdf",
            "markdown": "# Raw XML JSON K2 report",
            "markdownText": "# Raw XML JSON K2 report",
            "textLength": 24,
            "success": True,
        }

    monkeypatch.setattr(main, "_convert_document_to_markdown", fake_convert)
    encoded = base64.b64encode(b"%PDF-1.4\n%%EOF").decode("ascii")
    wrapped = f"<file><name>raw-xml-json.pdf</name><content>{encoded}</content></file>"

    response = client.post(
        "/K2-Markdown-Xml-Json",
        json={"fileXml": wrapped},
    )

    assert response.status_code == 200
    assert response.json()["fileName"] == "raw-xml-json.pdf"
    assert response.json()["markdown"] == "# Raw XML JSON K2 report"


def test_k2_file_xml_json_text_endpoint_returns_markdown_only(monkeypatch):
    def fake_convert(file_bytes, file_name, content_type, use_ocr=False):
        assert file_bytes == b"%PDF-1.4\n%%EOF"
        assert file_name == "raw-xml-text.pdf"
        assert content_type is None
        return {
            "fileName": file_name,
            "contentType": "application/pdf",
            "markdown": "# Markdown only\n\nNo JSON wrapper.",
            "markdownText": "# Markdown only\n\nNo JSON wrapper.",
            "textLength": 33,
            "success": True,
        }

    monkeypatch.setattr(main, "_convert_document_to_markdown", fake_convert)
    encoded = base64.b64encode(b"%PDF-1.4\n%%EOF").decode("ascii")
    wrapped = f"<file><name>raw-xml-text.pdf</name><content>{encoded}</content></file>"

    response = client.post(
        "/K2-Markdown-Xml-Json-Text",
        json={"fileXml": wrapped},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.text == "# Markdown only\n\nNo JSON wrapper."
    assert not response.text.startswith("{")

def test_top_level_k2_markdown_success(monkeypatch):
    def fake_convert(file_bytes, file_name, content_type, use_ocr=False):
        assert file_bytes.startswith(b"%PDF-")
        assert file_name == "incident-report.pdf"
        assert content_type == "application/pdf"
        assert use_ocr is False
        return {
            "fileName": file_name,
            "contentType": content_type,
            "markdown": "# Top-level K2 report",
            "markdownText": "# Top-level K2 report",
            "textLength": 21,
            "success": True,
        }

    monkeypatch.setattr(
        main,
        "_convert_document_to_markdown",
        fake_convert,
    )

    response = client.post(
        "/K2-Markdown",
        files={"file": ("incident-report.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")},
    )

    assert response.status_code == 200
    assert response.json()["markdown"] == "# Top-level K2 report"


def test_documents_markdown_rejects_incomplete_json_payload():
    response = client.post(
        "/documents/markdown",
        json={"fileName": "incident-report.pdf"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "JSON requests require fileName and fileContentBase64 values."
