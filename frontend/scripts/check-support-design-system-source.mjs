import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";

const root = new URL("..", import.meta.url).pathname;
const requiredFiles = [
  "src/support/styles/design-system.css",
  "src/support/components/SupportButton.tsx",
  "src/support/components/SupportField.tsx",
  "src/support/components/SupportSelect.tsx",
  "src/support/components/SupportToggle.tsx",
  "src/support/components/SupportBadge.tsx",
  "src/support/components/SupportTabs.tsx",
  "src/support/components/SupportPage.tsx",
  "src/support/components/SupportState.tsx",
  "src/support/components/SupportDataTable.tsx",
  "src/support/components/SupportModal.tsx",
  "src/support/components/index.ts",
];

const failures = [];
for (const file of requiredFiles) {
  if (!existsSync(join(root, file))) failures.push(`Missing ${file}`);
}

const styleIndex = readFileSync(join(root, "src/styles/index.css"), "utf8");
if (!styleIndex.includes('../support/styles/design-system.css')) failures.push("Support design-system CSS is not imported");

const designCss = readFileSync(join(root, "src/support/styles/design-system.css"), "utf8");
for (const token of ["--sc-bg", "--sc-border", "--sc-control-md", ".sc-page", ".sc-table", ".sc-modal"]) {
  if (!designCss.includes(token)) failures.push(`Missing design-system token/component ${token}`);
}

const inbox = readFileSync(join(root, "src/components/support/SupportInbox.tsx"), "utf8");
if (inbox.includes("src/support/components") || inbox.includes("support/components")) failures.push("SupportInbox was coupled to the new design system during the locked-frame upgrade");

if (failures.length) {
  console.error(failures.join("\n"));
  process.exit(1);
}
console.log("Support design-system source checks passed.");
