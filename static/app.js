import { refreshActivity, refreshAll, initEventHandlers } from "./actions.js";
import { state } from "./state.js";
import { initSettingsMenu, initThemeControls, toggleRuleTransferIntervalField, updateTestGatedButtons } from "./shared.js";
import { showMessage } from "./utils.js";

initSettingsMenu();
initThemeControls();
initEventHandlers();
updateTestGatedButtons();
toggleRuleTransferIntervalField();
refreshAll().catch((err) => showMessage(err.message, true));

const ACTIVE_TRANSFER_POLL_MS = 2000;
const IDLE_POLL_MS = 10000;
const HIDDEN_POLL_MS = 30000;
let pollTimer = null;

function scheduleActivityPoll() {
  if (pollTimer !== null) {
    clearTimeout(pollTimer);
  }

  const hasActiveTransfers = Boolean(state.activeTransfers && state.activeTransfers.length > 0);
  const interval = document.hidden
    ? HIDDEN_POLL_MS
    : hasActiveTransfers
      ? ACTIVE_TRANSFER_POLL_MS
      : IDLE_POLL_MS;
  pollTimer = setTimeout(async () => {
    try {
      await refreshActivity();
    } catch {
      // Keep UI responsive if active transfer telemetry is temporarily unavailable.
    } finally {
      scheduleActivityPoll();
    }
  }, interval);
}

document.addEventListener("visibilitychange", scheduleActivityPoll);
scheduleActivityPoll();
