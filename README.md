# QIESi API Toolkit

QIESi API Toolkit is a lightweight FastAPI-based service for workflow and automation scenarios. It provides simple file conversions for Nintex, AI, RAG, search indexing, compliance ingestion, and document-processing workflows.

## What it does

- Converts JSON into Excel (`.xlsx`)
- Converts plain text into CSV
- Converts uploaded or base64-supplied documents into Markdown/text using Microsoft MarkItDown
- Optionally supports OCR-style markdown conversion when Azure Document Intelligence is configured

## Installation

### Python version

- Python 3.10+ is recommended

### Setup

1. Create a virtual environment:

```bash
python -m venv .venv
```

2. Activate the virtual environment:

- Windows:

```powershell
.\.venv\Scripts\Activate.ps1
```

- macOS / Linux:

```bash
source .venv/bin/activate
```

3. Install requirements:

```bash
pip install -r requirements.txt
```

The requirements include MarkItDown's PDF extra, which is required by `/documents/markdown` for PDF reports.

### Optional OCR support

To enable Azure Document Intelligence OCR support for `/documents/markdown/ocr`, install MarkItDown with the optional Azure extension:

```bash
pip install markitdown[az-doc-intel]
```

Then configure the environment variable:

```bash
export AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT="https://<your-endpoint>"
export AZURE_API_KEY="<your-key>"
```

On Windows PowerShell:

```powershell
$env:AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT = "https://<your-endpoint>"
$env:AZURE_API_KEY = "<your-key>"
```

## Run locally

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Then open `http://127.0.0.1:8000/docs` for the interactive API docs.

The bundled Test API front end is also served by the backend:

```text
http://127.0.0.1:8000/test-api/
```

### Windows PowerShell helper scripts

From the repository root:

```powershell
.\dev-scripts\install-backend.ps1
.\dev-scripts\run-backend.ps1
.\dev-scripts\run-tests.ps1
```

For an interactive K2/Nintex POC launcher that prompts for a port and prints every local endpoint URL:

```powershell
.\dev-scripts\start-local-poc.ps1
```

Press Enter to use port `8000`, or enter another available port. The script waits for the health endpoint before printing the Test UI, Swagger, OpenAPI, conversion, and system URLs. Press `Ctrl+C` to stop the backend.

For repeatable launches without the prompt, pass the port directly:

```powershell
.\dev-scripts\start-local-poc.ps1 -Port 8123
```

To run the test front end as a separate static site:

```powershell
.\dev-scripts\run-test-frontend.ps1
```

Then open `http://127.0.0.1:5173`. The front end defaults to API base URL `http://127.0.0.1:8000`.

### Local configuration

Copy `.env.example` to `.env` if you want to configure local settings outside your shell. The API loads `.env` at startup and reads these environment variables:

- `QIESI_CORS_ORIGINS`: comma-separated local origins allowed to call the API. Defaults to `http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:8000,http://localhost:8000`.
- `QIESI_MAX_UPLOAD_BYTES`: maximum document upload size in bytes. Defaults to `26214400` (25 MB).
- `QIESI_PUBLIC_BASE_URL`: absolute base URL advertised in OpenAPI metadata for REST clients such as K2. The interactive launcher sets this from the selected port.
- `AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT` and `AZURE_API_KEY`: optional OCR support for `/documents/markdown/ocr`.

## Endpoints

### GET /health

Purpose: Verify the API is running.

Use cases: health checks from monitoring, load balancers, and workflow sensors.

Sample response:

```json
{
  "status": "ok"
}
```

---

### GET /info

Purpose: Return basic service metadata and endpoint references.

Use cases: discovery, integration validation, lightweight service info.

Sample response:

```json
{
  "name": "Qiesi API Toolkit",
  "version": "1.1.2",
  "author": "Paul Keys",
  "description": "API toolkit for Nintex workflow integrations",
  "endpoints": {
    "health": "/health",
    "ping": "/ping",
    "json_to_xlsx": "/JSON-to-XLSX",
    "text_to_csv": "/TXT-to-CSV",
    "document_to_markdown": "/documents/markdown",
    "document_to_markdown_ocr": "/documents/markdown/ocr",
    "docs": "/docs",
    "openapi": "/openapi.json"
  }
}
```

---

### GET /ping

Purpose: Simple connectivity check.

Use cases: lightweight ping/pong validation for workflow services and tooling.

Sample response:

```json
{
  "message": "pong",
  "api": "Qiesi Toolkit API"
}
```

---

### POST /JSON-to-XLSX

Purpose: Convert a JSON payload into a Base64 encoded Excel `.xlsx` file.

Use cases: export workflow JSON into spreadsheets, integrate Nintex JSON outputs into Excel, archive structured data.

Request format:

```json
{
  "jsonInput": "[{\"name\": \"Alice\", \"email\": \"alice@example.com\"}]"
}
```

Sample curl:

```bash
curl -X POST http://127.0.0.1:8000/JSON-to-XLSX \
  -H "Content-Type: application/json" \
  -d '{"jsonInput":"[{\"name\":\"Alice\",\"email\":\"alice@example.com\"}]"}'
```

Sample response:

```json
{
  "fileName": "QiesiAPI-JSONtoXLSX-20240101120000.xlsx",
  "excelFile": "<base64-encoded-xlsx>"
}
```

Error notes:

- Returns `400` when JSON is invalid or conversion fails.

---

### POST /TXT-to-CSV

Purpose: Convert plain text into a Base64 encoded CSV file.

Use cases: normalize multiline text into CSV rows, process notes or lists for workflow ingestion.

Request format:

```json
{
  "message": "line1\nline2\nline3"
}
```

Sample curl:

```bash
curl -X POST http://127.0.0.1:8000/TXT-to-CSV \
  -H "Content-Type: application/json" \
  -d '{"message":"line1\nline2\nline3"}'
```

Sample response:

```json
{
  "fileName": "QiesiAPI-TextToCSV-20240101120000.csv",
  "csvFile": "<base64-encoded-csv>"
}
```

Error notes:

- Returns `400` when conversion fails.

---

### POST /documents/markdown

Purpose: Convert uploaded or base64-supplied documents into Markdown/text using Microsoft MarkItDown.

Supported file types:

- PDF
- DOCX
- XLSX
- PPTX
- TXT
- CSV
- HTML
- JSON
- XML
- MD
- JPEG/PNG/BMP/TIFF

Use cases:

- Normalize supplier invoices for AI extraction
- Convert contracts to machine-readable text
- Ingest policies and procedures into search or knowledge bases
- Prepare reports, manuals, and meeting packs for summaries
- Convert operational reports for RAG and workflow automation

Request support:

Option 1 — multipart file upload:

```bash
curl -X POST http://127.0.0.1:8000/documents/markdown \
  -F "file=@invoice.pdf"
```

Option 2 — JSON body with base64 payload:

```bash
curl -X POST http://127.0.0.1:8000/documents/markdown \
  -H "Content-Type: application/json" \
  -d '{
    "fileName": "invoice.pdf",
    "fileContentBase64": "<base64-file-content>"
  }'
```

Sample response:

```json
{
  "fileName": "invoice.pdf",
  "contentType": "application/pdf",
  "markdown": "# Converted content...",
  "textLength": 12345,
  "success": true
}
```

Error notes:

- Returns `400` for invalid or empty uploads.
- Returns `400` when a `.pdf` upload does not contain a PDF file signature.
- Returns `400` for missing JSON fields or invalid base64.
- Returns `413` when the upload exceeds `QIESI_MAX_UPLOAD_BYTES`.
- Returns `415` for unsupported file types.

The normal response is JSON with Markdown in the `markdown` field:

```json
{
  "fileName": "incident-report.pdf",
  "contentType": "application/pdf",
  "markdown": "# Converted content...",
  "textLength": 12345,
  "success": true
}
```

### POST /documents/markdown/k2

Purpose: Provide a K2 REST Broker-friendly JSON contract without multipart file upload modeling.

Request:

```json
{
  "fileName": "safalo-incident-report.pdf",
  "fileContentBase64": "JVBERi0xLjQ..."
}
```

Response:

```json
{
  "fileName": "safalo-incident-report.pdf",
  "contentType": "application/pdf",
  "markdown": "# Incident Report...",
  "textLength": 12345,
  "success": true
}
```

In K2, generate the Object Type for `DocumentBase64Request` and the Service Operation named `document_to_markdown_k2_documents_markdown_k2_post`. Use the Service Operation SmartObject to execute the API call.

For K2 REST Broker versions that ignore nested paths sharing the `/documents/markdown` prefix, use the equivalent top-level operation:

```text
POST /K2-Markdown
```

Its generated operation ID is `k2_document_to_markdown_K2_Markdown_post`. This is the preferred operation for K2 SmartObject generation.

---

## Test API front end

The repository includes a lightweight browser test harness in `frontend/`. It does not perform PDF-to-Markdown conversion in the browser. It sends a multipart `FormData` request directly to the backend `/documents/markdown` endpoint and displays the API response.

Features:

- PDF picker with drag-and-drop support
- Configurable API base URL, defaulting to `http://127.0.0.1:8000`
- Client-side PDF and empty-file validation
- Loading, success, and human-readable error states
- Markdown response viewer
- Copy Markdown, download `.md`, and clear actions

Recommended local flow:

1. Start the backend:

```powershell
.\dev-scripts\run-backend.ps1
```

2. Open `http://127.0.0.1:8000/test-api/`.
3. Select or drag in a fake Safalo Mining & Construction SHERQ incident report PDF.
4. Click `Convert to Markdown`.
5. Review the Markdown response, then use `Copy` or `Download .md`.

To serve the static front end separately, run `./dev-scripts/run-test-frontend.ps1` and open `http://127.0.0.1:5173`. The backend CORS defaults allow this local origin.

## Safalo SHERQ PDF testing

The API expects real PDF bytes, rejects empty files, and rejects files with a `.pdf` extension that do not contain a PDF signature near the start of the file.

Example manual API test:

```powershell
curl.exe -X POST http://127.0.0.1:8000/documents/markdown `
  -F "file=@C:\path\to\safalo-incident-report.pdf"
```

Scanned PDFs or image-only medical reports may return little or no text through the standard endpoint. Use `/documents/markdown/ocr` with Azure Document Intelligence configured when OCR is required.

---

### POST /documents/markdown/ocr

Purpose: Convert scanned or image-heavy documents into Markdown using optional Azure Document Intelligence support.

Use cases:

- Scanned invoices and receipts
- Photo-based reports
- PDFs with screenshots or scanned pages
- Low-quality scanned forms and bank reports

Request support:

Option 1 — multipart file upload:

```bash
curl -X POST http://127.0.0.1:8000/documents/markdown/ocr \
  -F "file=@scanned-document.pdf"
```

Option 2 — JSON body with base64 payload:

```bash
curl -X POST http://127.0.0.1:8000/documents/markdown/ocr \
  -H "Content-Type: application/json" \
  -d '{
    "fileName": "scanned-document.pdf",
    "fileContentBase64": "<base64-file-content>"
  }'
```

Sample response:

```json
{
  "fileName": "scanned-invoice.pdf",
  "contentType": "application/pdf",
  "markdown": "# OCR converted content...",
  "textLength": 9876,
  "ocrApplied": true,
  "success": true
}
```

Error notes:

- Returns `400` when OCR dependencies or Azure configuration are missing.
- Returns `415` for unsupported file types.
- If optional OCR support is not installed, the endpoint returns a clear message explaining `markitdown[az-doc-intel]` and environment variables.

## Real business use cases

- Accounts payable invoice normalization for extraction engines
- Contract review preparation for clause analysis and summarization
- Knowledge base ingestion for policy, procedure, and training content
- Compliance and risk document ingestion for searchable archives
- Bank statement, report, and operational data pre-processing
- Nintex / workflow automation file ingestion pipelines
- AI, RAG, and search indexing of business documents

## Testing examples

### Automated tests

```powershell
.\dev-scripts\run-tests.ps1
```

The `/documents/markdown` tests cover missing uploads, unsupported types, empty and oversized uploads, invalid PDF signatures, base64 JSON requests, and the successful multipart response contract.

### JSON → XLSX

```bash
curl -X POST http://127.0.0.1:8000/JSON-to-XLSX \
  -H "Content-Type: application/json" \
  -d '{"jsonInput":"[{\"id\":1,\"name\":\"Widget\"}]"}'
```

### TXT → CSV

```bash
curl -X POST http://127.0.0.1:8000/TXT-to-CSV \
  -H "Content-Type: application/json" \
  -d '{"message":"line1\nline2\nline3"}'
```

### Documents → Markdown

```bash
curl -X POST http://127.0.0.1:8000/documents/markdown \
  -F "file=@example.docx"
```

### Documents → Markdown (base64)

```bash
curl -X POST http://127.0.0.1:8000/documents/markdown \
  -H "Content-Type: application/json" \
  -d '{"fileName":"example.pdf","fileContentBase64":"<base64>"}'
```

### Documents → Markdown OCR

```bash
curl -X POST http://127.0.0.1:8000/documents/markdown/ocr \
  -F "file=@scanned.pdf"
```

## Backward compatibility

All original routes remain supported and unchanged:

- `GET /health`
- `GET /info`
- `GET /ping`
- `POST /JSON-to-XLSX`
- `POST /TXT-to-CSV`

New endpoints were added as extensions only, preserving existing API behavior.
