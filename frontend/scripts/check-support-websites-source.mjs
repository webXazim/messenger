import fs from "node:fs";
const page = fs.readFileSync(new URL("../src/components/support/SupportWebsitesPage.tsx", import.meta.url), "utf8");
const inbox = fs.readFileSync(new URL("../src/components/support/SupportInbox.tsx", import.meta.url), "utf8");
const required = ["SupportWebsitesPage", "getWebsiteUsage", "Allowed origins", "Install script", "Widget preview", "SupportTabs"];
for (const token of required) if (!page.includes(token)) throw new Error(`Websites upgrade missing: ${token}`);
if (page.includes("SupportInbox")) throw new Error("Websites page must not couple to the Inbox component.");
if (!inbox.includes("export function SupportInbox")) throw new Error("Inbox baseline changed unexpectedly.");
console.log("Support Websites source checks passed.");
