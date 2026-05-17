import { els, state } from "./state.js";
import {
  api,
  formToObject,
  showMessage,
} from "./utils.js";
import {
  applyDestinationCapabilities,
  buildTestSignature,
  getIgnoredLabelsPayload,
  getDestinationTestPayloadFromForm,
  getRemapSettingsPayloadFromForm,
  getTransmissionPayloadFromForm,
  getSourceSettingsPayloadFromForm,
  getWatchSourceTestPayloadFromForm,
  markTestApprovalDirty,
  renderDestinationCapabilityInfo,
  renderWatchSourceCapabilityInfo,
  requireFreshTestOrThrow,
  toggleRuleTransferIntervalField,
  toggleWatchSourceFields,
  syncTransmissionContainerUi,
  updateSourceTypeHint,
  updateTestGatedButtons,
} from "./shared.js";
import {
  renderIgnoredLabels,
  renderLogs,
  renderOverview,
  renderRules,
  renderTorrents,
  computeRuleEffectiveMethod,
  updateRuleDestinationOptions,
  updateRuleLabelOptions,
  renderDestinations,
} from "./render.js";

function syncRemapControlsVisibility() {
  const isEnabled = Boolean(els.remapDownloadPath?.checked);
  els.remapPathFields?.classList.toggle("hidden", !isEnabled);
  els.saveRemapBtn?.classList.toggle("hidden", !isEnabled);
}

function setMaskedSecretPlaceholder(input, hasStoredSecret, emptyPlaceholder = "optional") {
  if (!(input instanceof HTMLInputElement)) {
    return;
  }
  input.value = "";
  input.placeholder = hasStoredSecret ? "********" : emptyPlaceholder;
}

export async function refreshTorrents(payload) {
  const data = await api("/api/transmission/torrents", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  state.lastTransmissionPayload = payload;
  state.torrents = data?.torrents || [];
  state.availableLabels = data?.labels || [];

  if (els.knownLabelsDatalist) {
    els.knownLabelsDatalist.innerHTML = state.availableLabels
      .map((label) => `<option value="${label}"></option>`)
      .join("");
  }

  updateRuleLabelOptions();
  renderTorrents();
  renderOverview();
}

export async function refreshTransmission() {
  const cfg = await api("/api/transmission");
  if (!cfg) {
    state.transmissionHasStoredPassword = false;
    setMaskedSecretPlaceholder(els.transmissionForm?.password, false);
    return;
  }

  els.transmissionForm.rpc_domain.value = cfg.rpc_domain || "";
  els.transmissionForm.rpc_port.value = cfg.rpc_port || (cfg.verify_tls ? 443 : 9091);
  els.transmissionForm.rpc_path.value = cfg.rpc_path || "/transmission/rpc";
  els.transmissionForm.username.value = cfg.username || "";
  els.transmissionForm.verify_tls.checked = Boolean(cfg.verify_tls);
  state.transmissionHasStoredPassword = Boolean(cfg.has_password);
  setMaskedSecretPlaceholder(els.transmissionForm?.password, state.transmissionHasStoredPassword);
}

export async function refreshAppSettings() {
  const cfg = await api("/api/app-settings").catch((err) => {
    if (String(err.message || "").includes("404")) {
      return null;
    }
    throw err;
  });

  if (!cfg || !els.generalSettingsForm) {
    toggleWatchSourceFields();
    return;
  }

  state.appSettings = cfg;
  els.transmissionInContainer.checked = Boolean(cfg.transmission_in_container);
  const normalizedWatchSourceKind = cfg.watch_source_kind === "sftp" ? "ssh" : (cfg.watch_source_kind || "local");
  els.watchSourceKind.value = normalizedWatchSourceKind;
  els.generalSettingsForm.watch_base_path.value = cfg.watch_base_path || "";
  if (els.generalSettingsForm.max_parallel_transfers) {
    els.generalSettingsForm.max_parallel_transfers.value = String(Number(cfg.max_parallel_transfers || 3));
  }
  els.generalSettingsForm.watch_host.value = cfg.watch_host || "";
  els.generalSettingsForm.watch_port.value = cfg.watch_port || 22;
  els.generalSettingsForm.watch_username.value = cfg.watch_username || "";
  setMaskedSecretPlaceholder(els.generalSettingsForm.watch_password, Boolean(cfg.has_watch_password));
  els.generalSettingsForm.watch_private_key.value = "";
  els.generalSettingsForm.watch_key_passphrase.value = "";
  state.ignoredLabels = cfg.ignored_labels ? cfg.ignored_labels.split(",").map((l) => l.trim()).filter(Boolean) : [];
  renderIgnoredLabels();
  const savedWatchMethods = (cfg.watch_detected_methods || "")
    .split(",")
    .map((m) => m.trim())
    .filter(Boolean);
  const savedWatchCaps = savedWatchMethods.length
    ? {
        available_methods: savedWatchMethods,
        preferred_method: cfg.watch_detected_preferred_method || null,
      }
    : null;
  state.testCapabilities.watchSourceSftp = savedWatchCaps;
  renderWatchSourceCapabilityInfo(savedWatchCaps);
  if (els.remapDownloadPath) {
    els.remapDownloadPath.checked = Boolean(cfg.remap_download_path);
    syncRemapControlsVisibility();
  }
  if (els.remapForm) {
    if (els.remapSourcePrefix) {
      els.remapSourcePrefix.value = cfg.remap_source_prefix || "";
    }
    if (els.remapTargetPrefix) {
      els.remapTargetPrefix.value = cfg.remap_target_prefix || "";
    }
  }
  syncTransmissionContainerUi();
  updateSourceTypeHint();
  toggleWatchSourceFields();
  updateTestGatedButtons();
}

async function saveIgnoredLabels() {
  const payload = getIgnoredLabelsPayload();
  try {
    const data = await api("/api/app-settings/ignored-labels", {
      method: "PUT",
      body: JSON.stringify(payload),
    });
    state.appSettings = data;
    showMessage("Ignored labels saved.");
  } catch (err) {
    showMessage(err.message, true);
  }
}

async function persistRemapSettings() {
  const payload = getRemapSettingsPayloadFromForm();
  try {
    const data = await api("/api/app-settings/remap", {
      method: "PUT",
      body: JSON.stringify(payload),
    });
    state.appSettings = data;
    updateSourceTypeHint();
    showMessage("Path remapping saved.");
  } catch (err) {
    showMessage(err.message, true);
  }
}

export async function refreshDestinations() {
  state.destinations = await api("/api/destinations");
  renderDestinations();
  updateRuleDestinationOptions();
  renderRuleEffectiveMethodPreview();
}

export async function refreshRules() {
  state.rules = await api("/api/rules");
  renderRules();
  renderOverview();
  renderRuleEffectiveMethodPreview();
}

export async function refreshActivity() {
  const [logs, active] = await Promise.all([
    api("/api/logs?limit=100"),
    api("/api/transfers/active"),
  ]);
  state.logs = logs || [];
  state.activeTransfers = active || [];
  renderLogs(state.logs);
  renderOverview();

  const payload = state.lastTransmissionPayload || getTransmissionPayloadFromForm();
  const canRefreshTorrents = Boolean(payload?.rpc_url) && (!payload.username || Boolean(payload.password) || state.transmissionHasStoredPassword);
  if (canRefreshTorrents) {
    try {
      await refreshTorrents(payload);
    } catch {
      // Keep activity polling resilient if torrent re-sync temporarily fails.
    }
  }
}

export async function refreshAll() {
  await Promise.all([
    refreshAppSettings(),
    refreshTransmission(),
    refreshDestinations(),
    refreshRules(),
    refreshActivity(),
  ]);
  const payload = getTransmissionPayloadFromForm();
  const canAutoRefresh =
    Boolean(payload.rpc_url) &&
    (!payload.username || Boolean(payload.password) || state.transmissionHasStoredPassword);
  if (canAutoRefresh) {
    try {
      await refreshTorrents(payload);
    } catch {
      // Keep the page usable even if torrent loading fails on startup.
    }
  }
}

export function resetDestinationForm() {
  state.editingDestinationId = null;
  els.destinationForm.reset();
  els.destKind.value = "local";
  els.sftpFields.classList.add("hidden");
  if (els.destinationTransferMethod) {
    els.destinationTransferMethod.value = "auto";
  }
  setMaskedSecretPlaceholder(els.destinationForm?.password, false);
  applyDestinationCapabilities(null);
  renderDestinationCapabilityInfo(null);
  if (els.destinationSubmitBtn) {
    els.destinationSubmitBtn.textContent = "Create Destination";
  }
  els.destinationCancelBtn?.classList.add("hidden");
  markTestApprovalDirty("destinationSftp");
  updateTestGatedButtons();
}

export function resetRuleForm() {
  state.editingRuleId = null;
  if (els.ruleLabelSelect) els.ruleLabelSelect.value = "";
  if (els.ruleTransferMode) els.ruleTransferMode.value = "move";
  if (els.ruleParallelismMode) els.ruleParallelismMode.value = "sequential";
  if (els.ruleConflictPolicy) els.ruleConflictPolicy.value = "overwrite";
  if (els.ruleTransferSchedule) els.ruleTransferSchedule.value = "auto";
  if (els.ruleTransferIntervalSeconds) els.ruleTransferIntervalSeconds.value = "300";
  if (els.ruleRemoveFromClient) {
    els.ruleRemoveFromClient.checked = Boolean(state.appSettings?.remove_torrent_on_complete ?? true);
  }
  if (els.ruleTrashDataOnRemove) {
    els.ruleTrashDataOnRemove.checked = false;
    els.ruleTrashDataOnRemove.disabled = !Boolean(els.ruleRemoveFromClient?.checked);
  }
  if (els.ruleEffectiveMethodInfo) {
    els.ruleEffectiveMethodInfo.textContent = "Effective method: select a destination to preview.";
  }
  toggleRuleTransferIntervalField();
  if (els.ruleSubmitBtn) els.ruleSubmitBtn.textContent = "Add Rule";
  els.ruleCancelBtn?.classList.add("hidden");
}

function syncRuleRemovalControls() {
  const shouldRemove = Boolean(els.ruleRemoveFromClient?.checked);
  const mode = String(els.ruleTransferMode?.value || "move").toLowerCase();
  const isMove = mode === "move";
  if (!els.ruleTrashDataOnRemove) {
    return;
  }
  els.ruleTrashDataWrap?.classList.toggle("hidden", !shouldRemove);
  // Trash-data only meaningful in Copy mode (Move already removes data from Transmission).
  const enabled = shouldRemove && !isMove;
  if (!enabled) {
    els.ruleTrashDataOnRemove.checked = false;
  }
  els.ruleTrashDataOnRemove.disabled = !enabled;
  els.ruleTrashDataHint?.classList.toggle("hidden", !(shouldRemove && isMove));
}

function renderRuleEffectiveMethodPreview() {
  if (!els.ruleEffectiveMethodInfo) {
    return;
  }
  const destinationId = Number(els.ruleDestinationSelect?.value || 0);
  const destination = state.destinations.find((d) => Number(d.id) === destinationId);
  if (!destination) {
    els.ruleEffectiveMethodInfo.textContent = "Effective method: select a destination to preview.";
    return;
  }

  const previewRule = {
    transfer_method_preference: "auto",
  };
  const method = computeRuleEffectiveMethod(previewRule, destination);
  els.ruleEffectiveMethodInfo.textContent = `Effective method: ${method}`;
}

export function initEventHandlers() {
  els.destKind.addEventListener("change", () => {
    const isRemote = els.destKind.value === "remote";
    els.sftpFields.classList.toggle("hidden", !isRemote);
    markTestApprovalDirty("destinationSftp");
    updateTestGatedButtons();
  });

  els.watchSourceKind?.addEventListener("change", () => {
    toggleWatchSourceFields();
    updateSourceTypeHint();
    markTestApprovalDirty("watchSourceSftp");
    updateTestGatedButtons();
  });

  els.generalSettingsForm?.addEventListener("input", () => {
    markTestApprovalDirty("watchSourceSftp");
    updateTestGatedButtons();
  });

  els.transmissionForm?.addEventListener("input", (ev) => {
    const target = ev.target;
    if (target instanceof HTMLInputElement && target.id === "transmissionInContainer") {
      return;
    }
    markTestApprovalDirty("transmission");
    updateTestGatedButtons();
  });

  els.transmissionInContainer?.addEventListener("change", () => {
    syncTransmissionContainerUi();
    updateSourceTypeHint();
  });

  els.logoutBtn?.addEventListener("click", () => {
    fetch("/api/auth/logout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    }).finally(() => {
      window.location.href = "/login";
    });
  });

  els.destinationForm?.addEventListener("input", (ev) => {
    const target = ev.target;
    if (!(target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement)) {
      return;
    }
    const nonConnectionFields = new Set([
      "transfer_method_preference",
      "detected_preferred_method",
      "detected_sftp_port",
      "detected_scp_port",
      "detected_rsync_port",
    ]);
    if (nonConnectionFields.has(target.name)) {
      return;
    }
    markTestApprovalDirty("destinationSftp");
    updateTestGatedButtons();
  });

  els.generalSettingsForm?.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const payload = getSourceSettingsPayloadFromForm();

    try {
      if (els.watchSourceKind?.value === "ssh") {
        const hasRemoteDestination = (state.destinations || []).some(
          (dest) => dest.kind === "remote" || dest.kind === "sftp"
        );
        if (hasRemoteDestination) {
          throw new Error("Remote-to-remote transfers are not supported. Switch source to local or convert remote destinations to local.");
        }
      }

      if (els.watchSourceKind?.value === "ssh") {
        const signature = buildTestSignature(getWatchSourceTestPayloadFromForm());
        requireFreshTestOrThrow(
          "watchSourceSftp",
          signature,
          "Run a successful Watch Source SSH Test Connection before saving. Retest after any credential or host change."
        );
      }

      const data = await api("/api/app-settings/source", {
        method: "PUT",
        body: JSON.stringify(payload),
      });
      state.appSettings = data;
      updateSourceTypeHint();
      toggleWatchSourceFields();
      updateTestGatedButtons();
      showMessage("Source settings saved.");
    } catch (err) {
      showMessage(err.message, true);
    }
  });

  els.transmissionForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const payload = getTransmissionPayloadFromForm();

    try {
      const signature = buildTestSignature(payload);
      requireFreshTestOrThrow(
        "transmission",
        signature,
        "Run a successful Transmission Test Connection before saving. Retest after any credential or URL/TLS change."
      );

      await api("/api/transmission", {
        method: "PUT",
        body: JSON.stringify(payload),
      });
      let transmissionContainerWarning = null;
      try {
        const appData = await api("/api/app-settings/transmission-container", {
          method: "PUT",
          body: JSON.stringify({
            transmission_in_container: Boolean(els.transmissionInContainer?.checked),
          }),
        });
        state.appSettings = appData;
        updateSourceTypeHint();
      } catch (secondaryErr) {
        transmissionContainerWarning = secondaryErr;
      }

      updateTestGatedButtons();
      if (transmissionContainerWarning) {
        showMessage(
          `Transmission details saved, but container mode update failed: ${transmissionContainerWarning.message}`,
          true
        );
      } else {
        showMessage("Transmission settings saved.");
      }
    } catch (err) {
      showMessage(err.message, true);
    }
  });

  els.testTransmissionBtn.addEventListener("click", async () => {
    const payload = getTransmissionPayloadFromForm();

    try {
      await api("/api/transmission/test", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      state.testApprovals.transmission = buildTestSignature(payload);
      updateTestGatedButtons();
      await refreshTorrents(payload);
      showMessage("Transmission connection succeeded. Torrents loaded.");
    } catch (err) {
      markTestApprovalDirty("transmission");
      updateTestGatedButtons();
      showMessage(err.message, true);
    }
  });

  els.testDestinationBtn?.addEventListener("click", async () => {
    const payload = getDestinationTestPayloadFromForm();
    const isLocal = !payload.host && !payload.username;
    if (!isLocal && (!payload.host || !payload.username || !payload.base_path)) {
      showMessage("Host, username, and base path are required to test SFTP.", true);
      return;
    }
    try {
      const result = await api("/api/sftp/test", { method: "POST", body: JSON.stringify(payload) });
      state.testApprovals.destinationSftp = buildTestSignature(payload);
      if (isLocal) {
        state.testCapabilities.destinationSftp = null;
        applyDestinationCapabilities(null);
        renderDestinationCapabilityInfo(null);
      } else {
        state.testCapabilities.destinationSftp = result;
        applyDestinationCapabilities(result);
        renderDestinationCapabilityInfo(result);
        if (els.destinationTransferMethod) {
          const allowedMethods = new Set(["auto", "rsync", "scp", "sftp"]);
          const currentMethod = String(els.destinationTransferMethod.value || "").toLowerCase();
          if (!allowedMethods.has(currentMethod)) {
            els.destinationTransferMethod.value = "auto";
          }
          if (state.editingDestinationId == null) {
            els.destinationTransferMethod.value = "auto";
          }
        }
      }
      updateTestGatedButtons();
      if (isLocal) {
        showMessage("Local destination path validation succeeded.");
      } else {
        showMessage("Remote destination validation succeeded. Preferred method and ports captured.");
      }
    } catch (err) {
      markTestApprovalDirty("destinationSftp");
      applyDestinationCapabilities(null);
      updateTestGatedButtons();
      showMessage(err.message, true);
    }
  });

  els.testWatchSourceBtn?.addEventListener("click", async () => {
    const payload = getWatchSourceTestPayloadFromForm();
    if (!payload.host || !payload.username || !payload.base_path) {
      showMessage("Host, username, and base path are required to test remote SSH source.", true);
      return;
    }
    try {
      const result = await api("/api/sftp/test", { method: "POST", body: JSON.stringify(payload) });
      state.testApprovals.watchSourceSftp = buildTestSignature(payload);
      state.testCapabilities.watchSourceSftp = result;
      renderWatchSourceCapabilityInfo(result);
      updateTestGatedButtons();
      showMessage("Remote SSH source connection and path validation succeeded.");
    } catch (err) {
      markTestApprovalDirty("watchSourceSftp");
      updateTestGatedButtons();
      showMessage(err.message, true);
    }
  });

  els.refreshTorrentsBtn.addEventListener("click", async () => {
    try {
      const payload = state.lastTransmissionPayload || getTransmissionPayloadFromForm();
      await refreshTorrents(payload);
      showMessage("Torrents refreshed.");
    } catch (err) {
      showMessage(err.message, true);
    }
  });

  els.destinationForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const payload = formToObject(els.destinationForm);
    payload.port = Number(payload.port || 22);

    if ((state.appSettings?.watch_source_kind || "local") === "ssh" && payload.kind === "remote") {
      showMessage("Remote-to-remote transfers are not supported. Set source to local or use a local destination.", true);
      return;
    }

    try {
      const signature = buildTestSignature(getDestinationTestPayloadFromForm());
      requireFreshTestOrThrow(
        "destinationSftp",
        signature,
        "Run a successful Destination Test Connection before saving. Retest after any path, host, or credential change."
      );

      if (payload.kind === "remote") {
        const caps = state.testCapabilities.destinationSftp;
        if (!caps) {
          throw new Error("Run Destination Test Connection to negotiate transfer capability before saving.");
        }

        const selectedMethod = (payload.transfer_method_preference || "auto").toLowerCase();
        const availableMethods = Array.isArray(caps.available_methods) ? caps.available_methods.map((m) => String(m).toLowerCase()) : [];
        if (selectedMethod !== "auto" && !availableMethods.includes(selectedMethod)) {
          throw new Error(`Selected transfer method '${selectedMethod}' is not supported by the tested remote host.`);
        }

        payload.detected_preferred_method = caps.preferred_method || null;
        payload.detected_sftp_port = caps?.service_ports?.sftp ?? null;
        payload.detected_scp_port = caps?.service_ports?.scp ?? null;
        payload.detected_rsync_port = caps?.service_ports?.rsync ?? null;
      }

      if (state.editingDestinationId) {
        await api(`/api/destinations/${state.editingDestinationId}`, {
          method: "PUT",
          body: JSON.stringify(payload),
        });
        showMessage("Destination updated.");
      } else {
        await api("/api/destinations", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        showMessage("Destination created.");
      }
      resetDestinationForm();
      await refreshDestinations();
      await refreshRules();
    } catch (err) {
      showMessage(err.message, true);
    }
  });

  els.ruleForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const payload = formToObject(els.ruleForm);
    payload.destination_id = Number(payload.destination_id);
    payload.enabled = els.ruleForm.enabled.checked;
    payload.transfer_mode = els.ruleTransferMode?.value || "move";
    payload.parallelism_mode = els.ruleParallelismMode?.value || "sequential";
    payload.conflict_policy = els.ruleConflictPolicy?.value || "overwrite";
    payload.transfer_schedule = els.ruleTransferSchedule?.value || "auto";
    payload.transfer_interval_seconds = Number(els.ruleTransferIntervalSeconds?.value || 300);
    payload.remove_from_client = Boolean(els.ruleRemoveFromClient?.checked);
    payload.trash_data_on_remove = Boolean(els.ruleTrashDataOnRemove?.checked);

    try {
      if (state.editingRuleId) {
        await api(`/api/rules/${state.editingRuleId}`, {
          method: "PUT",
          body: JSON.stringify(payload),
        });
        showMessage("Rule updated.");
      } else {
        await api("/api/rules", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        showMessage("Rule created.");
      }
      resetRuleForm();
      await refreshRules();
    } catch (err) {
      showMessage(err.message, true);
    }
  });

  els.destinationsTable.addEventListener("click", async (ev) => {
    const target = ev.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }

    const deleteId = target.dataset.deleteDestination;
    if (deleteId) {
      const numericId = Number(deleteId);
      const previous = state.destinations.slice();
      state.destinations = state.destinations.filter((d) => Number(d.id) !== numericId);
      renderDestinations();
      updateRuleDestinationOptions();
      try {
        await api(`/api/destinations/${deleteId}`, { method: "DELETE" });
        showMessage("Destination deleted.");
        if (state.editingDestinationId === numericId) {
          resetDestinationForm();
        }
        await refreshRules();
      } catch (err) {
        state.destinations = previous;
        renderDestinations();
        updateRuleDestinationOptions();
        showMessage(err.message, true);
      }
      return;
    }

    const editId = target.dataset.editDestination;
    if (editId) {
      const dest = state.destinations.find((d) => String(d.id) === editId);
      if (!dest) return;
      state.editingDestinationId = Number(editId);
      els.destinationForm.name.value = dest.name;
      els.destKind.value = dest.kind === "sftp" ? "remote" : dest.kind;
      els.sftpFields.classList.toggle("hidden", els.destKind.value !== "remote");
      els.destinationForm.base_path.value = dest.base_path;
      if (els.destinationTransferMethod) {
        els.destinationTransferMethod.value = (dest.transfer_method_preference || "auto").toLowerCase();
      }
      applyDestinationCapabilities({
        preferred_method: dest.detected_preferred_method,
        service_ports: {
          sftp: dest.detected_sftp_port,
          scp: dest.detected_scp_port,
          rsync: dest.detected_rsync_port,
        },
      });
      renderDestinationCapabilityInfo({
        preferred_method: dest.detected_preferred_method,
        service_ports: {
          sftp: dest.detected_sftp_port,
          scp: dest.detected_scp_port,
          rsync: dest.detected_rsync_port,
        },
      });
      if (els.destKind.value === "remote") {
        els.destinationForm.host.value = dest.host || "";
        els.destinationForm.port.value = dest.port || 22;
        els.destinationForm.username.value = dest.username || "";
        setMaskedSecretPlaceholder(els.destinationForm.password, Boolean(dest.has_password));
        els.destinationForm.private_key.value = "";
        els.destinationForm.key_passphrase.value = "";
      }
      if (els.destinationSubmitBtn) els.destinationSubmitBtn.textContent = "Update Destination";
      els.destinationCancelBtn?.classList.remove("hidden");
      els.destinationForm.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  });

  els.rulesTable.addEventListener("click", async (ev) => {
    const target = ev.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }

    const deleteId = target.dataset.deleteRule;
    if (deleteId) {
      const numericId = Number(deleteId);
      const previous = state.rules.slice();
      state.rules = state.rules.filter((r) => Number(r.id) !== numericId);
      renderRules();
      renderOverview();
      try {
        await api(`/api/rules/${deleteId}`, { method: "DELETE" });
        showMessage("Rule deleted.");
        if (state.editingRuleId === numericId) {
          resetRuleForm();
        }
      } catch (err) {
        state.rules = previous;
        renderRules();
        renderOverview();
        showMessage(err.message, true);
      }
      return;
    }

    const editId = target.dataset.editRule;
    if (editId) {
      const rule = state.rules.find((r) => String(r.id) === editId);
      if (!rule) return;
      state.editingRuleId = Number(editId);
      updateRuleLabelOptions(rule.label);
      if (els.ruleLabelSelect) els.ruleLabelSelect.value = rule.label;
      if (els.ruleDestinationSelect) els.ruleDestinationSelect.value = String(rule.destination_id);
      if (els.ruleTransferMode) els.ruleTransferMode.value = rule.transfer_mode || "move";
      if (els.ruleParallelismMode) els.ruleParallelismMode.value = rule.parallelism_mode || "sequential";
      if (els.ruleConflictPolicy) els.ruleConflictPolicy.value = rule.conflict_policy || "overwrite";
      if (els.ruleTransferSchedule) els.ruleTransferSchedule.value = rule.transfer_schedule || "auto";
      if (els.ruleTransferIntervalSeconds) {
        els.ruleTransferIntervalSeconds.value = String(rule.transfer_interval_seconds || 300);
      }
      if (els.ruleRemoveFromClient) {
        els.ruleRemoveFromClient.checked = Boolean(rule.remove_from_client);
      }
      if (els.ruleTrashDataOnRemove) {
        els.ruleTrashDataOnRemove.checked = Boolean(rule.trash_data_on_remove);
      }
      syncRuleRemovalControls();
      renderRuleEffectiveMethodPreview();
      toggleRuleTransferIntervalField();
      if (els.ruleForm.enabled) els.ruleForm.enabled.checked = rule.enabled;
      if (els.ruleSubmitBtn) els.ruleSubmitBtn.textContent = "Update Rule";
      els.ruleCancelBtn?.classList.remove("hidden");
      els.ruleForm.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  });

  els.runOnceBtn.addEventListener("click", async () => {
    try {
      await api("/api/run-once", { method: "POST" });
      await refreshActivity();
      showMessage("Scan completed.");
    } catch (err) {
      showMessage(err.message, true);
    }
  });

  els.clearLogsBtn?.addEventListener("click", async () => {
    try {
      await api("/api/logs", { method: "DELETE" });
      state.logs = [];
      renderLogs(state.logs);
      showMessage("Activity log cleared.");
    } catch (err) {
      showMessage(err.message, true);
    }
  });

  els.overviewTable?.addEventListener("click", async (ev) => {
    const target = ev.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }

    const torrentId = target.dataset.transferNow;
    if (!torrentId) {
      return;
    }

    try {
      await api(`/api/transfer/torrent/${torrentId}`, { method: "POST" });
      await refreshActivity();
      showMessage("Transfer started for selected torrent.");
    } catch (err) {
      showMessage(err.message, true);
    }
  });

  els.torrentsTable.addEventListener("click", async (ev) => {
    const target = ev.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }

    const removeTorrentId = target.dataset.removeLabelTorrentId;
    const removeLabelEncoded = target.dataset.removeLabel;
    if (removeTorrentId && removeLabelEncoded) {
      const payload = state.lastTransmissionPayload || getTransmissionPayloadFromForm();
      if (!payload?.rpc_url) {
        showMessage("Test Transmission connection first.", true);
        return;
      }

      const labelToRemove = decodeURIComponent(removeLabelEncoded);
      try {
        await api("/api/transmission/torrents/label/remove", {
          method: "POST",
          body: JSON.stringify({
            ...payload,
            torrent_id: Number(removeTorrentId),
            label: labelToRemove,
          }),
        });
        await refreshTorrents(payload);
        showMessage(`Removed label '${labelToRemove}'.`);
      } catch (err) {
        showMessage(err.message, true);
      }
      return;
    }

    const torrentId = target.dataset.assignLabel;
    if (!torrentId) {
      return;
    }

    const input = els.torrentsTable.querySelector(`input[data-label-input="${torrentId}"]`);
    if (!(input instanceof HTMLInputElement)) {
      return;
    }

    const label = input.value.trim();
    if (!label) {
      showMessage("Enter a label before assigning.", true);
      return;
    }

    const payload = state.lastTransmissionPayload || getTransmissionPayloadFromForm();
    if (!payload?.rpc_url) {
      showMessage("Test Transmission connection first.", true);
      return;
    }

    try {
      await api("/api/transmission/torrents/label", {
        method: "POST",
        body: JSON.stringify({
          ...payload,
          torrent_id: Number(torrentId),
          label,
        }),
      });
      input.value = "";
      await refreshTorrents(payload);
      showMessage("Label assigned to torrent.");
    } catch (err) {
      showMessage(err.message, true);
    }
  });

  els.remapDownloadPath?.addEventListener("change", () => {
    syncRemapControlsVisibility();
  });

  els.saveRemapBtn?.addEventListener("click", async () => {
    await persistRemapSettings();
  });

  function attachRemapEnterToSave(input) {
    input?.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        ev.preventDefault();
        els.saveRemapBtn?.click();
      }
    });
  }
  attachRemapEnterToSave(els.remapSourcePrefix);
  attachRemapEnterToSave(els.remapTargetPrefix);

  els.reseedStaticBtn?.addEventListener("click", async () => {
    const confirmed = window.confirm(
      "This will overwrite the live web UI files (HTML/CSS/JS) with the image-baked defaults. Local edits to static/ will be lost. The page will reload automatically. Continue?"
    );
    if (!confirmed) {
      return;
    }
    const btn = els.reseedStaticBtn;
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Resetting...";
    try {
      const result = await api("/api/app-settings/reseed-static", { method: "POST" });
      const count = result?.count ?? 0;
      showMessage(`Reset ${count} web file${count === 1 ? "" : "s"} from defaults. Reloading...`);
      // Hard reload with a cache-busting query so the browser re-fetches every
      // JS/CSS module instead of serving the previously cached user-edited copies.
      setTimeout(() => {
        const cacheBust = `tm_reseed=${Date.now()}`;
        const target = `${window.location.pathname}?${cacheBust}${window.location.hash || ""}`;
        window.location.replace(target);
      }, 600);
    } catch (err) {
      showMessage(err.message, true);
      btn.disabled = false;
      btn.textContent = originalText;
    }
  });

  els.destinationCancelBtn?.addEventListener("click", () => {
    resetDestinationForm();
  });

  els.ruleCancelBtn?.addEventListener("click", () => {
    resetRuleForm();
  });

  els.ruleTransferSchedule?.addEventListener("change", () => {
    toggleRuleTransferIntervalField();
  });

  els.ruleDestinationSelect?.addEventListener("change", () => {
    renderRuleEffectiveMethodPreview();
  });

  els.ruleRemoveFromClient?.addEventListener("change", () => {
    syncRuleRemovalControls();
  });

  els.ruleTransferMode?.addEventListener("change", () => {
    syncRuleRemovalControls();
  });

  els.addIgnoredLabelBtn?.addEventListener("click", async () => {
    const val = els.ignoredLabelsInput?.value?.trim();
    if (!val || state.ignoredLabels.includes(val)) {
      return;
    }
    const previous = state.ignoredLabels.slice();
    state.ignoredLabels.push(val);
    if (els.ignoredLabelsInput) els.ignoredLabelsInput.value = "";
    renderIgnoredLabels();
    renderOverview();
    try {
      const data = await api("/api/app-settings/ignored-labels", {
        method: "PUT",
        body: JSON.stringify(getIgnoredLabelsPayload()),
      });
      state.appSettings = data;
      showMessage(`Added ignored label "${val}".`);
    } catch (err) {
      state.ignoredLabels = previous;
      renderIgnoredLabels();
      renderOverview();
      showMessage(err.message, true);
    }
  });

  els.ignoredLabelsInput?.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") {
      ev.preventDefault();
      els.addIgnoredLabelBtn?.click();
    }
  });

  els.ignoredLabelsList?.addEventListener("click", async (ev) => {
    const target = ev.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    const label = target.dataset.removeIgnored;
    if (!label) {
      return;
    }
    const previous = state.ignoredLabels.slice();
    state.ignoredLabels = state.ignoredLabels.filter((l) => l !== label);
    renderIgnoredLabels();
    renderOverview();
    try {
      const data = await api("/api/app-settings/ignored-labels", {
        method: "PUT",
        body: JSON.stringify(getIgnoredLabelsPayload()),
      });
      state.appSettings = data;
      showMessage(`Removed ignored label "${label}".`);
    } catch (err) {
      state.ignoredLabels = previous;
      renderIgnoredLabels();
      renderOverview();
      showMessage(err.message, true);
    }
  });

  els.changePasswordForm?.addEventListener("submit", async (ev) => {
    ev.preventDefault();

    const oldPassword = els.currentPassword?.value?.trim();
    const newPassword = els.newPassword?.value?.trim();
    const confirmPassword = els.confirmNewPassword?.value?.trim();

    if (!oldPassword || !newPassword || !confirmPassword) {
      showMessage("All password fields are required", true);
      return;
    }

    if (newPassword !== confirmPassword) {
      showMessage("New passwords do not match", true);
      return;
    }

    if (newPassword.length < 8) {
      showMessage("Password must be at least 8 characters", true);
      return;
    }

    try {
      await api("/api/auth/change-password", {
        method: "POST",
        body: JSON.stringify({
          old_password: oldPassword,
          new_password: newPassword,
        }),
      });

      // Clear form
      els.currentPassword.value = "";
      els.newPassword.value = "";
      els.confirmNewPassword.value = "";

      showMessage("Password changed successfully. You may need to log in again.");
    } catch (err) {
      showMessage(err.message, true);
    }
  });

  // Initial trash-data hint state and reseed availability probe.
  syncRuleRemovalControls();
  probeReseedAvailability();
}

async function probeReseedAvailability() {
  if (!els.reseedStaticBtn) {
    return;
  }
  try {
    const data = await api("/api/app-settings/reseed-static");
    const available = Boolean(data?.available);
    els.reseedStaticBtn.disabled = !available;
    els.reseedStaticHint?.classList.toggle("hidden", available);
  } catch {
    // Endpoint not present; leave defaults alone.
  }
}
