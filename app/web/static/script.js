/* script.js — fetch, render, copy logic */
"use strict";

// ── State ──────────────────────────────────────────────────────────────────
let _processing = false;
let _progressTimer = null;
let _stepTimer = null;
let _startedAt = 0;
let _currentVideoId = "";
let _actionInFlight = false;
let _inputMode = "url";
let _selectedUploadFile = null;

// ── Submit ─────────────────────────────────────────────────────────────────
async function submitUrl() {
  const input = document.getElementById("url-input");
  const url = input.value.trim();
  const provider = getSelectedProvider();
  const transcription_mode = getSelectedTranscriptionMode();

  if (!url) {
    showError("الرجاء إدخال رابط يوتيوب.");
    return;
  }
  if (!isYouTubeUrl(url)) {
    showError("الرابط غير صالح. أدخل رابط يوتيوب صحيح.");
    return;
  }
  if (_processing) return;

  clearError();
  clearResults();
  setLoading(true, "url");

  try {
    const res = await fetch("/process", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ youtube_url: url, provider, transcription_mode }),
    });

    let json = {};
    try {
      json = await res.json();
    } catch {
      json = {};
    }

    if (!res.ok) {
      showError(humanizeError(res.status, json.detail));
      return;
    }

    const rendered = renderResults(json);
    if (!rendered) {
      showError(
        "تمت المعالجة لكن المحتوى الناتج فارغ. جرّب فيديو آخر أو أعد المحاولة.",
      );
      return;
    }
  } catch (err) {
    showError("تعذّر الاتصال بالخادم. تحقق من أن الخدمة تعمل ثم أعد المحاولة.");
    console.error(err);
  } finally {
    setLoading(false, "url");
  }
}

async function submitUpload() {
  const provider = getSelectedProvider();
  const transcription_mode = getSelectedTranscriptionMode();

  if (_processing) return;
  if (!_selectedUploadFile) {
    showError("اختر ملفاً أولاً قبل الرفع.");
    return;
  }

  clearError();
  clearResults();
  setLoading(true, "upload");
  updateLoadingStep("Uploading file...");

  try {
    const form = new FormData();
    form.append("media_file", _selectedUploadFile);

    const params = new URLSearchParams({ provider, transcription_mode });
    const res = await fetch(`/upload?${params.toString()}`, {
      method: "POST",
      body: form,
    });

    let json = {};
    try {
      json = await res.json();
    } catch {
      json = {};
    }

    if (!res.ok) {
      showError(humanizeError(res.status, json.detail));
      return;
    }

    const rendered = renderResults(json);
    if (!rendered) {
      showError(
        "تمت المعالجة لكن المحتوى الناتج فارغ. جرّب ملفاً آخر أو أعد المحاولة.",
      );
      return;
    }
  } catch (err) {
    showError("تعذّر رفع الملف أو الاتصال بالخادم. حاول مرة أخرى.");
    console.error(err);
  } finally {
    setLoading(false, "upload");
  }
}

// Allow pressing Enter to submit
document.addEventListener("DOMContentLoaded", () => {
  const input = document.getElementById("url-input");
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") submitUrl();
  });

  const fileInput = document.getElementById("media-file-input");
  const dropzone = document.getElementById("upload-dropzone");
  if (fileInput) {
    fileInput.addEventListener("change", () => {
      const file =
        fileInput.files && fileInput.files[0] ? fileInput.files[0] : null;
      setSelectedUploadFile(file);
    });
  }

  if (dropzone) {
    ["dragenter", "dragover"].forEach((evt) => {
      dropzone.addEventListener(evt, (e) => {
        e.preventDefault();
        dropzone.classList.add("dragover");
      });
    });
    ["dragleave", "drop"].forEach((evt) => {
      dropzone.addEventListener(evt, (e) => {
        e.preventDefault();
        dropzone.classList.remove("dragover");
      });
    });
    dropzone.addEventListener("drop", (e) => {
      const files = e.dataTransfer?.files;
      const file = files && files.length ? files[0] : null;
      setSelectedUploadFile(file);
    });
  }
});

// ── Validation ─────────────────────────────────────────────────────────────
function getYouTubeVideoId(url) {
  const raw = String(url || "").trim();
  if (!raw) return null;

  let parsed;
  try {
    parsed = new URL(raw);
  } catch {
    try {
      parsed = new URL(`https://${raw}`);
    } catch {
      return null;
    }
  }

  let host = (parsed.hostname || "").toLowerCase();
  if (host.startsWith("www.")) host = host.slice(4);

  const pathname = parsed.pathname || "";
  const searchParams = parsed.searchParams;
  let videoId = null;

  if (host === "youtu.be") {
    videoId = pathname.slice(1).split("/")[0] || null;
  } else if (host === "youtube.com" || host === "m.youtube.com") {
    if (pathname === "/watch") {
      videoId = searchParams.get("v");
    } else if (pathname.startsWith("/shorts/")) {
      videoId = pathname.slice("/shorts/".length).split("/")[0] || null;
    }
  }

  const normalized = String(videoId || "").trim();
  return normalized || null;
}

function isYouTubeUrl(url) {
  return getYouTubeVideoId(url) !== null;
}

function setInputMode(mode) {
  _inputMode = mode === "upload" ? "upload" : "url";
  const urlPanel = document.getElementById("url-panel");
  const uploadPanel = document.getElementById("upload-panel");
  const urlBtn = document.getElementById("mode-url-btn");
  const uploadBtn = document.getElementById("mode-upload-btn");

  urlPanel.classList.toggle("hidden", _inputMode !== "url");
  uploadPanel.classList.toggle("hidden", _inputMode !== "upload");
  urlBtn.classList.toggle("active", _inputMode === "url");
  uploadBtn.classList.toggle("active", _inputMode === "upload");
  clearError();
}

function browseUploadFile() {
  const fileInput = document.getElementById("media-file-input");
  if (fileInput) fileInput.click();
}

function setSelectedUploadFile(file) {
  const label = document.getElementById("upload-file-name");
  _selectedUploadFile = null;

  if (!file) {
    label.textContent = "";
    return;
  }

  const ext = getFileExtension(file.name);
  if (!isAllowedUploadExt(ext)) {
    showError("نوع الملف غير مدعوم.");
    label.textContent = "";
    return;
  }

  _selectedUploadFile = file;
  clearError();
  label.textContent = `Selected: ${file.name}`;
}

function getFileExtension(name) {
  const n = String(name || "");
  const idx = n.lastIndexOf(".");
  return idx >= 0 ? n.slice(idx).toLowerCase() : "";
}

function isAllowedUploadExt(ext) {
  return [
    ".mp4",
    ".mov",
    ".mkv",
    ".webm",
    ".mp3",
    ".wav",
    ".m4a",
    ".aac",
    ".ogg",
  ].includes(ext);
}

// ── Render ─────────────────────────────────────────────────────────────────
function renderResults(json) {
  const { data, cached } = json;
  if (!data) return false;

  const hasTranscript =
    typeof data.transcript === "string" && data.transcript.trim().length > 0;
  if (!hasTranscript) return false;

  // Badge
  const badge = document.getElementById("cache-badge");
  if (cached) {
    badge.classList.remove("hidden");
  } else {
    badge.classList.add("hidden");
  }

  // Metadata
  const meta = data.metadata || {};
  const metaEl = document.getElementById("meta-content");
  metaEl.innerHTML = "";
  const metaFields = [
    ["العنوان", meta.title],
    ["الرابط", meta.url],
    ["المدة", meta.duration ? formatDuration(meta.duration) : null],
    ["القناة", meta.uploader],
    ["تاريخ النشر", meta.upload_date ? formatDate(meta.upload_date) : null],
    ["وضع Whisper", meta.transcription_mode],
    ["نموذج Whisper", meta.whisper_model],
    ["اللغة المكتشفة", meta.language_detected],
  ];
  metaFields.forEach(([key, val]) => {
    if (!val) return;
    metaEl.innerHTML += `<span class="key">${key}</span><span class="val">${escHtml(String(val))}</span>`;
  });

  _currentVideoId = extractVideoId(meta.output_dir || "");

  // Transcript-first UX: hide previously cached optional outputs until user regenerates.
  setText("tldr-content", "");
  setText("thread-content", "");
  setText("faq-content", "");
  setText("transcript-content", data.transcript);

  toggleResultCard("tldr-card", false);
  toggleResultCard("thread-card", false);
  toggleResultCard("faq-card", false);

  setSectionOpen("transcript-body", true);
  setSectionOpen("tldr-body", true);
  setSectionOpen("thread-body", true);
  setSectionOpen("faq-body", true);

  document.getElementById("results").classList.remove("hidden");
  document.getElementById("results").scrollIntoView({ behavior: "smooth" });
  return true;
}

function toggleResultCard(cardId, show) {
  const el = document.getElementById(cardId);
  if (!el) return;
  el.classList.toggle("hidden", !show);
}

function extractVideoId(outputDir) {
  const raw = String(outputDir || "").replace(/\\/g, "/");
  const parts = raw.split("/").filter(Boolean);
  return parts.length ? parts[parts.length - 1] : "";
}

async function generateArtifact(target, btn) {
  clearActionError();
  clearActionStatus();

  if (_actionInFlight) {
    showActionError("يوجد إجراء توليد قيد التنفيذ. انتظر حتى يكتمل.");
    return;
  }

  if (!_currentVideoId) {
    showActionError("لا يوجد فيديو نشط. نفّذ التفريغ أولاً.");
    return;
  }

  const provider = getSelectedProvider();
  const actionLabel = btn?.dataset?.label || "Generate";
  _actionInFlight = true;
  setActionButtonsDisabled(true, btn, "Processing...");
  showActionStatus(`${actionLabel}: Processing...`);

  try {
    const res = await fetch(`/generate/${target}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ video_id: _currentVideoId, provider }),
    });

    let json = {};
    try {
      json = await res.json();
    } catch {
      json = {};
    }

    if (!res.ok) {
      showActionError(humanizeError(res.status, json.detail));
      return;
    }

    const content = String(json.content || "");
    if (!content.trim()) {
      showActionError("تم التنفيذ لكن الناتج فارغ.");
      return;
    }

    if (target === "clean") {
      setText("transcript-content", content);
      setSectionOpen("transcript-body", true);
    }
    if (target === "tldr") {
      setText("tldr-content", content);
      toggleResultCard("tldr-card", true);
      setSectionOpen("tldr-body", true);
    }
    if (target === "thread") {
      setText("thread-content", content);
      toggleResultCard("thread-card", true);
      setSectionOpen("thread-body", true);
    }
    if (target === "faq") {
      setText("faq-content", content);
      toggleResultCard("faq-card", true);
      setSectionOpen("faq-body", true);
    }
    showActionStatus(`${actionLabel}: Completed`);
  } catch (err) {
    showActionError("تعذّر توليد هذا المخرج الآن. حاول مرة أخرى.");
    console.error(err);
  } finally {
    _actionInFlight = false;
    setActionButtonsDisabled(false);
  }
}

function setActionButtonsDisabled(
  disabled,
  activeBtn,
  processingText = "Processing...",
) {
  const buttons = document.querySelectorAll(".action-btn");
  buttons.forEach((b) => {
    const fallback = b.getAttribute("data-label") || b.textContent.trim();
    b.dataset.label = b.dataset.label || fallback;
    b.disabled = disabled;
    b.textContent =
      disabled && b === activeBtn ? processingText : b.dataset.label;
  });
}

function showActionError(msg) {
  const el = document.getElementById("action-error");
  if (!el) return;
  el.textContent = msg;
  el.classList.remove("hidden");
}

function clearActionError() {
  const el = document.getElementById("action-error");
  if (!el) return;
  el.textContent = "";
  el.classList.add("hidden");
}

function showActionStatus(msg) {
  const el = document.getElementById("action-status");
  if (!el) return;
  el.textContent = msg;
  el.classList.remove("hidden");
}

function clearActionStatus() {
  const el = document.getElementById("action-status");
  if (!el) return;
  el.textContent = "";
  el.classList.add("hidden");
}

function setText(id, text) {
  const el = document.getElementById(id);
  el.textContent = text || "";
}

// ── Copy ───────────────────────────────────────────────────────────────────
async function copySection(contentId, btnEl) {
  const el = document.getElementById(contentId);
  if (!el) return;
  const text = el.textContent;
  if (!text || !text.trim()) return;

  const btn = btnEl || el.closest(".result-card")?.querySelector(".copy-btn");
  try {
    await navigator.clipboard.writeText(text);
    if (btn) flashCopied(btn);
  } catch {
    // Fallback for older browsers
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    document.body.removeChild(ta);
  }
}

function flashCopied(btn) {
  const orig = btn.textContent;
  btn.textContent = "✓ تم النسخ";
  btn.classList.add("copied");
  setTimeout(() => {
    btn.textContent = orig;
    btn.classList.remove("copied");
  }, 1800);
}

// ── Collapsible ────────────────────────────────────────────────────────────
function toggleSection(bodyId) {
  const body = document.getElementById(bodyId);
  if (!body) return;
  const toggleId = bodyId.replace("-body", "-toggle");
  const icon = document.getElementById(toggleId);
  const isOpen = body.classList.contains("open");
  body.classList.toggle("open", !isOpen);
  if (icon) icon.style.transform = isOpen ? "" : "rotate(180deg)";
}

function setSectionOpen(bodyId, open) {
  const body = document.getElementById(bodyId);
  if (!body) return;
  const icon = document.getElementById(bodyId.replace("-body", "-toggle"));
  body.classList.toggle("open", open);
  if (icon) icon.style.transform = open ? "rotate(180deg)" : "";
}

// ── UI helpers ──────────────────────────────────────────────────────────────
function setLoading(on, mode = "url") {
  _processing = on;
  document.getElementById("loading").classList.toggle("hidden", !on);
  const btn = document.getElementById("submit-btn");
  const uploadBtn = document.getElementById("upload-btn");
  const browseBtn = document.getElementById("browse-btn");
  const modeBtns = document.querySelectorAll(".mode-btn");
  const input = document.getElementById("url-input");
  const uploadInput = document.getElementById("media-file-input");
  const radios = document.querySelectorAll("input[name='provider']");
  const modeSelect = document.getElementById("transcription-mode");
  btn.disabled = on;
  uploadBtn.disabled = on;
  browseBtn.disabled = on;
  input.disabled = on;
  if (uploadInput) uploadInput.disabled = on;
  modeBtns.forEach((b) => {
    b.disabled = on;
  });
  radios.forEach((r) => {
    r.disabled = on;
  });
  if (modeSelect) modeSelect.disabled = on;
  setActionButtonsDisabled(on);
  if (on) {
    _startedAt = Date.now();
    document.getElementById("loading-text").textContent =
      mode === "upload"
        ? "جاري رفع الملف ومعالجته..."
        : "جاري المعالجة… قد تستغرق العملية بضع دقائق للفيديوهات الطويلة";
    updateLoadingStep(
      mode === "upload" ? "Uploading file..." : "Downloading...",
    );
    updateProgress(
      4,
      mode === "upload" ? "Uploading file" : "Preparing request",
    );
    startStepAnimation();
    startProgressPolling();
  } else {
    stopStepAnimation();
    stopProgressPolling();
  }
}

function startStepAnimation() {
  stopStepAnimation();
  _stepTimer = setInterval(() => {
    if (!_processing) return;
    const elapsedSec = Math.floor((Date.now() - _startedAt) / 1000);

    if (_inputMode === "upload" && elapsedSec < 8) {
      updateLoadingStep("Uploading file...");
      if (currentProgress() < 18) updateProgress(18, "Uploading file");
      return;
    }

    if (elapsedSec < 8) {
      updateLoadingStep("Downloading...");
      if (currentProgress() < 22) updateProgress(22, "Downloading");
      return;
    }

    if (elapsedSec < 28) {
      updateLoadingStep("Transcribing...");
      if (currentProgress() < 65) updateProgress(65, "Transcribing");
      return;
    }

    updateLoadingStep("Saving transcript...");
    if (currentProgress() < 90) updateProgress(90, "Saving transcript");
  }, 1200);
}

function stopStepAnimation() {
  if (_stepTimer) {
    clearInterval(_stepTimer);
    _stepTimer = null;
  }
}

function startProgressPolling() {
  stopProgressPolling();
  fetchProgress();
  _progressTimer = setInterval(fetchProgress, 1200);
}

function stopProgressPolling() {
  if (_progressTimer) {
    clearInterval(_progressTimer);
    _progressTimer = null;
  }
}

async function fetchProgress() {
  if (!_processing) return;
  try {
    const res = await fetch("/progress", { cache: "no-store" });
    if (!res.ok) return;
    const p = await res.json();
    const percent = Number.isFinite(p.percent) ? p.percent : 0;
    const message = p.message || "Processing";
    const stepLabel = mapMessageToStep(message);
    if (stepLabel) updateLoadingStep(stepLabel);
    updateProgress(percent, message);
  } catch (_) {
    // Ignore transient polling errors while the request is still running.
  }
}

function updateProgress(percent, message) {
  const safePercent = Math.max(0, Math.min(100, Math.floor(percent)));
  document.getElementById("progress-bar").style.width = `${safePercent}%`;
  document.getElementById("progress-percent").textContent = `${safePercent}%`;
  document.getElementById("loading-text").textContent =
    `جاري المعالجة: ${message}`;
}

function updateLoadingStep(stepText) {
  const el = document.getElementById("loading-step");
  if (el) el.textContent = stepText;
}

function currentProgress() {
  const txt = document.getElementById("progress-percent").textContent || "0%";
  const n = parseInt(txt.replace("%", ""), 10);
  return Number.isFinite(n) ? n : 0;
}

function mapMessageToStep(message) {
  const m = String(message || "").toLowerCase();
  if (m.includes("uploading file") || m.includes("uploaded file")) {
    return "Uploading file...";
  }
  if (m.includes("processing file") || m.includes("processing uploaded")) {
    return "Processing file...";
  }
  if (m.includes("download")) return "Downloading...";
  if (m.includes("transcrib") || m.includes("whisper"))
    return "Transcribing...";
  if (
    m.includes("generat") ||
    m.includes("clean") ||
    m.includes("faq") ||
    m.includes("thread") ||
    m.includes("tldr")
  ) {
    return "Generating content...";
  }
  if (m.includes("saving transcript") || m.includes("metadata")) {
    return "Saving transcript...";
  }
  return null;
}

function showError(msg) {
  const el = document.getElementById("error-msg");
  el.textContent = msg;
  el.classList.remove("hidden");
}

function clearError() {
  const el = document.getElementById("error-msg");
  el.textContent = "";
  el.classList.add("hidden");
}

function clearResults() {
  document.getElementById("results").classList.add("hidden");
  _currentVideoId = "";
  _actionInFlight = false;
  clearActionError();
  clearActionStatus();
  setText("transcript-content", "");
  setText("tldr-content", "");
  setText("thread-content", "");
  setText("faq-content", "");
  setActionButtonsDisabled(false);
  toggleResultCard("tldr-card", false);
  toggleResultCard("thread-card", false);
  toggleResultCard("faq-card", false);
}

function humanizeError(status, detail) {
  const d = String(detail || "").toLowerCase();

  if (
    status === 422 ||
    d.includes("youtube") ||
    d.includes("url") ||
    d.includes("valid")
  ) {
    return "الرابط غير صالح. أدخل رابط يوتيوب صحيح مثل https://youtu.be/...";
  }

  if (status === 413 || d.includes("too large") || d.includes("max upload")) {
    return "حجم الملف كبير جداً. قلّل الحجم ثم أعد المحاولة.";
  }

  if (d.includes("unsupported file type") || d.includes("unsupported")) {
    return "نوع الملف غير مدعوم. استخدم MP4/MOV/MKV/WEBM أو MP3/WAV/M4A/AAC/OGG.";
  }

  if (d.includes("empty")) {
    return "الملف فارغ. اختر ملفاً صالحاً ثم أعد المحاولة.";
  }

  if (status === 503) {
    return "يوجد طلب آخر قيد المعالجة حالياً. انتظر قليلاً ثم أعد المحاولة.";
  }

  if (status === 502 || d.includes("z.ai") || d.includes("api")) {
    return "تعذّر الوصول لخدمة الذكاء الاصطناعي حالياً. حاول مرة أخرى بعد دقيقة.";
  }

  if (status === 504 || d.includes("timed out")) {
    return "المعالجة أخذت وقتاً أطول من المتوقع. جرّب فيديو أقصر أو أعد المحاولة.";
  }

  if (status >= 500) {
    return "حدث خطأ داخلي أثناء المعالجة. حاول مرة أخرى.";
  }

  return "تعذّر إكمال الطلب. تحقق من الرابط ثم أعد المحاولة.";
}

function getSelectedProvider() {
  const checked = document.querySelector("input[name='provider']:checked");
  return checked?.value || "zai";
}

function getSelectedTranscriptionMode() {
  const sel = document.getElementById("transcription-mode");
  return sel?.value || "balanced";
}

// ── Utils ──────────────────────────────────────────────────────────────────
function escHtml(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function formatDuration(seconds) {
  if (!seconds) return null;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0)
    return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function formatDate(raw) {
  // yt-dlp returns YYYYMMDD
  if (!raw || raw.length !== 8) return raw;
  return `${raw.slice(0, 4)}-${raw.slice(4, 6)}-${raw.slice(6, 8)}`;
}
