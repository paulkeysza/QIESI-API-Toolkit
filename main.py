import os
import re
import json
import base64
import binascii
import csv
import mimetypes
import logging
import time
import uuid
from logging.handlers import RotatingFileHandler
from io import BytesIO, StringIO
from datetime import datetime
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree

from fastapi import FastAPI, HTTPException, UploadFile, File, Request, Body
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
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

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
LOG_LEVEL = os.getenv("QIESI_LOG_LEVEL", "INFO").upper()

logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)

logger = logging.getLogger("qiesi-api-toolkit")
file_handler = RotatingFileHandler(
    LOG_DIR / "qiesi-api.log",
    maxBytes=1_000_000,
    backupCount=3,
    encoding="utf-8",
)
file_handler.setLevel(LOG_LEVEL)
file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
logger.addHandler(file_handler)
logger.propagate = True

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


def _preview_bytes(value: bytes, limit: int = 80) -> str:
    preview = value[:limit]
    return preview.decode("utf-8", errors="replace").replace("\r", "\\r").replace("\n", "\\n")


def _preview_text(value: str, limit: int = 120) -> str:
    return value[:limit].replace("\r", "\\r").replace("\n", "\\n")


async def _describe_request_form(request: Request) -> str:
    try:
        form = await request.form()
    except Exception as exc:
        return f"unable to parse form: {exc}"

    descriptions = []
    for key, value in form.multi_items():
        if hasattr(value, "filename"):
            descriptions.append(
                f"{key}=file(filename={value.filename!r}, content_type={value.content_type!r})"
            )
        else:
            text_value = str(value)
            descriptions.append(
                f"{key}=field(length={len(text_value)}, preview={_preview_text(text_value)!r})"
            )
    return "; ".join(descriptions) if descriptions else "no form fields"


@app.middleware("http")
async def log_requests(request: Request, call_next):
    request_id = uuid.uuid4().hex[:8]
    started = time.perf_counter()
    content_type = request.headers.get("content-type", "")
    content_length = request.headers.get("content-length", "")

    logger.info(
        "request %s start method=%s path=%s content_type=%r content_length=%r client=%s",
        request_id,
        request.method,
        request.url.path,
        content_type,
        content_length,
        request.client.host if request.client else None,
    )

    try:
        response = await call_next(request)
    except Exception:
        logger.exception("request %s failed before response path=%s", request_id, request.url.path)
        raise

    elapsed_ms = (time.perf_counter() - started) * 1000
    logger.info(
        "request %s complete status=%s elapsed_ms=%.1f path=%s",
        request_id,
        response.status_code,
        elapsed_ms,
        request.url.path,
    )
    return response


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.warning(
        "validation failed path=%s content_type=%r errors=%s",
        request.url.path,
        request.headers.get("content-type", ""),
        exc.errors(),
    )

    if (request.headers.get("content-type") or "").lower().startswith("multipart/form-data"):
        logger.warning(
            "validation failed multipart fields path=%s fields=%s",
            request.url.path,
            await _describe_request_form(request),
        )

    return JSONResponse(status_code=422, content={"detail": exc.errors()})


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
    markdownText: str = Field(description="Markdown-only text value for K2 output mapping.")
    textLength: int
    success: bool


class K2FileXmlRequest(BaseModel):
    fileXml: str = Field(description="Raw K2 file XML containing <name> and Base64 <content> values.")

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
    logger.info(
        "decoding base64 file content chars=%s preview=%r",
        len(file_content_base64 or ""),
        _preview_text(file_content_base64 or "", 40),
    )
    try:
        decoded = base64.b64decode(file_content_base64, validate=True)
    except binascii.Error as exc:
        logger.warning(
            "base64 decode failed chars=%s preview=%r",
            len(file_content_base64 or ""),
            _preview_text(file_content_base64 or "", 80),
        )
        raise HTTPException(status_code=400, detail="Invalid base64 fileContentBase64.") from exc
    _validate_document_bytes(decoded, "Decoded file content")
    logger.info(
        "base64 decode succeeded bytes=%s bytes_preview=%r",
        len(decoded),
        _preview_bytes(decoded),
    )
    return decoded


def _extract_k2_file_xml_content(value: str) -> tuple[Optional[str], str]:
    cleaned = (value or "").strip()
    if not cleaned.startswith("<"):
        return None, value

    try:
        root = ElementTree.fromstring(cleaned)
    except ElementTree.ParseError as exc:
        logger.warning("K2 file XML parse failed error=%s preview=%r", exc, _preview_text(cleaned, 160))
        return None, value

    file_node = root if root.tag == "file" else root.find(".//file")
    if file_node is None:
        logger.warning("K2 file XML did not contain a file element preview=%r", _preview_text(cleaned, 160))
        return None, value

    encoding = (file_node.attrib.get("encoding") or "").lower()
    name_node = file_node.find("name")
    content_node = file_node.find("content")
    extracted_name = name_node.text.strip() if name_node is not None and name_node.text else None
    extracted_content = content_node.text.strip() if content_node is not None and content_node.text else ""

    if encoding and encoding != "base64":
        logger.warning("K2 file XML uses unexpected encoding=%r filename=%r", encoding, extracted_name)

    logger.info(
        "K2 file XML extracted filename=%r encoding=%r base64_chars=%s base64_preview=%r",
        extracted_name,
        encoding,
        len(extracted_content),
        _preview_text(extracted_content, 40),
    )
    return extracted_name, extracted_content


def _normalize_k2_document_payload(payload: DocumentBase64Request) -> DocumentBase64Request:
    extracted_name, extracted_content = _extract_k2_file_xml_content(payload.fileContentBase64)
    if not extracted_content or extracted_content == payload.fileContentBase64:
        return payload

    file_name = extracted_name or payload.fileName
    logger.info(
        "K2 payload normalized original_fileName=%r normalized_fileName=%r",
        payload.fileName,
        file_name,
    )
    return DocumentBase64Request(fileName=file_name, fileContentBase64=extracted_content)


async def _read_document_request(
    request: Request,
    file: Optional[UploadFile],
) -> tuple[bytes, str, Optional[str]]:
    content_type = request.headers.get("content-type", "").lower()
    logger.info(
        "reading document request path=%s content_type=%r content_length=%r",
        request.url.path,
        request.headers.get("content-type", ""),
        request.headers.get("content-length", ""),
    )

    if content_type.startswith("application/json"):
        if file is not None:
            raise HTTPException(
                status_code=400,
                detail="Provide either a multipart file upload or a JSON base64 payload, not both.",
            )
        try:
            raw_payload = await request.json()
            logger.info(
                "json document payload keys=%s fileName=%r base64_chars=%s base64_preview=%r",
                sorted(raw_payload.keys()) if isinstance(raw_payload, dict) else type(raw_payload).__name__,
                raw_payload.get("fileName") if isinstance(raw_payload, dict) else None,
                len(raw_payload.get("fileContentBase64", "")) if isinstance(raw_payload, dict) else None,
                _preview_text(raw_payload.get("fileContentBase64", ""), 40) if isinstance(raw_payload, dict) else "",
            )
            payload = _normalize_k2_document_payload(DocumentBase64Request.model_validate(raw_payload))
        except (ValueError, ValidationError) as exc:
            logger.warning("json document payload validation failed path=%s error=%s", request.url.path, exc)
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
    logger.info(
        "multipart document upload received field=file filename=%r content_type=%r",
        upload_file_name,
        upload_content_type,
    )
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

    logger.info(
        "multipart document upload read filename=%r bytes=%s bytes_preview=%r",
        upload_file_name,
        len(file_bytes),
        _preview_bytes(file_bytes),
    )
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
        logger.warning(
            "pdf signature validation failed filename=%r bytes=%s bytes_preview=%r",
            file_name,
            len(file_bytes),
            _preview_bytes(file_bytes, 120),
        )
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
    logger.info(
        "conversion start filename=%r incoming_content_type=%r bytes=%s use_ocr=%s",
        file_name,
        content_type,
        len(file_bytes),
        use_ocr,
    )
    _validate_document_bytes(file_bytes, "Document file content")
    extension = _ensure_supported_extension(file_name, content_type)
    _validate_pdf_signature(file_bytes, file_name, extension)
    content_type = _get_content_type(file_name, extension)
    logger.info(
        "conversion normalized filename=%r extension=%r content_type=%r",
        file_name,
        extension,
        content_type,
    )
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
        logger.warning("conversion unsupported format filename=%r error=%s", file_name, exc)
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except MissingDependencyException as exc:
        logger.warning("conversion missing dependency filename=%r error=%s", file_name, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileConversionException as exc:
        logger.warning("conversion failed filename=%r error=%s", file_name, exc)
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
        "markdownText": markdown,
        "textLength": len(markdown),
        "success": True,
    }

    if use_ocr:
        response["ocrApplied"] = ocr_applied

    logger.info(
        "conversion success filename=%r markdown_chars=%s content_type=%r use_ocr=%s",
        file_name,
        len(markdown),
        content_type,
        use_ocr,
    )
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
            "k2_markdown": "/K2-Markdown",
            "k2_markdown_xml": "/K2-Markdown-Xml",
            "k2_markdown_xml_json": "/K2-Markdown-Xml-Json",
            "k2_markdown_xml_json_text": "/K2-Markdown-Xml-Json-Text",
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
    logger.info("endpoint /documents/markdown invoked")
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
)
def document_to_markdown_k2(payload: DocumentBase64Request):
    payload = _normalize_k2_document_payload(payload)
    logger.info(
        "endpoint /documents/markdown/k2 invoked fileName=%r base64_chars=%s base64_preview=%r",
        payload.fileName,
        len(payload.fileContentBase64 or ""),
        _preview_text(payload.fileContentBase64 or "", 40),
    )
    return _convert_document_to_markdown(
        file_bytes=_decode_base64_file_content(payload.fileContentBase64),
        file_name=payload.fileName,
        content_type=None,
    )


@app.post(
    "/K2-Markdown",
    tags=["K2 Conversion"],
    summary="K2 PDF-to-Markdown",
    description=(
        "Top-level K2 REST Broker operation accepting a multipart PDF file upload."
    ),
    response_model=DocumentMarkdownResponse,
)
async def k2_document_to_markdown(
    request: Request,
    file: UploadFile = File(...),
):
    logger.info("endpoint /K2-Markdown invoked")
    file_bytes, file_name, content_type = await _read_document_request(request, file)
    return _convert_document_to_markdown(
        file_bytes=file_bytes,
        file_name=file_name,
        content_type=content_type,
    )


@app.post(
    "/K2-Markdown-Xml",
    tags=["K2 Conversion"],
    summary="K2 XML File-to-Markdown",
    description=(
        "K2-friendly endpoint accepting the raw XML value produced by K2 file controls, "
        "for example <file><name>report.pdf</name><content>JVBERi0...</content></file>."
    ),
    response_model=DocumentMarkdownResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "text/plain": {"schema": {"type": "string", "title": "K2FileXml"}},
                "application/xml": {"schema": {"type": "string", "title": "K2FileXml"}},
                "application/json": {"schema": K2FileXmlRequest.model_json_schema()},
            },
        }
    },
)
async def k2_file_xml_to_markdown(request: Request):
    request_content_type = (request.headers.get("content-type") or "").lower()
    if request_content_type.startswith("application/json"):
        raw_payload = await request.json()
        if isinstance(raw_payload, dict):
            file_xml = raw_payload.get("fileXml") or raw_payload.get("file_xml") or ""
        elif isinstance(raw_payload, str):
            file_xml = raw_payload
        else:
            file_xml = ""
        logger.info(
            "endpoint /K2-Markdown-Xml received JSON payload type=%s keys=%s",
            type(raw_payload).__name__,
            sorted(raw_payload.keys()) if isinstance(raw_payload, dict) else None,
        )
    else:
        file_xml = (await request.body()).decode("utf-8", errors="replace")

    logger.info(
        "endpoint /K2-Markdown-Xml invoked xml_chars=%s xml_preview=%r",
        len(file_xml or ""),
        _preview_text(file_xml or "", 160),
    )
    file_name, file_content_base64 = _extract_k2_file_xml_content(file_xml)
    if not file_content_base64:
        raise HTTPException(
            status_code=400,
            detail="K2 file XML must contain a <content> value with Base64 PDF content.",
        )

    return _convert_document_to_markdown(
        file_bytes=_decode_base64_file_content(file_content_base64),
        file_name=file_name or "k2-upload.pdf",
        content_type=None,
    )


@app.post(
    "/K2-Markdown-Xml-Json",
    tags=["K2 Conversion"],
    summary="K2 XML JSON-to-Markdown",
    description=(
        "K2 REST Broker-friendly JSON endpoint accepting the raw K2 file XML in a `fileXml` property."
    ),
    response_model=DocumentMarkdownResponse,
)
def k2_file_xml_json_to_markdown(payload: K2FileXmlRequest):
    logger.info(
        "endpoint /K2-Markdown-Xml-Json invoked xml_chars=%s xml_preview=%r",
        len(payload.fileXml or ""),
        _preview_text(payload.fileXml or "", 160),
    )
    file_name, file_content_base64 = _extract_k2_file_xml_content(payload.fileXml)
    if not file_content_base64:
        raise HTTPException(
            status_code=400,
            detail="K2 file XML must contain a <content> value with Base64 PDF content.",
        )

    return _convert_document_to_markdown(
        file_bytes=_decode_base64_file_content(file_content_base64),
        file_name=file_name or "k2-upload.pdf",
        content_type=None,
    )


@app.post(
    "/K2-Markdown-Xml-Json-Text",
    tags=["K2 Conversion"],
    summary="K2 XML JSON-to-Markdown Text",
    description=(
        "K2 REST Broker-friendly JSON endpoint accepting the raw K2 file XML in a `fileXml` property "
        "and returning only Markdown text as text/markdown. Map K2 HttpResponseContent for the Markdown output."
    ),
    response_class=PlainTextResponse,
    responses={
        200: {
            "description": "Markdown text only.",
            "content": {
                "text/markdown": {"schema": {"type": "string", "title": "MarkdownText"}},
                "text/plain": {"schema": {"type": "string", "title": "MarkdownText"}},
            },
        }
    },
)
def k2_file_xml_json_to_markdown_text(payload: K2FileXmlRequest):
    logger.info(
        "endpoint /K2-Markdown-Xml-Json-Text invoked xml_chars=%s xml_preview=%r",
        len(payload.fileXml or ""),
        _preview_text(payload.fileXml or "", 160),
    )
    file_name, file_content_base64 = _extract_k2_file_xml_content(payload.fileXml)
    if not file_content_base64:
        raise HTTPException(
            status_code=400,
            detail="K2 file XML must contain a <content> value with Base64 PDF content.",
        )

    result = _convert_document_to_markdown(
        file_bytes=_decode_base64_file_content(file_content_base64),
        file_name=file_name or "k2-upload.pdf",
        content_type=None,
    )
    return PlainTextResponse(result["markdown"], media_type="text/markdown; charset=utf-8")


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
    logger.info("endpoint /documents/markdown/ocr invoked")
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


_default_openapi = app.openapi


def _k2_compatible_openapi():
    schema = _default_openapi()
    response_schema = schema.get("components", {}).get("schemas", {}).get("DocumentMarkdownResponse")
    k2_paths = ["/documents/markdown/k2", "/K2-Markdown", "/K2-Markdown-Xml", "/K2-Markdown-Xml-Json"]

    if response_schema:
        for path in k2_paths:
            operation = schema.get("paths", {}).get(path, {}).get("post")
            if operation:
                operation["responses"]["200"]["content"]["application/json"]["schema"] = response_schema

    return schema


app.openapi = _k2_compatible_openapi
