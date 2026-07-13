const CENTRAL_AUTH_ORIGIN = import.meta.env.VITE_CENTRAL_AUTH_ORIGIN?.trim() || "https://accounts.crescentsphere.com";
export type CentralAuthMode = "login" | "signup" | "forgot-password" | "reset-password" | "verify-email" | "account";

function callbackUrl() {
  return `${window.location.origin}/auth/callback`;
}

export function centralAuthUrl(mode: CentralAuthMode = "login") {
  const path = {
    login: "/login/",
    signup: "/signup/",
    "forgot-password": "/forgot-password/",
    "reset-password": "/reset-password/",
    "verify-email": "/verify-email/",
    account: "/account/",
  }[mode];
  const url = new URL(path, CENTRAL_AUTH_ORIGIN);
  if (mode !== "account") {
    url.searchParams.set("next", callbackUrl());
  }
  return url.toString();
}

export function redirectToCentralAuth(mode: CentralAuthMode = "login") {
  window.location.assign(centralAuthUrl(mode));
}
