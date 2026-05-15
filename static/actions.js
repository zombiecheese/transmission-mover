import { els, state } from "./state.js";
import {
  api,
  formToObject,
  showMessage,
} from "./utils.js";
import {
  applyDestinationCapabilities,
  buildTestSignature,
  checkRemoteToRemoteTransfer,
  getAppSettingsPayloadFromForm,
  getDestinationTestPayloadFromForm,
  getTransmissionPayloadFromForm,
  getWatchSourceTestPayloadFromForm,
  markTestApprovalDirty,
  renderDestinationCapabilityInfo,
  requireFreshTestOrThrow,
  toggleRuleTransferIntervalField,
  toggleWatchSourceFields,
  updateSourceTypeHint,
  updateTestGatedButtons,
} from "./shared.js";
import {
  renderIgnoredLabels,
  renderLogs,
  renderOverview,
  renderRules,
  renderTorrents,
  updateRuleDestinationOptions,
  updateRuleLabelOptions,
  renderDestinations,
} from "./render.js";

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
    return;
  }

  els.transmissionForm.rpc_url.value = cfg.rpc_url || "";
  els.transmissionForm.username.value = cfg.username || "";
  els.transmissionForm.password.value = "";
  els.transmissionForm.verify_tls.checked = Boolean(cfg.verify_tls);
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
  els.generalSettingsForm.remove_torrent_on_complete.checked = Boolean(cfg.remove_torrent_on_complete);
  els.watchSourceKind.value = cfg.watch_source_kind || "local";
  els.generalSettingsForm.watch_base_path.value = cfg.watch_base_path || "";
  els.generalSettingsForm.watch_host.value = cfg.watch_host || "";
  els.generalSettingsForm.watch_port.value = cfg.watch_port || 22;
  els.generalSettingsForm.watch_username.value = cfg.watch_username || "";
  els.generalSettingsForm.watch_password.value = "";
  els.generalSettingsForm.watch_private_key.value = "";
  els.generalSettingsForm.watch_key_passphrase.value = "";
  state.ignoredLabels = cfg.ignored_labels ? cfg.ignored_labels.split(",").map((l) => l.trim()).filter(Boolean) : [];
  renderIgnoredLabels();
  if (els.remapDownloadPath) {
    els.remapDownloadPath.checked = Boolean(cfg.remap_download_path);
    els.remapPathFields?.classList.toggle("hidden", !cfg.remap_download_path);
  }
  if (els.remapForm) {
    els.remapForm.remap_source_prefix.value = cfg.remap_source_prefix || "";
    els.remapForm.remap_target_prefix.value = cfg.remap_target_prefix || "";
  }
  updateSourceTypeHint();
  toggleWatchSourceFields();
  updateTestGatedButtons();
  checkRemoteToRemoteTransfer();
}

export async function refreshDestinations() {
  state.destinations = await api("/api/destinations");
  renderDestinations();
  updateRuleDestinationOptions();
}

export async function refreshRules() {
  state.rules = await api("/api/rules");
  renderRules();
  renderOverview();
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
  if (payload.rpc_url) {
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
  if (els.ruleTransferSchedule) els.ruleTransferSchedule.value = "auto";
  if (els.ruleTransferIntervalSeconds) els.ruleTransferIntervalSeconds.value = "300";
  toggleRuleTransferIntervalField();
  if (els.ruleSubmitBtn) els.ruleSubmitBtn.textContent = "Add Rule";
  els.ruleCancelBtn?.classList.add("hidden");
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

  els.transmissionForm?.addEventListener("input", () => {
    markTestApprovalDirty("transmission");
    updateTestGatedButtons();
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
    const payload = getAppSettingsPayloadFromForm();

    try {
      if (els.watchSourceKind?.value === "sftp") {
        const signature = buildTestSignature(getWatchSourceTestPayloadFromForm());
        requireFreshTestOrThrow(
          "watchSourceSftp",
          signature,
          "Run a successful Watch Source SFTP Test Connection before saving. Retest after any credential or host change."
        );
      }

      const data = await api("/api/app-settings", {
        method: "PUT",
        body: JSON.stringify(payload),
      });
      state.appSettings = data;
      updateSourceTypeHint();
      toggleWatchSourceFields();
      updateTestGatedButtons();
      checkRemoteToRemoteTransfer();
      showMessage("General settings saved.");
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
      const appPayload = {
        ...(state.appSettings || {}),
        transmission_in_container: Boolean(els.transmissionInContainer?.checked),
      };
      const appData = await api("/api/app-settings", {
        method: "PUT",
        body: JSON.stringify(appPayload),
      });
      state.appSettings = appData;
      updateSourceTypeHint();
      updateTestGatedButtons();
      showMessage("Transmission settings saved.");
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
    if (!payload.host || !payload.username || !payload.base_path) {
      showMessage("Host, username, and base path are required to test SFTP.", true);
      return;
    }
    try {
      const result = await api("/api/sftp/test", { method: "POST", body: JSON.stringify(payload) });
      state.testApprovals.destinationSftp = buildTestSignature(payload);
      state.testCapabilities.destinationSftp = result;
      applyDestinationCapabilities(result);
      renderDestinationCapabilityInfo(result);
      if (els.destinationTransferMethod && (!els.destinationTransferMethod.value || els.destinationTransferMethod.value === "auto")) {
        els.destinationTransferMethod.value = result?.preferred_method || "sftp";
      }
      updateTestGatedButtons();
      showMessage("Remote destination validation succeeded. Preferred method and ports captured.");
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
      showMessage("Host, username, and base path are required to test SFTP.", true);
      return;
    }
    try {
      await api("/api/sftp/test", { method: "POST", body: JSON.stringify(payload) });
      state.testApprovals.watchSourceSftp = buildTestSignature(payload);
      updateTestGatedButtons();
      showMessage("SFTP watch source connection and path validation succeeded.");
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

    try {
      if (payload.kind === "remote") {
        const signature = buildTestSignature(getDestinationTestPayloadFromForm());
        requireFreshTestOrThrow(
          "destinationSftp",
          signature,
          "Run a successful Destination Test Connection before saving. Retest after any credential or host/path change."
        );

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
    payload.transfer_schedule = els.ruleTransferSchedule?.value || "auto";
    payload.transfer_interval_seconds = Number(els.ruleTransferIntervalSeconds?.value || 300);

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
      try {
        await api(`/api/destinations/${deleteId}`, { method: "DELETE" });
        showMessage("Destination deleted.");
        if (state.editingDestinationId === Number(deleteId)) {
          resetDestinationForm();
        }
        await refreshDestinations();
        await refreshRules();
      } catch (err) {
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
        els.destinationForm.password.value = "";
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
      try {
        await api(`/api/rules/${deleteId}`, { method: "DELETE" });
        showMessage("Rule deleted.");
        if (state.editingRuleId === Number(deleteId)) {
          resetRuleForm();
        }
        await refreshRules();
      } catch (err) {
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
      if (els.ruleTransferSchedule) els.ruleTransferSchedule.value = rule.transfer_schedule || "auto";
      if (els.ruleTransferIntervalSeconds) {
        els.ruleTransferIntervalSeconds.value = String(rule.transfer_interval_seconds || 300);
      }
      toggleRuleTransferIntervalField();
      if (els.ruleForm.enabled) els.ruleForm.enabled.checked = rule.enabled;
      if (els.ruleSubmitBtn) els.ruleSubmitBtn.textContent = "Update Rule";
      els.ruleCancelBtn?.classList.remove("hidden");
      els.ruleForm.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  });

  els.runOnceBtn.addEventListener("click", async () => {
    try {
      els.statusChip.textContent = "Running";
      await api("/api/run-once", { method: "POST" });
      await refreshActivity();
      showMessage("Scan completed.");
    } catch (err) {
      showMessage(err.message, true);
    } finally {
      els.statusChip.textContent = "Idle";
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
    els.remapPathFields?.classList.toggle("hidden", !els.remapDownloadPath.checked);
  });

  els.remapForm?.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const merged = {
      ...(state.appSettings || {}),
      ignored_labels: state.ignoredLabels.join(","),
      transmission_in_container: Boolean(els.transmissionInContainer?.checked),
      remap_download_path: els.remapDownloadPath?.checked ?? false,
      remap_source_prefix: els.remapForm.remap_source_prefix.value.trim() || null,
      remap_target_prefix: els.remapForm.remap_target_prefix.value.trim() || null,
    };
    try {
      const data = await api("/api/app-settings", {
        method: "PUT",
        body: JSON.stringify(merged),
      });
      state.appSettings = data;
      updateSourceTypeHint();
      showMessage("Path remapping saved.");
    } catch (err) {
      showMessage(err.message, true);
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

  els.addIgnoredLabelBtn?.addEventListener("click", () => {
    const val = els.ignoredLabelsInput?.value?.trim();
    if (!val || state.ignoredLabels.includes(val)) {
      return;
    }
    state.ignoredLabels.push(val);
    if (els.ignoredLabelsInput) els.ignoredLabelsInput.value = "";
    renderIgnoredLabels();
    renderOverview();
  });

  els.ignoredLabelsInput?.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") {
      ev.preventDefault();
      els.addIgnoredLabelBtn?.click();
    }
  });

  els.ignoredLabelsList?.addEventListener("click", (ev) => {
    const target = ev.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    const label = target.dataset.removeIgnored;
    if (!label) {
      return;
    }
    state.ignoredLabels = state.ignoredLabels.filter((l) => l !== label);
    renderIgnoredLabels();
    renderOverview();
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
}
