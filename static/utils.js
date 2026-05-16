import { els, state } from "./state.js";

export function getSystemPrefersDark() {
  return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export function showMessage(text, isError = false) {
  if (!els.toastContainer) {
    return;
  }

  const toast = document.createElement("div");
  toast.className = `toast${isError ? " error" : ""}`;
  toast.textContent = text;
  els.toastContainer.appendChild(toast);

  requestAnimationFrame(() => {
    toast.classList.add("visible");
  });

  const timeoutId = window.setTimeout(() => {
    toast.classList.remove("visible");
    window.setTimeout(() => {
      toast.remove();
      state.toastTimeouts.delete(toast);
    }, 220);
  }, 3000);

  state.toastTimeouts.set(toast, timeoutId);
}

export async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });

  if (response.status === 401 && path !== "/api/auth/login") {
    window.location.href = "/login";
    throw new Error("Unauthorized");
  }

  if (!response.ok) {
    let detail = "Request failed";
    let detailPayload = null;
    try {
      const payload = await response.json();
      detailPayload = payload.detail;
      if (typeof payload.detail === "string") {
        detail = payload.detail;
      } else if (payload.detail && typeof payload.detail === "object") {
        const checks = Array.isArray(payload.detail.checks) ? payload.detail.checks : [];
        const failed = checks.find((item) => item && item.passed === false);
        const message = String(payload.detail.message || "").trim();
        const hint = failed?.hint ? String(failed.hint).trim() : "";
        const label = failed?.label ? String(failed.label).trim() : "";
        if (label && hint) {
          detail = `${label}. ${hint}`;
        } else if (label) {
          detail = label;
        } else if (message) {
          detail = message;
        }
      }
    } catch {
      // Ignore invalid payload and use generic detail.
    }
    const err = new Error(detail);
    err.detailPayload = detailPayload;
    throw err;
  }

  if (response.status === 204) {
    return null;
  }

  return response.json();
}

export function formToObject(form) {
  const fd = new FormData(form);
  const obj = Object.fromEntries(fd.entries());

  for (const [k, v] of Object.entries(obj)) {
    if (v === "") {
      obj[k] = null;
    }
  }

  return obj;
}

export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

export function formatBytes(bytes) {
  const value = Number(bytes || 0);
  if (!Number.isFinite(value) || value <= 0) {
    return "0 B";
  }
  const units = ["B", "KB", "MB", "GB", "TB"];
  const idx = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  const scaled = value / (1024 ** idx);
  return `${scaled.toFixed(idx === 0 ? 0 : 1)} ${units[idx]}`;
}

export function formatUtcDateTime(input) {
  if (!input) {
    return "-";
  }
  const parsed = new Date(input);
  if (Number.isNaN(parsed.getTime())) {
    return String(input);
  }
  return parsed.toISOString().replace("T", " ").replace(".000Z", "");
}

export async function copyToClipboard(text) {
  const content = String(text || "");
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(content);
      showMessage("Copied to clipboard.");
      return;
    }
    throw new Error("Clipboard API unavailable");
  } catch (_err) {
    const ta = document.createElement("textarea");
    ta.value = content;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.top = "-9999px";
    ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();

    try {
      const ok = document.execCommand("copy");
      if (!ok) {
        throw new Error("execCommand copy failed");
      }
      showMessage("Copied to clipboard.");
    } catch (err) {
      console.error("Failed to copy text: ", err);
      showMessage("Failed to copy to clipboard.", true);
    } finally {
      document.body.removeChild(ta);
    }
  }
}
