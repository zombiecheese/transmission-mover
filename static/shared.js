import { els, state, THEME_STORAGE_KEY } from "./state.js";
import { formToObject, getSystemPrefersDark, showMessage } from "./utils.js";

const DEFAULT_RPC_PATH = "/transmission/rpc";
const DEFAULT_RPC_PORT = 9091;
const DEFAULT_TLS_RPC_PORT = 443;

function normalizeRpcPath(path) {
  const trimmed = String(path || "").trim();
  if (!trimmed) {
    return DEFAULT_RPC_PATH;
  }
  return trimmed.startsWith("/") ? trimmed : `/${trimmed}`;
}

function normalizeRpcDomain(domain) {
  const raw = String(domain || "").trim();
  if (!raw) {
    return "";
  }
  try {
    const parsed = new URL(raw);
    return `${parsed.protocol}//${parsed.hostname}`;
  } catch {
    return raw.replace(/\/+$/, "").replace(/:\d+$/, "");
  }
}

function normalizeRpcPort(port, verifyTls) {
  const raw = String(port || "").trim();
  if (!raw) {
    return verifyTls ? DEFAULT_TLS_RPC_PORT : DEFAULT_RPC_PORT;
  }
  const parsed = Number(raw);
  if (!Number.isFinite(parsed) || parsed < 1 || parsed > 65535) {
    return verifyTls ? DEFAULT_TLS_RPC_PORT : DEFAULT_RPC_PORT;
  }
  return Math.trunc(parsed);
}

function buildRpcUrl(domain, port, path, verifyTls) {
  const normalizedDomain = normalizeRpcDomain(domain);
  const normalizedPort = normalizeRpcPort(port, verifyTls);
  const normalizedPath = normalizeRpcPath(path);
  return normalizedDomain ? `${normalizedDomain}:${normalizedPort}${normalizedPath}` : "";
}

export function setSettingsView(view) {
  const normalized = view === "transmission" ? "transmission" : view === "security" ? "security" : "general";
  els.settingsGeneralView?.classList.toggle("hidden", normalized !== "general");
  els.settingsTransmissionView?.classList.toggle("hidden", normalized !== "transmission");
  els.settingsSecurityView?.classList.toggle("hidden", normalized !== "security");

  const tabs = els.settingsMenu?.querySelectorAll("[data-settings-view]") || [];
  for (const tab of tabs) {
    const isActive = tab.dataset.settingsView === normalized;
    tab.classList.toggle("active", isActive);
    tab.classList.toggle("secondary", !isActive);
  }
}

export function setSettingsPanelOpen(isOpen) {
  els.settingsPanel?.classList.toggle("hidden", !isOpen);
  if (els.settingsToggleBtn) {
    els.settingsToggleBtn.setAttribute("aria-expanded", isOpen ? "true" : "false");
  }
}

export function setMoveRulesPanelOpen(isOpen) {
  els.moveRulesPanel?.classList.toggle("hidden", !isOpen);
  if (els.moveRulesToggleBtn) {
    els.moveRulesToggleBtn.setAttribute("aria-expanded", isOpen ? "true" : "false");
  }
}

export function initSettingsMenu() {
  setSettingsPanelOpen(false);
  setMoveRulesPanelOpen(false);
  setSettingsView("transmission");

  els.settingsToggleBtn?.addEventListener("click", () => {
    const isHidden = els.settingsPanel?.classList.contains("hidden");
    setSettingsPanelOpen(Boolean(isHidden));
  });

  els.moveRulesToggleBtn?.addEventListener("click", () => {
    const isHidden = els.moveRulesPanel?.classList.contains("hidden");
    setMoveRulesPanelOpen(Boolean(isHidden));
  });

  els.settingsMenu?.addEventListener("click", (ev) => {
    const target = ev.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }

    const view = target.dataset.settingsView;
    if (!view) {
      return;
    }

    setSettingsView(view);
  });
}

export function applyTheme(mode) {
  const normalized = mode === "light" || mode === "dark" ? mode : "auto";
  const resolved = normalized === "auto" ? (getSystemPrefersDark() ? "dark" : "light") : normalized;
  document.documentElement.dataset.theme = resolved;
  state.themeMode = normalized;
  if (els.themeMode) {
    els.themeMode.value = normalized;
  }
}

export function loadThemePreference() {
  const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
  applyTheme(stored || "auto");
}

export function initThemeControls() {
  loadThemePreference();

  if (els.themeMode) {
    els.themeMode.addEventListener("change", () => {
      const mode = els.themeMode.value;
      window.localStorage.setItem(THEME_STORAGE_KEY, mode);
      applyTheme(mode);
      showMessage(`Theme set to ${mode}.`);
    });
  }

  if (window.matchMedia) {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    media.addEventListener("change", () => {
      if (state.themeMode === "auto") {
        applyTheme("auto");
      }
    });
  }
}

export function toggleWatchSourceFields() {
  const isSsh = els.watchSourceKind?.value === "ssh";
  els.watchSourceSftpFields?.classList.toggle("hidden", !isSsh);
  els.generalSettingsActions?.classList.toggle("hidden", isSsh);
}

export function toggleRuleTransferIntervalField() {
  const isInterval = els.ruleTransferSchedule?.value === "interval";
  if (!els.ruleTransferIntervalSeconds) {
    return;
  }
  els.ruleTransferIntervalSeconds.disabled = !isInterval;
  els.ruleTransferIntervalSeconds.classList.toggle("hidden", !isInterval);
}

export function syncTransmissionContainerUi() {
  const inContainer = Boolean(els.transmissionInContainer?.checked);
  els.remapForm?.classList.toggle("hidden", !inContainer);
}

export function updateSourceTypeHint() {
  syncTransmissionContainerUi();

  if (!els.sourceTypeHint) {
    return;
  }
  const inContainer = Boolean(els.transmissionInContainer?.checked);
  const kind = els.watchSourceKind?.value || "local";
  const localOption = els.watchSourceKind?.querySelector('option[value="local"]');
  const sshOption = els.watchSourceKind?.querySelector('option[value="ssh"]');

  if (localOption) {
    localOption.textContent = inContainer ? "Local shared mounted path" : "Local host/path from Transmission";
  }
  if (sshOption) {
    sshOption.textContent = "Remote SSH negotiation";
  }

  if (els.generalSettingsForm?.watch_base_path) {
    els.generalSettingsForm.watch_base_path.placeholder = inContainer
      ? "/watch or shared mount path"
      : "/volume1/downloads/completed (or leave blank to use Transmission path)";
  }

  if (!inContainer) {
    els.sourceTypeHint.textContent = "Transmission runs directly on the host: local source can use host paths reported by Transmission, or remote SSH negotiation if preferred.";
    return;
  }
  if (kind === "local") {
    els.sourceTypeHint.textContent = "Transmission is containerized: local source requires a shared mounted path visible to this app container.";
  } else {
    els.sourceTypeHint.textContent = "Transmission is containerized: use remote SSH negotiation when host paths are not shared or differ from container paths.";
  }
}

export function getAppSettingsPayloadFromForm() {
  const payload = {
    ...(state.appSettings || {}),
    ...formToObject(els.generalSettingsForm),
  };
  payload.transmission_in_container = Boolean(els.transmissionInContainer?.checked);
  payload.remove_torrent_on_complete = els.generalSettingsForm.remove_torrent_on_complete.checked;
  payload.transfer_schedule = payload.transfer_schedule || "auto";
  payload.transfer_interval_seconds = Number(payload.transfer_interval_seconds || 300);
  payload.watch_port = Number(payload.watch_port || 22);
  payload.watch_attempt_sudo = Boolean(els.generalSettingsForm.watch_attempt_sudo?.checked);
  payload.ignored_labels = state.ignoredLabels.join(",");
  payload.remap_download_path = Boolean(els.remapDownloadPath?.checked);
  payload.remap_source_prefix = els.remapSourcePrefix?.value?.trim() || null;
  payload.remap_target_prefix = els.remapTargetPrefix?.value?.trim() || null;
  return payload;
}

export function getTransmissionPayloadFromForm() {
  const rpc_domain = els.transmissionForm.rpc_domain.value;
  const verify_tls = Boolean(els.transmissionForm.verify_tls.checked);
  const rpc_port = normalizeRpcPort(els.transmissionForm.rpc_port.value, verify_tls);
  const rpc_path = normalizeRpcPath(els.transmissionForm.rpc_path.value);
  return {
    rpc_domain,
    rpc_port,
    rpc_path,
    rpc_url: buildRpcUrl(rpc_domain, rpc_port, rpc_path, verify_tls),
    username: els.transmissionForm.username.value || null,
    password: els.transmissionForm.password.value || null,
    verify_tls,
  };
}

export function getWatchSourceTestPayloadFromForm() {
  const f = els.generalSettingsForm;
  return {
    role: "source",
    host: f.watch_host?.value?.trim() || "",
    port: Number(f.watch_port?.value || 22),
    username: f.watch_username?.value?.trim() || "",
    attempt_sudo: Boolean(f.watch_attempt_sudo?.checked),
    password: f.watch_password?.value || null,
    private_key: f.watch_private_key?.value || null,
    key_passphrase: f.watch_key_passphrase?.value || null,
    base_path: f.watch_base_path?.value?.trim() || null,
  };
}

export function getDestinationTestPayloadFromForm() {
  const f = els.destinationForm;
  return {
    role: "destination",
    host: f.host?.value?.trim() || "",
    port: Number(f.port?.value || 22),
    username: f.username?.value?.trim() || "",
    attempt_sudo: Boolean(f.attempt_sudo?.checked),
    password: f.password?.value || null,
    private_key: f.private_key?.value || null,
    key_passphrase: f.key_passphrase?.value || null,
    base_path: f.base_path?.value?.trim() || null,
  };
}

export function buildTestSignature(payload) {
  return JSON.stringify({
    host: payload.host || "",
    port: Number(payload.port || 0),
    username: payload.username || "",
    attempt_sudo: payload.attempt_sudo === true,
    password: payload.password || "",
    private_key: payload.private_key || "",
    key_passphrase: payload.key_passphrase || "",
    base_path: payload.base_path || "",
    rpc_url: payload.rpc_url || "",
    verify_tls: payload.verify_tls === true,
  });
}

export function setDestinationHiddenField(name, value) {
  const input = els.destinationForm?.elements?.namedItem(name);
  if (input instanceof HTMLInputElement) {
    input.value = value == null ? "" : String(value);
  }
}

export function applyDestinationCapabilities(result) {
  if (!result) {
    setDestinationHiddenField("detected_preferred_method", "");
    setDestinationHiddenField("detected_sftp_port", "");
    setDestinationHiddenField("detected_scp_port", "");
    setDestinationHiddenField("detected_rsync_port", "");
    return;
  }

  setDestinationHiddenField("detected_preferred_method", result.preferred_method || "");
  setDestinationHiddenField("detected_sftp_port", result?.service_ports?.sftp ?? "");
  setDestinationHiddenField("detected_scp_port", result?.service_ports?.scp ?? "");
  setDestinationHiddenField("detected_rsync_port", result?.service_ports?.rsync ?? "");
}

export function renderDestinationCapabilityInfo(result) {
  if (!els.destinationCapabilityInfo) {
    return;
  }
  if (!result) {
    els.destinationCapabilityInfo.textContent = "Run Test Connection to detect preferred method and service ports.";
    return;
  }

  const ports = result.service_ports || {};
  const preferred = result.preferred_method || "sftp";
  const parts = [
    `Preferred: ${preferred}`,
    `SFTP: ${ports.sftp ?? "n/a"}`,
    `SCP: ${ports.scp ?? "n/a"}`,
    `rsync: ${ports.rsync ?? "n/a"}`,
  ];
  els.destinationCapabilityInfo.textContent = parts.join(" | ");
}

export function renderWatchSourceCapabilityInfo(result) {
  if (!els.watchSourceCapabilityInfo) {
    return;
  }
  if (!result) {
    els.watchSourceCapabilityInfo.textContent = "Run Test Connection to detect source transfer methods.";
    return;
  }

  const methods = Array.isArray(result.available_methods) ? result.available_methods : [];
  const preferred = result.preferred_method || "sftp";
  els.watchSourceCapabilityInfo.textContent = `Discovered: ${methods.join(", ") || "none"} | Preferred: ${preferred}`;
}

export function markTestApprovalDirty(kind) {
  if (state.testApprovals[kind] !== null) {
    state.testApprovals[kind] = null;
  }
  if (Object.prototype.hasOwnProperty.call(state.testCapabilities, kind)) {
    state.testCapabilities[kind] = null;
  }
  if (kind === "destinationSftp") {
    renderDestinationCapabilityInfo(null);
  }
  if (kind === "watchSourceSftp") {
    renderWatchSourceCapabilityInfo(null);
  }
  updateTestGatedButtons();
}

export function requireFreshTestOrThrow(kind, signature, message) {
  if (state.testApprovals[kind] !== signature) {
    throw new Error(message);
  }
}

export function updateTestGatedButtons() {
  const transmissionSignature = buildTestSignature(getTransmissionPayloadFromForm());
  const transmissionReady = state.testApprovals.transmission === transmissionSignature;
  if (els.transmissionSubmitBtn) {
    els.transmissionSubmitBtn.disabled = !transmissionReady;
  }

  const destinationSignature = buildTestSignature(getDestinationTestPayloadFromForm());
  const destinationReady = state.testApprovals.destinationSftp === destinationSignature;
  if (els.destinationSubmitBtn) {
    els.destinationSubmitBtn.disabled = !destinationReady;
  }

  const watchSourceSignature = buildTestSignature(getWatchSourceTestPayloadFromForm());
  const watchSourceRequiresTest = els.watchSourceKind?.value === "ssh";
  const watchSourceReady = !watchSourceRequiresTest || state.testApprovals.watchSourceSftp === watchSourceSignature;
  if (els.generalSettingsSubmitBtn) {
    els.generalSettingsSubmitBtn.disabled = !watchSourceReady;
  }
  if (els.watchSourceSaveBtn) {
    els.watchSourceSaveBtn.disabled = !watchSourceReady;
  }
}

export function checkRemoteToRemoteTransfer() {
  const isRemoteSource = (state.appSettings?.watch_source_kind || "local") === "ssh";
  const isRemoteDestination = state.destinations.some(
    (dest) => dest.kind === "remote" || dest.kind === "sftp"
  );

  const warningBanner = document.getElementById("warningBanner");
  if (!warningBanner) {
    return;
  }

  if (isRemoteSource && isRemoteDestination) {
    warningBanner.classList.remove("hidden");
  } else {
    warningBanner.classList.add("hidden");
  }
}
