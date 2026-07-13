import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { setAccessToken, setRefreshToken } from "../lib/tokenStore";
import { AUTH_API_BASE_URL } from "../lib/config";

interface TokenPair {
  access: string;
  refresh: string;
}

function readCallbackTokens() {
  const params = new URLSearchParams(window.location.hash.replace(/^#/, "") || window.location.search);
  return {
    access: params.get("access"),
    refresh: params.get("refresh"),
    code: params.get("code"),
  };
}

function callbackUrl() {
  return `${window.location.origin}/auth/callback`;
}

async function exchangeCode(code: string): Promise<TokenPair> {
  const response = await fetch(`${AUTH_API_BASE_URL}/auth/sso-code/exchange/`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ code, redirect_uri: callbackUrl() }),
  });

  if (!response.ok) {
    throw new Error("Central sign-in code could not be exchanged.");
  }

  const payload = await response.json();
  if (payload && typeof payload === "object" && "data" in payload) {
    return (payload as { data: TokenPair }).data;
  }
  return payload as TokenPair;
}

export function AuthCallbackPage() {
  const navigate = useNavigate();

  useEffect(() => {
    let cancelled = false;

    async function completeSignIn() {
      try {
        const { access, refresh, code } = readCallbackTokens();
        if (access && refresh) {
          setAccessToken(access);
          setRefreshToken(refresh);
        } else if (code) {
          const exchanged = await exchangeCode(code);
          setAccessToken(exchanged.access);
          setRefreshToken(exchanged.refresh);
        } else {
          throw new Error("Central sign-in did not return credentials.");
        }

        if (!cancelled) {
          window.history.replaceState({}, document.title, "/auth/callback");
          navigate("/chat", { replace: true });
        }
      } catch {
        if (!cancelled) {
          navigate("/login", { replace: true });
        }
      }
    }

    completeSignIn();
    return () => {
      cancelled = true;
    };
  }, [navigate]);

  return <main id="main-content" className="auth-page" tabIndex={-1}><div className="auth-card" role="status">Signing you in…</div></main>;
}
