$ErrorActionPreference = "Stop"

$env:QIESI_CORS_ORIGINS = if ($env:QIESI_CORS_ORIGINS) {
    $env:QIESI_CORS_ORIGINS
} else {
    "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:8000,http://localhost:8000"
}

python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
