import { refreshActivity, refreshAll, initEventHandlers } from "./actions.js";
import { initSettingsMenu, initThemeControls, toggleRuleTransferIntervalField, updateTestGatedButtons, checkRemoteToRemoteTransfer } from "./shared.js";
import { showMessage } from "./utils.js";

initSettingsMenu();
initThemeControls();
initEventHandlers();
updateTestGatedButtons();
toggleRuleTransferIntervalField();
checkRemoteToRemoteTransfer();
refreshAll().catch((err) => showMessage(err.message, true));

const ACTIVE_POLL_MS = 10000;
const HIDDEN_POLL_MS = 30000;
let pollTimer = null;

function scheduleActivityPoll() {
  if (pollTimer !== null) {
    clearTimeout(pollTimer);
  }

  const interval = document.hidden ? HIDDEN_POLL_MS : ACTIVE_POLL_MS;
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
