import { els, state } from "./state.js";
import { copyToClipboard, escapeHtml, formatBytes, formatUtcDateTime } from "./utils.js";

function parseMethodList(csv) {
  return String(csv || "")
    .split(",")
    .map((m) => m.trim().toLowerCase())
    .filter(Boolean);
}

function chooseMethodByPreference(availableMethods, rulePref, destinationPref, detectedPreferred) {
  const available = new Set(availableMethods);
  const orderedFallback = ["rsync", "scp", "sftp"];

  const rp = String(rulePref || "auto").toLowerCase();
  const dp = String(destinationPref || "auto").toLowerCase();
  const detected = String(detectedPreferred || "").toLowerCase();

  if (rp !== "auto") {
    if (available.has(rp)) {
      return rp;
    }
    return orderedFallback.find((m) => available.has(m)) || "sftp";
  }

  if (dp !== "auto") {
    if (available.has(dp)) {
      return dp;
    }
    return orderedFallback.find((m) => available.has(m)) || "sftp";
  }

  if (detected && available.has(detected)) {
    return detected;
  }

  return orderedFallback.find((m) => available.has(m)) || "sftp";
}

export function computeRuleEffectiveMethod(rule, destination) {
  const sourceKind = (state.appSettings?.watch_source_kind || "local").toLowerCase();
  const destinationKind = (destination?.kind || "local").toLowerCase();

  if (sourceKind === "local" && destinationKind === "local") {
    return "local filesystem";
  }

  if (sourceKind === "ssh" && destinationKind === "local") {
    return "sftp (source -> local)";
  }

  if (sourceKind === "local" && (destinationKind === "remote" || destinationKind === "sftp")) {
    const destMethods = parseMethodList(destination?.detected_methods || "") || [];
    const available = destMethods.length ? destMethods : ["sftp"];
    const chosen = chooseMethodByPreference(
      available,
      rule?.transfer_method_preference,
      destination?.transfer_method_preference,
      destination?.detected_preferred_method
    );
    return chosen;
  }

  if (sourceKind === "ssh" && (destinationKind === "remote" || destinationKind === "sftp")) {
    return "unsupported";
  }

  return "auto";
}

export function renderIgnoredLabels() {
  if (!els.ignoredLabelsList) {
    return;
  }
  const isEmpty = !state.ignoredLabels.length;
  if (els.ignoredLabelsEmptyHint) {
    els.ignoredLabelsEmptyHint.classList.toggle("hidden", !isEmpty);
  }
  if (isEmpty) {
    els.ignoredLabelsList.innerHTML = "";
    return;
  }
  els.ignoredLabelsList.innerHTML = state.ignoredLabels
    .map((label) => {
      const safe = escapeHtml(label);
      return `<span class="label-chip"><span class="label-chip-text">${safe}</span><button type="button" class="chip-remove" data-remove-ignored="${safe}" aria-label="Remove ${safe}" title="Remove ${safe}">&times;</button></span>`;
    })
    .join("");
}

export function updateRuleDestinationOptions() {
  if (!els.ruleDestinationSelect) {
    return;
  }
  const selected = els.ruleDestinationSelect.value;
  if (!state.destinations.length) {
    els.ruleDestinationSelect.innerHTML = '<option value="">-- no destinations --</option>';
    return;
  }
  els.ruleDestinationSelect.innerHTML = state.destinations
    .map((d) => `<option value="${d.id}">${escapeHtml(d.name)}</option>`)
    .join("");
  if (selected && state.destinations.some((d) => String(d.id) === selected)) {
    els.ruleDestinationSelect.value = selected;
  }
}

export function updateRuleLabelOptions(preferred = "") {
  if (!els.ruleLabelSelect) {
    return;
  }
  // Preserve the user's in-progress selection across periodic rebuilds (e.g. label polling).
  const currentValue = els.ruleLabelSelect.value;
  const labels = new Set();
  for (const label of state.availableLabels || []) {
    if (label) {
      labels.add(String(label));
    }
  }
  for (const rule of state.rules || []) {
    if (rule?.label) {
      labels.add(String(rule.label));
    }
  }
  const sorted = Array.from(labels).sort((a, b) => a.localeCompare(b));
  // If the resulting option set is identical to what's already rendered, skip the
  // innerHTML rewrite entirely so the <select> doesn't briefly drop focus/value.
  const existingValues = Array.from(els.ruleLabelSelect.options)
    .map((opt) => opt.value)
    .filter((v) => v !== "");
  const sameSet =
    existingValues.length === sorted.length &&
    existingValues.every((v, i) => v === sorted[i]);
  if (!sameSet) {
    const options = ['<option value="">-- select label --</option>'];
    options.push(...sorted.map((label) => `<option value="${escapeHtml(label)}">${escapeHtml(label)}</option>`));
    els.ruleLabelSelect.innerHTML = options.join("");
  }
  if (preferred && sorted.includes(preferred)) {
    els.ruleLabelSelect.value = preferred;
  } else if (currentValue && sorted.includes(currentValue)) {
    els.ruleLabelSelect.value = currentValue;
  }
}

export function renderDestinations() {
  if (!els.destinationsTable) {
    return;
  }
  if (els.destinationsCount) {
    els.destinationsCount.textContent = state.destinations.length ? String(state.destinations.length) : "";
  }
  if (!state.destinations.length) {
    els.destinationsTable.innerHTML = '<tr><td colspan="4">No destinations created yet.</td></tr>';
    return;
  }
  els.destinationsTable.innerHTML = state.destinations
    .map((dest) => {
      const isRemote = dest.kind === "remote" || dest.kind === "sftp";
      const target = isRemote
        ? `${dest.username || "user"}@${dest.host || "host"}:${dest.base_path || ""}`
        : dest.base_path || "";
      const kindLabel = isRemote ? "Remote" : "Local";
      return `
        <tr>
          <td>${escapeHtml(dest.name)}</td>
          <td>${escapeHtml(kindLabel)}</td>
          <td>${escapeHtml(target)}</td>
          <td>
            <button type="button" class="secondary" data-edit-destination="${dest.id}">Edit</button>
            <button type="button" class="secondary" data-delete-destination="${dest.id}">Delete</button>
          </td>
        </tr>
      `;
    })
    .join("");
}

export function renderRules() {
  if (!els.rulesTable) {
    return;
  }
  if (els.rulesCount) {
    els.rulesCount.textContent = state.rules.length ? String(state.rules.length) : "";
  }
  if (!state.rules.length) {
    els.rulesTable.innerHTML = '<tr><td colspan="6">No rules created yet.</td></tr>';
    updateRuleLabelOptions();
    return;
  }
  els.rulesTable.innerHTML = state.rules
    .map((rule) => {
      const destination = state.destinations.find((d) => Number(d.id) === Number(rule.destination_id));
      const mode = escapeHtml((rule.transfer_mode || "move").toUpperCase());
      const parallelismMode = escapeHtml((rule.parallelism_mode || "sequential").toUpperCase());
      const conflictPolicy = escapeHtml((rule.conflict_policy || "overwrite").toUpperCase());
      const effectiveMethod = escapeHtml(computeRuleEffectiveMethod(rule, destination));
      const removeFromClient = rule.remove_from_client ? "Yes" : "No";
      const trashData = rule.remove_from_client && rule.trash_data_on_remove ? "Yes" : "No";
      return `
      <tr>
        <td>${escapeHtml(rule.label)}</td>
        <td>${escapeHtml(rule.destination_name || "Unknown")}</td>
        <td>${mode}<br /><span class="muted">Execution: ${parallelismMode}</span><br /><span class="muted">Method: ${effectiveMethod}</span><br /><span class="muted">Conflict: ${conflictPolicy}</span><br /><span class="muted">Remove from client: ${escapeHtml(removeFromClient)}</span><br /><span class="muted">Trash data: ${escapeHtml(trashData)}</span></td>
        <td>${escapeHtml((rule.transfer_schedule || "auto").toUpperCase())}${rule.transfer_schedule === "interval" ? ` (${Number(rule.transfer_interval_seconds || 300)}s)` : ""}</td>
        <td>${rule.enabled ? "Yes" : "No"}</td>
        <td>
          <button type="button" class="secondary" data-edit-rule="${rule.id}">Edit</button>
          <button type="button" class="secondary" data-delete-rule="${rule.id}">Delete</button>
        </td>
      </tr>
    `;
    })
    .join("");
  updateRuleLabelOptions();
}

export function formatTorrentStatus(status) {
  const map = {
    0: "Stopped",
    1: "Check queued",
    2: "Checking",
    3: "Download queued",
    4: "Downloading",
    5: "Seed queued",
    6: "Seeding",
  };
  return map[Number(status)] || `Status ${status}`;
}

export function renderTorrents() {
  if (!els.torrentsTable) {
    return;
  }
  if (els.labelManagementCount) {
    els.labelManagementCount.textContent = state.torrents.length ? String(state.torrents.length) : "";
  }
  if (!state.torrents.length) {
    els.torrentsTable.innerHTML = '<tr><td colspan="5">No torrents found.</td></tr>';
    return;
  }
  els.torrentsTable.innerHTML = state.torrents
    .map((torrent) => {
      const labels = Array.isArray(torrent.labels) ? torrent.labels : [];
      const labelsHtml = labels.length
        ? `<div class="label-chips">${labels
            .map(
              (label) =>
                `<span class="label-chip">${escapeHtml(label)}<button type="button" class="chip-remove" data-remove-label-torrent-id="${torrent.id}" data-remove-label="${encodeURIComponent(label)}" aria-label="Remove label ${escapeHtml(label)}" title="Remove label ${escapeHtml(label)}">×</button></span>`
            )
            .join("")}</div>`
        : "-";
      const pct = Math.round((Number(torrent.percent_done || 0) * 100) * 10) / 10;
      return `
        <tr>
          <td>${escapeHtml(torrent.name)}</td>
          <td>${escapeHtml(formatTorrentStatus(torrent.status))}</td>
          <td>${pct.toFixed(1)}%</td>
          <td>${labelsHtml}</td>
          <td>
            <input type="text" data-label-input="${torrent.id}" list="knownLabels" placeholder="label" />
            <button type="button" class="secondary" data-assign-label="${torrent.id}">Assign</button>
          </td>
        </tr>
      `;
    })
    .join("");
}

export function renderOverview() {
  if (!els.overviewTable) {
    return;
  }
  const ignored = new Set((state.ignoredLabels || []).map((x) => String(x).toLowerCase()));
  const rulesByLabel = new Map((state.rules || []).map((r) => [r.label, r]));
  const destinationsById = new Map((state.destinations || []).map((d) => [Number(d.id), d]));
  const torrents = (state.torrents || []).filter((t) => {
    const labels = Array.isArray(t.labels) ? t.labels : [];
    return !labels.some((l) => ignored.has(String(l).toLowerCase()));
  });

  if (!torrents.length) {
    els.overviewTable.innerHTML = '<tr><td colspan="6">No torrents to display.</td></tr>';
    if (els.overviewChip) {
      els.overviewChip.textContent = "0 visible";
    }
    if (els.overviewCount) {
      els.overviewCount.textContent = "";
    }
    return;
  }

  els.overviewTable.innerHTML = torrents
    .map((torrent) => {
      const labels = Array.isArray(torrent.labels) ? torrent.labels : [];
      const matchedLabel = labels.find((label) => rulesByLabel.has(label)) || "";
      const rule = rulesByLabel.get(matchedLabel);
      const moveTarget = rule?.destination_name || "No matching rule";
      const ruleMode = String(rule?.transfer_mode || "move").toLowerCase();
      const pct = Math.round((Number(torrent.percent_done || 0) * 100) * 10) / 10;
      const isComplete = Number(torrent.percent_done || 0) >= 1;
      const active = (state.activeTransfers || []).find((a) => Number(a.torrent_id) === Number(torrent.id));
      const destination = rule ? destinationsById.get(Number(rule.destination_id)) : null;
      const computedMethod = rule ? computeRuleEffectiveMethod(rule, destination) : "-";
      const methodText = String(active?.method || computedMethod || "").trim();
      const moveStatus = active
        ? `Active (${Number(active.percent || 0).toFixed(1)}%) [${String(active.mode || "move").toUpperCase()}${methodText ? ` | ${methodText.toUpperCase()}` : ""}]`
        : `Idle (${ruleMode}${methodText ? ` | ${methodText.toUpperCase()}` : ""})`;

      return `
        <tr>
          <td>${escapeHtml(torrent.name)}</td>
          <td>${pct.toFixed(1)}%</td>
          <td>${escapeHtml(labels.join(", ") || "-")}</td>
          <td>${escapeHtml(moveTarget)}</td>
          <td>${escapeHtml(moveStatus)}</td>
          <td><button type="button" class="secondary" data-transfer-now="${torrent.id}" ${isComplete ? "" : "disabled"} title="${isComplete ? "Transfer completed torrent now" : "Torrent is still downloading"}">Transfer now</button></td>
        </tr>
      `;
    })
    .join("");

  if (els.overviewChip) {
    els.overviewChip.textContent = `${torrents.length} visible`;
  }
  if (els.overviewCount) {
    els.overviewCount.textContent = String(torrents.length);
  }
}

export function renderLogs(logs) {
  if (!els.logsTable) {
    return;
  }

  const activeRows = (state.activeTransfers || []).map((item) => {
    const pct = Number(item.percent || 0);
    const status = `Active (${pct.toFixed(1)}%)`;
    const speed = formatBytes(item.speed_bytes_per_sec || 0);
    const moved = formatBytes(item.transferred_bytes || 0);
    const total = formatBytes(item.total_bytes || 0);
    const method = String(item.method || "").trim();
    const mode = String(item.mode || "transfer").trim();
    const detail = `${moved} / ${total} at ${speed}/s (${mode}${method ? ` via ${method}` : ""})`;
    return {
      created_at: new Date().toISOString(),
      torrent_name: item.torrent_name || "<unknown>",
      label: "-",
      destination_name: item.destination_name || "-",
      status,
      message: detail,
    };
  });

  const recentRows = Array.isArray(logs) ? logs : [];
  const rows = [...activeRows, ...recentRows];

  if (els.activityLogCount) {
    els.activityLogCount.textContent = rows.length ? String(rows.length) : "";
  }

  if (rows.length === 0) {
    els.logsTable.innerHTML = '<tr><td colspan="6">No activity yet.</td></tr>';
    return;
  }

  els.logsTable.innerHTML = "";
  for (const log of rows) {
    const tr = document.createElement("tr");

    const timeTd = document.createElement("td");
    timeTd.textContent = formatUtcDateTime(log.created_at);
    tr.appendChild(timeTd);

    const torrentTd = document.createElement("td");
    torrentTd.textContent = log.torrent_name || "-";
    tr.appendChild(torrentTd);

    const labelTd = document.createElement("td");
    labelTd.textContent = log.label || "-";
    tr.appendChild(labelTd);

    const destTd = document.createElement("td");
    destTd.textContent = log.destination_name || "-";
    tr.appendChild(destTd);

    const statusTd = document.createElement("td");
    statusTd.textContent = log.status || "-";
    tr.appendChild(statusTd);

    const detailTd = document.createElement("td");
    const wrap = document.createElement("div");
    wrap.style.display = "flex";
    wrap.style.alignItems = "center";
    wrap.style.gap = "8px";

    const detailText = log.message || "-";
    const span = document.createElement("span");
    span.textContent = detailText;
    wrap.appendChild(span);

    const copyBtn = document.createElement("button");
    copyBtn.type = "button";
    copyBtn.className = "secondary copy-icon-btn";
    copyBtn.setAttribute("aria-label", "Copy log detail");
    copyBtn.title = "Copy log detail";
    copyBtn.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M9 9h10v10H9z" fill="none" stroke="currentColor" stroke-width="2"/><path d="M5 5h10v2H7v8H5z" fill="none" stroke="currentColor" stroke-width="2"/></svg>';
    copyBtn.addEventListener("click", () => copyToClipboard(span.textContent || detailText));
    wrap.appendChild(copyBtn);

    detailTd.appendChild(wrap);
    tr.appendChild(detailTd);
    els.logsTable.appendChild(tr);
  }
}
