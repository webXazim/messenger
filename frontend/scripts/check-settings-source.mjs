import fs from "node:fs";
import path from "node:path";

const root = path.resolve(process.cwd());
const read = (relative) => fs.readFileSync(path.join(root, relative), "utf8");
const settings = read("src/pages/SettingsPage.tsx");
const presentation = read("src/lib/settingsPresentation.ts");
const styles = read("src/styles/pages/settings.css");

function expect(condition, message) {
  if (!condition) throw new Error(message);
}

for (const heading of [
  "Your profile",
  "Account and password",
  "Active sessions",
  "Privacy and blocked users",
  "Messages and calls",
  "Call quality",
  "Secure devices",
  "Export or delete account",
]) {
  expect(settings.includes(heading), `Missing real-user settings section: ${heading}`);
}

for (const technicalCopy of [
  "Backend parity",
  "Capabilities and integrations",
  "applied_quality_profile",
  "Android Firebase devices",
  "Stored web token",
  "feature groups advertised",
]) {
  expect(!settings.includes(technicalCopy), `Developer-facing copy is still visible: ${technicalCopy}`);
}

expect(!settings.includes("<pre className=\"ms-settings-code\""), "Raw calling configuration is still rendered.");
expect(!settings.includes("{device.push_token}"), "Raw notification tokens are still rendered.");
expect(settings.includes("deleteConfirmationMatches"), "Account deletion no longer requires username confirmation.");
expect(settings.includes('kind: "delete-account"'), "Account deletion does not use the accessible confirmation dialog.");
expect(settings.includes('kind: "session"'), "Session revocation does not use a confirmation step.");
expect(settings.includes('kind: "secure-device"'), "Secure-device removal does not use a confirmation step.");
expect(settings.includes("beforeunload"), "Unsaved profile changes are not protected during browser navigation.");
expect(settings.includes("show_online_status"), "Online-status privacy is missing from Settings.");
expect(settings.includes("is_discoverable"), "Account discovery privacy is missing from Settings.");
expect(settings.includes("nearby_discovery_enabled"), "Nearby privacy is missing from Settings.");
expect(settings.includes("View security code"), "Security fingerprints are not kept behind an advanced disclosure.");
expect(presentation.includes("Microsoft Edge") && presentation.includes("Android phone"), "Sessions are not converted to readable device names.");
expect(presentation.includes("Automatic") && presentation.includes("Data saver") && presentation.includes("Best quality"), "Call quality choices still expose internal preset names.");
expect(styles.includes(".ms-settings-choice-grid"), "Call quality choices have no responsive settings styling.");
expect(styles.includes("@media (max-width: 620px)"), "Settings mobile layout regression guard is missing.");

console.log("Settings usability source regression checks passed.");
