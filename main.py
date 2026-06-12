import os
import re
import json
import base64
import binascii
import csv
import mimetypes
import logging
from io import BytesIO, StringIO
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile, File, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError
from dotenv import load_dotenv
from openpyxl import Workbook
from markitdown import MarkItDown, StreamInfo, PRIORITY_SPECIFIC_FILE_FORMAT
from markitdown import UnsupportedFormatException, MissingDependencyException, FileConversionException

try:
    from markitdown.converters._doc_intel_converter import DocumentIntelligenceConverter
except Exception:
    DocumentIntelligenceConverter = None

load_dotenv()

logger = logging.getLogger("qiesi-api-toolkit")

PUBLIC_BASE_URL_ENV = "QIESI_PUBLIC_BASE_URL"
public_base_url = os.getenv(PUBLIC_BASE_URL_ENV, "http://127.0.0.1:8000").rstrip("/")

app = FastAPI(
    title="Qiesi API Toolkit",
    version="1.1.3",
    servers=[{"url": public_base_url, "description": "QIESI API Toolkit"}],
    description="""
A lightweight API toolkit providing:

• JSON → Excel conversion  
• Text → CSV conversion  
• Document/Markdown conversion for AI, workflows, and automation

Designed for Nintex NAC workflow integrations.
"""
)

DEFAULT_CORS_ORIGINS = [
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:8000",
    "http://localhost:8000",
]

cors_origins = [
    origin.strip()
    for origin in os.getenv("QIESI_CORS_ORIGINS", ",".join(DEFAULT_CORS_ORIGINS)).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent / "frontend"
if STATIC_DIR.exists():
    app.mount("/test-api", StaticFiles(directory=STATIC_DIR, html=True), name="test-api")


# -----------------------------
# Request Models
# -----------------------------

class ConvertRequest(BaseModel):
    jsonInput: str


class MessageCSVRequest(BaseModel):
    message: str


class DocumentBase64Request(BaseModel):
    fileName: str = Field(description="Original document file name including its extension.")
    fileContentBase64: str = Field(description="Base64-encoded document file content.")


class DocumentMarkdownResponse(BaseModel):
    fileName: str
    contentType: str
    markdown: str
    textLength: int
    success: bool

SUPPORTED_DOCUMENT_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".xlsx",
    ".pptx",
    ".txt",
    ".csv",
    ".html",
    ".htm",
    ".json",
    ".xml",
    ".md",
    ".jpeg",
    ".jpg",
    ".png",
    ".bmp",
    ".tiff",
}

OCR_DOCUMENT_EXTENSIONS = {".pdf", ".jpeg", ".jpg", ".png", ".bmp", ".tiff"}

DEFAULT_CONTENT_TYPE_MAPPING = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".html": "text/html",
    ".htm": "text/html",
    ".json": "application/json",
    ".xml": "application/xml",
    ".md": "text/markdown",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
}

DOCUMENT_INTELLIGENCE_ENDPOINT_ENV = "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT"
MAX_UPLOAD_BYTES_ENV = "QIESI_MAX_UPLOAD_BYTES"
DEFAULT_MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def _get_max_upload_bytes() -> int:
    raw_value = os.getenv(MAX_UPLOAD_BYTES_ENV)
    if not raw_value:
        return DEFAULT_MAX_UPLOAD_BYTES
    try:
        parsed = int(raw_value)
    except ValueError:
        logger.warning("Invalid %s value %r. Falling back to default.", MAX_UPLOAD_BYTES_ENV, raw_value)
        return DEFAULT_MAX_UPLOAD_BYTES
    return parsed if parsed > 0 else DEFAULT_MAX_UPLOAD_BYTES


def _get_file_extension(file_name: str) -> str:
    return os.path.splitext(file_name)[1].lower()


def _get_content_type(file_name: str, extension: str) -> str:
    content_type, _ = mimetypes.guess_type(file_name)
    if content_type:
        return content_type
    return DEFAULT_CONTENT_TYPE_MAPPING.get(extension, "application/octet-stream")


def _ensure_supported_extension(file_name: str, content_type: Optional[str]) -> str:
    extension = _get_file_extension(file_name)
    if not extension and content_type:
        extension = mimetypes.guess_extension(content_type) or ""
    if extension in SUPPORTED_DOCUMENT_EXTENSIONS:
        return extension
    raise HTTPException(
        status_code=415,
        detail=(
            f"Unsupported file type for '{file_name}'. "
            f"Supported extensions: {', '.join(sorted(SUPPORTED_DOCUMENT_EXTENSIONS))}."
        ),
    )


def _decode_base64_file_content(file_content_base64: str) -> bytes:
    try:
        decoded = base64.b64decode(file_content_base64, validate=True)
    except binascii.Error as exc:
        raise HTTPException(status_code=400, detail="Invalid base64 fileContentBase64.") from exc
    _validate_document_bytes(decoded, "Decoded file content")
    return decoded


async def _read_document_request(
    request: Request,
    file: Optional[UploadFile],
) -> tuple[bytes, str, Optional[str]]:
    content_type = request.headers.get("content-type", "").lower()

    if content_type.startswith("application/json"):
        if file is not None:
            raise HTTPException(
                status_code=400,
                detail="Provide either a multipart file upload or a JSON base64 payload, not both.",
            )
        try:
            payload = DocumentBase64Request.model_validate(await request.json())
        except (ValueError, ValidationError) as exc:
            raise HTTPException(
                status_code=400,
                detail="JSON requests require fileName and fileContentBase64 values.",
            ) from exc
        return (
            _decode_base64_file_content(payload.fileContentBase64),
            payload.fileName,
            None,
        )

    if file is None:
        raise HTTPException(
            status_code=400,
            detail="No file upload or JSON base64 payload provided.",
        )

    upload_content_type = file.content_type
    upload_file_name = file.filename or "uploaded-document"
    try:
        chunks = []
        total_bytes = 0
        max_upload_bytes = _get_max_upload_bytes()
        while chunk := await file.read(1024 * 1024):
            total_bytes += len(chunk)
            if total_bytes > max_upload_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        "Uploaded file is too large. Maximum supported upload size is "
                        f"{max_upload_bytes} bytes."
                    ),
                )
            chunks.append(chunk)
        file_bytes = b"".join(chunks)
    finally:
        await file.close()

    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    return file_bytes, upload_file_name, upload_content_type


def _validate_document_bytes(file_bytes: bytes, label: str) -> None:
    if not file_bytes:
        raise HTTPException(status_code=400, detail=f"{label} is empty.")

    max_upload_bytes = _get_max_upload_bytes()
    if len(file_bytes) > max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"{label} is too large. Maximum supported upload size is "
                f"{max_upload_bytes} bytes."
            ),
        )


def _validate_pdf_signature(file_bytes: bytes, file_name: str, extension: str) -> None:
    if extension == ".pdf" and b"%PDF-" not in file_bytes[:1024]:
        raise HTTPException(
            status_code=400,
            detail=f"'{file_name}' has a .pdf extension but does not look like a valid PDF file.",
        )


def _convert_document_to_markdown(
    file_bytes: bytes,
    file_name: str,
    content_type: Optional[str],
    use_ocr: bool = False,
) -> dict:
    _validate_document_bytes(file_bytes, "Document file content")
    extension = _ensure_supported_extension(file_name, content_type)
    _validate_pdf_signature(file_bytes, file_name, extension)
    content_type = _get_content_type(file_name, extension)
    stream_info = StreamInfo(mimetype=content_type, extension=extension, filename=file_name)

    markdowner = MarkItDown(enable_builtins=True)
    ocr_applied = False

    if use_ocr:
        if DocumentIntelligenceConverter is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "OCR endpoint requires optional dependencies from `markitdown[az-doc-intel]`. "
                    "Install the optional package and configure the Azure Document Intelligence endpoint."
                ),
            )

        endpoint = os.getenv(DOCUMENT_INTELLIGENCE_ENDPOINT_ENV)
        if not endpoint:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"OCR endpoint requires the {DOCUMENT_INTELLIGENCE_ENDPOINT_ENV} environment variable "
                    "to be configured with a valid Azure Document Intelligence endpoint."
                ),
            )

        try:
            doc_intel_converter = DocumentIntelligenceConverter(endpoint=endpoint)
            markdowner.register_converter(doc_intel_converter, priority=PRIORITY_SPECIFIC_FILE_FORMAT)
        except MissingDependencyException as exc:
            raise HTTPException(
                status_code=400,
                detail=(
                    "OCR support requires optional Azure Document Intelligence dependencies. "
                    "Install with `pip install markitdown[az-doc-intel]`."
                ),
            ) from exc

        ocr_applied = extension in OCR_DOCUMENT_EXTENSIONS or (content_type and content_type.startswith("image/"))

    try:
        result = markdowner.convert_stream(BytesIO(file_bytes), stream_info=stream_info)
    except UnsupportedFormatException as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except MissingDependencyException as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileConversionException as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Document conversion failed for %s", file_name)
        raise HTTPException(
            status_code=400,
            detail="Document conversion failed. Check that the file is readable and contains extractable text.",
        ) from exc

    markdown = result.markdown or ""
    response = {
        "fileName": file_name,
        "contentType": content_type,
        "markdown": markdown,
        "textLength": len(markdown),
        "success": True,
    }

    if use_ocr:
        response["ocrApplied"] = ocr_applied

    return response


# -----------------------------
# System Endpoints
# -----------------------------

@app.get("/health", tags=["System"])
def health():
    return {"status": "ok"}


@app.get("/info", tags=["System"])
def info():
    return {
        "name": "Qiesi API Toolkit",
        "version": "1.1.3",
        "author": "Paul Keys",
        "description": "API toolkit for Nintex workflow integrations",
        "endpoints": {
            "health": "/health",
            "ping": "/ping",
            "json_to_xlsx": "/JSON-to-XLSX",
            "text_to_csv": "/TXT-to-CSV",
            "document_to_markdown": "/documents/markdown",
            "document_to_markdown_k2": "/documents/markdown/k2",
            "document_to_markdown_ocr": "/documents/markdown/ocr",
            "document_markdown_test_ui": "/test-api/",
            "docs": "/docs",
            "openapi": "/openapi.json"
        }
    }


@app.get("/ping", tags=["System"])
def ping():
    return {
        "message": "pong",
        "api": "Qiesi Toolkit API"
    }


# -----------------------------
# JSON → Excel Conversion
# -----------------------------

@app.post(
    "/JSON-to-XLSX",
    tags=["Conversion"],
    summary="JSON-to-MS Excel",
    description="Accepts JSON and returns it as a Base64 encoded Excel (.xlsx) file."
)
def convert(req: ConvertRequest):

    try:

        data = json.loads(req.jsonInput)

        if isinstance(data, dict):
            data = [data]

        wb = Workbook()
        ws = wb.active
        ws.title = "Data"

        headers = list(data[0].keys())
        ws.append(headers)

        for row in data:
            ws.append([row.get(h) for h in headers])

        buffer = BytesIO()
        wb.save(buffer)
        buffer.seek(0)

        encoded = base64.b64encode(buffer.read()).decode()

        filename = f"QiesiAPI-JSONtoXLSX-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.xlsx"

        return {
            "fileName": filename,
            "excelFile": encoded
        }

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post(
    "/documents/markdown",
    tags=["Document Conversion"],
    summary="Document-to-Markdown",
    description="Convert uploaded documents or base64 document payloads into Markdown for AI, automation, and workflow scenarios.",
)
async def document_to_markdown(
    request: Request,
    file: Optional[UploadFile] = File(None),
):
    file_bytes, file_name, content_type = await _read_document_request(request, file)
    return _convert_document_to_markdown(
        file_bytes=file_bytes,
        file_name=file_name,
        content_type=content_type,
    )


@app.post(
    "/documents/markdown/k2",
    tags=["Document Conversion"],
    summary="K2 Document-to-Markdown",
    description=(
        "K2-friendly JSON endpoint that accepts a file name and Base64 document content, "
        "then returns explicit Markdown response properties."
    ),
    response_model=DocumentMarkdownResponse,
    operation_id="convert_document_to_markdown_k2",
)
def document_to_markdown_k2(payload: DocumentBase64Request):
    return _convert_document_to_markdown(
        file_bytes=_decode_base64_file_content(payload.fileContentBase64),
        file_name=payload.fileName,
        content_type=None,
    )


@app.post(
    "/documents/markdown/ocr",
    tags=["Document Conversion"],
    summary="Document OCR-to-Markdown",
    description="Convert scanned or image-heavy documents into Markdown using optional Azure Document Intelligence OCR support.",
)
async def document_to_markdown_ocr(
    request: Request,
    file: Optional[UploadFile] = File(None),
):
    file_bytes, file_name, content_type = await _read_document_request(request, file)
    return _convert_document_to_markdown(
        file_bytes=file_bytes,
        file_name=file_name,
        content_type=content_type,
        use_ocr=True,
    )


# -----------------------------
# Text → CSV Conversion
# Supports multiline input
# -----------------------------

@app.post(
    "/TXT-to-CSV",
    tags=["Conversion"],
    summary="Text-To-CSV",
    description="Accepts text and returns it as a Base64 encoded CSV file."
)
def message_to_csv(req: MessageCSVRequest):

    try:

        output = StringIO()
        writer = csv.writer(output)

        lines = req.message.splitlines()

        for line in lines:
            writer.writerow([line])

        csv_bytes = output.getvalue().encode("utf-8")

        encoded = base64.b64encode(csv_bytes).decode()

        filename = f"QiesiAPI-TextToCSV-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.csv"

        return {
            "fileName": filename,
            "csvFile": encoded
        }

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
