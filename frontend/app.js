const form = document.getElementById("uploadForm");
const apiBaseUrl = document.getElementById("apiBaseUrl");
const pdfFile = document.getElementById("pdfFile");
const dropZone = document.getElementById("dropZone");
const fileDetail = document.getElementById("fileDetail");
const message = document.getElementById("message");
const statusPill = document.getElementById("statusPill");
const markdownOutput = document.getElementById("markdownOutput");
const submitButton = document.getElementById("submitButton");
const copyButton = document.getElementById("copyButton");
const downloadButton = document.getElementById("downloadButton");
const clearButton = document.getElementById("clearButton");

let selectedFile = null;
let lastMarkdown = "";

if (window.location.pathname.startsWith("/test-api/")) {
  apiBaseUrl.value = window.location.origin;
}

function setStatus(text, type = "") {
  statusPill.textContent = text;
  message.textContent = type === "error" ? text : "";
  message.className = `message ${type}`.trim();
}

function setMessage(text, type = "") {
  message.textContent = text;
  message.className = `message ${type}`.trim();
}

function setBusy(isBusy) {
  submitButton.disabled = isBusy;
  submitButton.textContent = isBusy ? "Converting..." : "Convert to Markdown";
}

function updateOutput(markdown) {
  lastMarkdown = markdown || "";
  markdownOutput.value = lastMarkdown;
  copyButton.disabled = !lastMarkdown;
  downloadButton.disabled = !lastMarkdown;
}

function isPdf(file) {
  return file && (file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf"));
}

function selectFile(file) {
  if (!file) {
    selectedFile = null;
    fileDetail.textContent = "No file selected";
    return;
  }

  if (!isPdf(file)) {
    selectedFile = null;
    pdfFile.value = "";
    fileDetail.textContent = "No file selected";
    setStatus("Select a PDF file", "error");
    return;
  }

  if (file.size === 0) {
    selectedFile = null;
    pdfFile.value = "";
    fileDetail.textContent = "No file selected";
    setStatus("The selected PDF is empty", "error");
    return;
  }

  selectedFile = file;
  fileDetail.textContent = `${file.name} (${formatBytes(file.size)})`;
  setStatus("Ready");
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  return `${(kb / 1024).toFixed(1)} MB`;
}

function endpointUrl() {
  const base = apiBaseUrl.value.trim().replace(/\/+$/, "");
  return `${base}/documents/markdown`;
}

async function readError(response) {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    const body = await response.json();
    if (typeof body.detail === "string") return body.detail;
    return JSON.stringify(body.detail || body);
  }
  return response.text();
}

function extractMarkdown(body) {
  if (typeof body === "string") return body;
  if (body && typeof body.markdown === "string") return body.markdown;
  return JSON.stringify(body, null, 2);
}

pdfFile.addEventListener("change", () => {
  selectFile(pdfFile.files[0]);
});

dropZone.addEventListener("dragover", (event) => {
  event.preventDefault();
  dropZone.classList.add("is-dragging");
});

dropZone.addEventListener("dragleave", () => {
  dropZone.classList.remove("is-dragging");
});

dropZone.addEventListener("drop", (event) => {
  event.preventDefault();
  dropZone.classList.remove("is-dragging");
  const file = event.dataTransfer.files[0];
  selectFile(file);
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  updateOutput("");

  if (!selectedFile) {
    setStatus("Choose a PDF report first", "error");
    return;
  }

  if (!apiBaseUrl.value.trim()) {
    setStatus("Enter the API base URL", "error");
    return;
  }

  const formData = new FormData();
  formData.append("file", selectedFile, selectedFile.name);

  setBusy(true);
  setStatus("Converting");
  setMessage("Uploading PDF and waiting for Markdown...");

  try {
    const response = await fetch(endpointUrl(), {
      method: "POST",
      body: formData,
    });

    if (!response.ok) {
      throw new Error(await readError(response));
    }

    const contentType = response.headers.get("content-type") || "";
    const body = contentType.includes("application/json")
      ? await response.json()
      : await response.text();
    const markdown = extractMarkdown(body);

    updateOutput(markdown);
    setStatus("Success");
    setMessage(`Converted ${selectedFile.name} to Markdown.`, "success");
  } catch (error) {
    setStatus("Conversion failed", "error");
    setMessage(error.message || "The API request failed.", "error");
  } finally {
    setBusy(false);
  }
});

copyButton.addEventListener("click", async () => {
  if (!lastMarkdown) return;

  try {
    await navigator.clipboard.writeText(lastMarkdown);
    setMessage("Markdown copied to clipboard.", "success");
  } catch {
    markdownOutput.focus();
    markdownOutput.select();
    setMessage("Select the Markdown text and copy it manually.", "error");
  }
});

downloadButton.addEventListener("click", () => {
  if (!lastMarkdown) return;

  const baseName = selectedFile ? selectedFile.name.replace(/\.pdf$/i, "") : "converted-document";
  const blob = new Blob([lastMarkdown], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${baseName}.md`;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
});

clearButton.addEventListener("click", () => {
  selectedFile = null;
  pdfFile.value = "";
  fileDetail.textContent = "No file selected";
  updateOutput("");
  setStatus("Ready");
  setMessage("");
});
