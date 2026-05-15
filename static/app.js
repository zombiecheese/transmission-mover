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
setInterval(() => {
  refreshActivity().catch(() => {
    // Keep UI responsive if active transfer telemetry is temporarily unavailable.
  });
}, 2000);
