import fs from "node:fs";
import path from "node:path";

const root = process.cwd();
const read = (file) => fs.readFileSync(path.join(root, file), "utf8");
const checks = [];

function expect(condition, message) {
  if (!condition) checks.push(message);
}

const app = read("src/App.tsx");
const authPage = read("src/pages/AuthRedirectPage.tsx");
const authApi = read("src/api/auth.ts");
const authContext = read("src/contexts/AuthContext.tsx");
const authRefresh = read("src/lib/authRefresh.ts");
const http = read("src/lib/http.ts");
const chatSocketHook = read("src/hooks/useChatSocket.ts");
const settings = read("src/pages/SettingsPage.tsx");

expect(app.includes('mode="reset-password"'), "Reset-password route is not wired to the confirmation screen.");
expect(authPage.includes("submitForgotPassword"), "Forgot-password request form is missing.");
expect(authPage.includes("submitPasswordReset"), "Password-reset confirmation form is missing.");
expect(authPage.includes('mode === "verify-email"'), "Email-verification confirmation screen is missing.");
expect(authPage.includes("Email or username"), "Login identifier is not labelled for both email and username.");
expect(!authPage.includes("require a configured mail service"), "Placeholder recovery copy is still present.");
expect(authApi.includes('update.profile = profile'), "Profile updates are not sent through the nested backend profile contract.");
expect(authApi.includes('payload.first_name ?? ""'), "Profile clearing is not preserved for first name.");
expect(authApi.includes('profileSource[key]'), "Profile clearing is not preserved for profile fields.");
expect(authContext.indexOf("clearTokens();") < authContext.indexOf("await Promise.race(["), "Local tokens are not cleared before remote logout cleanup completes.");
expect(http.includes('from "./authRefresh"'), "HTTP requests do not use the shared refresh-token coordinator.");
expect(chatSocketHook.includes('from "../lib/authRefresh"'), "Realtime does not use the shared refresh-token coordinator.");
expect(authRefresh.includes("getRefreshToken() === submittedRefresh"), "A stale refresh failure can erase newer rotated credentials.");
expect(settings.includes("deleteConfirmationMatches"), "Account deletion does not require explicit username confirmation.");
expect(settings.includes('profileFieldErrors["profile.bio"]'), "Nested profile validation is not displayed beside the correct field.");
expect(settings.includes("emailVerificationMessage"), "Email-verification feedback is not displayed in the account section.");

if (checks.length) {
  console.error("Authentication regression checks failed:\n- " + checks.join("\n- "));
  process.exit(1);
}

console.log("Authentication source regression checks passed.");
