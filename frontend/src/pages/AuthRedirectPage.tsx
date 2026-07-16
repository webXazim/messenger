import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import type { CentralAuthMode } from "../lib/centralAuth";
import { useAuth } from "../contexts/AuthContext";
import { APP_NAME } from "../lib/config";
import { parseApiError, type ApiFieldErrors } from "../lib/apiErrors";
import { authApi } from "../api/auth";

type AuthPageMode = CentralAuthMode | "reset-password";

type AuthFormShellProps = {
  children: React.ReactNode;
  title: string;
  description: string;
  kicker: string;
};

function AuthFormShell({ children, title, description, kicker }: AuthFormShellProps) {
  return (
    <main id="main-content" className="auth-page auth-production-shell" tabIndex={-1}>
      <a className="ms-skip-link" href="#auth-form">Skip to sign-in form</a>
      <section className="auth-story" aria-label="Product introduction">
        <div className="auth-brand-mark" aria-hidden="true"><span>CS</span></div>
        <div>
          <p className="auth-eyebrow">Crescentsphere</p>
          <h1>Messages that feel<br />closer to home.</h1>
          <p className="auth-story-copy">Private conversations, clear calls, and the people who matter—together on every screen.</p>
        </div>
        <div className="auth-trust-row">
          <span>Private by design</span><span>Realtime</span><span>Cross-platform</span>
        </div>
      </section>

      <section id="auth-form" className="auth-form-panel" tabIndex={-1}>
        <div className="auth-card auth-production-card">
          <div className="auth-mobile-brand"><span className="auth-brand-dot">CS</span>{APP_NAME}</div>
          <div className="auth-heading">
            <p className="auth-kicker">{kicker}</p>
            <h2>{title}</h2>
            <p>{description}</p>
          </div>
          {children}
        </div>
      </section>
    </main>
  );
}

function FieldError({ id, message }: { id: string; message?: string }) {
  if (!message) return null;
  return <span id={id} className="auth-field-error" role="alert">{message}</span>;
}

export function AuthRedirectPage({ mode = "login" }: { mode?: AuthPageMode }) {
  const {
    login,
    register,
    confirmRegistrationCode,
    resendRegistrationCode,
    requestPasswordReset,
    confirmPasswordReset,
    confirmEmailVerification,
    isAuthenticated,
  } = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [identifier, setIdentifier] = useState("");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [verificationEmail, setVerificationEmail] = useState("");
  const [verificationCode, setVerificationCode] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [fieldErrors, setFieldErrors] = useState<ApiFieldErrors>({});
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [usernameAvailability, setUsernameAvailability] = useState<{
    status: "idle" | "checking" | "available" | "unavailable" | "error";
    username: string;
    message: string;
  }>({ status: "idle", username: "", message: "" });
  const verificationStarted = useRef(false);
  const isSignup = mode === "signup";
  const token = searchParams.get("token")?.trim() || "";
  const passwordScore = useMemo(() => [
    password.length >= 8,
    /[A-Z]/.test(password) && /[a-z]/.test(password),
    /\d/.test(password),
    /[^A-Za-z0-9]/.test(password),
  ].filter(Boolean).length, [password]);
  const normalizedUsername = username.trim();
  const usernameCheckIsCurrent = usernameAvailability.username.toLowerCase() === normalizedUsername.toLowerCase();

  useEffect(() => {
    if (!isSignup || !normalizedUsername) {
      setUsernameAvailability({ status: "idle", username: "", message: "" });
      return;
    }
    const controller = new AbortController();
    setUsernameAvailability({ status: "checking", username: normalizedUsername, message: "Checking availability…" });
    const timer = window.setTimeout(() => {
      void authApi.checkUsernameAvailability(normalizedUsername, controller.signal)
        .then((result) => {
          setUsernameAvailability({
            status: result.available ? "available" : "unavailable",
            username: normalizedUsername,
            message: result.detail || (result.available ? "Username is available." : "This username is unavailable."),
          });
        })
        .catch(() => {
          if (!controller.signal.aborted) {
            setUsernameAvailability({ status: "error", username: normalizedUsername, message: "Availability could not be checked. It will be verified when you submit." });
          }
        });
    }, 400);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [isSignup, normalizedUsername]);

  useEffect(() => {
    if (mode !== "verify-email" || verificationStarted.current) return;
    verificationStarted.current = true;
    if (!token) {
      setError("This verification link is incomplete. Request a new email from Settings.");
      return;
    }

    setBusy(true);
    void confirmEmailVerification(token)
      .then((payload) => setMessage(payload.detail || "Email verified."))
      .catch((reason) => setError(parseApiError(reason, "This verification link is invalid or has expired.").message))
      .finally(() => setBusy(false));
  }, [confirmEmailVerification, mode, token]);

  function resetFeedback() {
    setError("");
    setFieldErrors({});
    setMessage("");
  }

  async function submitCredentials(event: FormEvent) {
    event.preventDefault();
    resetFeedback();
    setBusy(true);
    try {
      if (isSignup) {
        if (usernameCheckIsCurrent && usernameAvailability.status === "unavailable") {
          setFieldErrors({ username: usernameAvailability.message });
          return;
        }
        if (password !== confirmPassword) {
          setFieldErrors({ password_confirm: "Passwords do not match." });
          return;
        }
        const result = await register({ username: username.trim(), email: email.trim(), password, password_confirm: password });
        if (result.emailVerificationRequired) {
          setVerificationEmail(email.trim());
          setMessage("We sent a six-digit verification code to your email.");
          return;
        }
      } else {
        await login({ username: identifier.trim(), password });
      }
      navigate("/chat", { replace: true });
    } catch (reason) {
      const parsed = parseApiError(reason, isSignup ? "Unable to create the account." : "Unable to sign in with those details.");
      if (!isSignup && parsed.message.toLowerCase().includes("email verification") && identifier.includes("@")) {
        setVerificationEmail(identifier.trim());
        await resendRegistrationCode(identifier.trim()).catch(() => undefined);
        setMessage("We sent a new six-digit verification code to your email.");
        return;
      }
      setError(parsed.message);
      setFieldErrors(parsed.fields);
    } finally {
      setBusy(false);
    }
  }

  async function submitVerificationCode(event: FormEvent) {
    event.preventDefault();
    resetFeedback();
    setBusy(true);
    try {
      await confirmRegistrationCode(verificationEmail, verificationCode);
      await login({ username: username.trim() || identifier.trim() || verificationEmail, password });
      navigate("/chat", { replace: true });
    } catch (reason) {
      setError(parseApiError(reason, "That code is invalid or has expired.").message);
    } finally {
      setBusy(false);
    }
  }

  async function resendVerificationCode() {
    resetFeedback();
    setBusy(true);
    try {
      const payload = await resendRegistrationCode(verificationEmail);
      setMessage(payload.detail || "A new verification code has been sent.");
    } catch (reason) {
      setError(parseApiError(reason, "Unable to resend the code right now.").message);
    } finally {
      setBusy(false);
    }
  }

  async function submitForgotPassword(event: FormEvent) {
    event.preventDefault();
    resetFeedback();
    setBusy(true);
    try {
      const payload = await requestPasswordReset(email.trim());
      setMessage(payload.detail || "Check your email for a password reset link.");
    } catch (reason) {
      const parsed = parseApiError(reason, "Unable to request a password reset right now.");
      setError(parsed.message);
      setFieldErrors(parsed.fields);
    } finally {
      setBusy(false);
    }
  }

  async function submitPasswordReset(event: FormEvent) {
    event.preventDefault();
    resetFeedback();
    if (!token) {
      setError("This password reset link is incomplete. Request a new link.");
      return;
    }
    if (password !== confirmPassword) {
      setFieldErrors({ new_password_confirm: "Passwords do not match." });
      return;
    }

    setBusy(true);
    try {
      const payload = await confirmPasswordReset(token, password);
      setMessage(payload.detail || "Password updated. You can now sign in.");
      setPassword("");
      setConfirmPassword("");
    } catch (reason) {
      const parsed = parseApiError(reason, "This password reset link is invalid or has expired.");
      setError(parsed.message);
      setFieldErrors(parsed.fields);
    } finally {
      setBusy(false);
    }
  }

  if (mode === "verify-email") {
    return (
      <AuthFormShell kicker="Account security" title={message ? "Email verified" : error ? "Verification unavailable" : "Verifying your email"} description={message ? "Your account email is now confirmed." : error ? "The link could not be completed." : "Please keep this page open for a moment."}>
        {busy ? <div className="auth-status" role="status"><span className="auth-spinner auth-spinner--dark" />Verifying your email…</div> : null}
        {error ? <div className="auth-alert" role="alert"><strong>We couldn't verify this email</strong><span>{error}</span></div> : null}
        {message ? <div className="auth-success" role="status"><strong>Verification complete</strong><span>{message}</span></div> : null}
        <Link className="auth-submit auth-submit--link" to={isAuthenticated ? "/settings" : "/login"}>{isAuthenticated ? "Return to settings" : "Continue to sign in"}</Link>
      </AuthFormShell>
    );
  }

  if (verificationEmail) {
    return (
      <AuthFormShell kicker="Verify your email" title="Enter your six-digit code" description={`We sent a verification code to ${verificationEmail}. It expires in 10 minutes.`}>
        <form className="auth-inline-form" onSubmit={submitVerificationCode} noValidate>
          {error ? <div className="auth-alert" role="alert"><strong>We couldn't verify the code</strong><span>{error}</span></div> : null}
          {message ? <div className="auth-success" role="status"><strong>Check your inbox</strong><span>{message}</span></div> : null}
          <label>
            <span>Verification code</span>
            <input
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="[0-9]{6}"
              maxLength={6}
              value={verificationCode}
              onChange={(event) => setVerificationCode(event.target.value.replace(/\D/g, "").slice(0, 6))}
              placeholder="000000"
              aria-label="Six-digit verification code"
              autoFocus
              required
            />
          </label>
          <button className="auth-submit" type="submit" disabled={busy || verificationCode.length !== 6}>{busy ? <><span className="auth-spinner" />Verifying…</> : "Verify and continue"}</button>
          <p className="auth-switch">Didn't receive it? <button type="button" className="auth-link-button" onClick={() => void resendVerificationCode()} disabled={busy}>Send a new code</button></p>
          <p className="auth-switch"><button type="button" className="auth-link-button" onClick={() => { setVerificationEmail(""); resetFeedback(); }}>Change email</button></p>
        </form>
      </AuthFormShell>
    );
  }

  if (mode === "forgot-password") {
    return (
      <AuthFormShell kicker="Account recovery" title="Reset your password" description="Enter the email address connected to your account. We will send a secure reset link when the account exists.">
        <form className="auth-inline-form" onSubmit={submitForgotPassword} noValidate>
          {error ? <div className="auth-alert" role="alert"><strong>We couldn't send the email</strong><span>{error}</span></div> : null}
          {message ? <div className="auth-success" role="status"><strong>Check your inbox</strong><span>{message}</span></div> : null}
          <label>
            <span>Email address</span>
            <input
              type="email"
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              aria-invalid={Boolean(fieldErrors.email)}
              aria-describedby={fieldErrors.email ? "forgot-email-error" : undefined}
              placeholder="you@example.com"
              required
            />
            <FieldError id="forgot-email-error" message={fieldErrors.email} />
          </label>
          <button className="auth-submit" type="submit" disabled={busy || !email.trim()}>{busy ? <><span className="auth-spinner" />Sending reset link…</> : "Send reset link"}</button>
          <p className="auth-switch"><Link to="/login">Back to sign in</Link></p>
        </form>
      </AuthFormShell>
    );
  }

  if (mode === "reset-password") {
    return (
      <AuthFormShell kicker="Account recovery" title="Choose a new password" description="Use a strong password you do not use for another account.">
        <form className="auth-inline-form" onSubmit={submitPasswordReset} noValidate>
          {error ? <div className="auth-alert" role="alert"><strong>We couldn't reset the password</strong><span>{error}</span></div> : null}
          {message ? <div className="auth-success" role="status"><strong>Password updated</strong><span>{message}</span></div> : null}
          {!message ? <>
            <label>
              <span>New password</span>
              <div className="auth-password-wrap">
                <input
                  type={showPassword ? "text" : "password"}
                  autoComplete="new-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  aria-invalid={Boolean(fieldErrors.new_password)}
                  aria-describedby={fieldErrors.new_password ? "reset-password-error" : undefined}
                  placeholder="At least 8 characters"
                  minLength={8}
                  required
                />
                <button className="auth-show-password" type="button" onClick={() => setShowPassword((value) => !value)}>{showPassword ? "Hide" : "Show"}</button>
              </div>
              <FieldError id="reset-password-error" message={fieldErrors.new_password} />
            </label>
            <div className="auth-strength" aria-label={`Password strength ${passwordScore} of 4`}><i className={passwordScore > 0 ? "active" : ""}/><i className={passwordScore > 1 ? "active" : ""}/><i className={passwordScore > 2 ? "active" : ""}/><i className={passwordScore > 3 ? "active" : ""}/><span>{password ? ["Weak", "Fair", "Good", "Strong"][Math.max(0, passwordScore - 1)] : "Use letters, a number, and a symbol"}</span></div>
            <label>
              <span>Confirm new password</span>
              <input
                type="password"
                autoComplete="new-password"
                value={confirmPassword}
                onChange={(event) => setConfirmPassword(event.target.value)}
                aria-invalid={Boolean(fieldErrors.new_password_confirm)}
                aria-describedby={fieldErrors.new_password_confirm ? "reset-confirm-error" : undefined}
                placeholder="Repeat your new password"
                required
              />
              <FieldError id="reset-confirm-error" message={fieldErrors.new_password_confirm} />
            </label>
            <button className="auth-submit" type="submit" disabled={busy || !password || !confirmPassword}>{busy ? <><span className="auth-spinner" />Updating password…</> : "Update password"}</button>
          </> : null}
          <p className="auth-switch"><Link to="/login">Continue to sign in</Link></p>
        </form>
      </AuthFormShell>
    );
  }

  return (
    <AuthFormShell kicker={isSignup ? "Get started" : "Welcome back"} title={isSignup ? "Create your account" : "Sign in to Crescentsphere"} description={isSignup ? "One account for private conversations on all your devices." : "Continue your conversations securely."}>
      <form className="auth-inline-form" onSubmit={submitCredentials} noValidate>
        {error ? <div className="auth-alert" role="alert"><strong>We couldn't continue</strong><span>{error}</span></div> : null}

        <div className="auth-fields">
          {isSignup ? <>
            <label>
              <span>Username</span>
              <input autoComplete="username" placeholder="Choose a username" value={username} onChange={(event) => setUsername(event.target.value)} aria-invalid={Boolean(fieldErrors.username) || (usernameCheckIsCurrent && usernameAvailability.status === "unavailable")} aria-describedby={[fieldErrors.username ? "signup-username-error" : "", normalizedUsername ? "signup-username-status" : ""].filter(Boolean).join(" ") || undefined} required />
              <FieldError id="signup-username-error" message={fieldErrors.username} />
              {normalizedUsername ? <span id="signup-username-status" className={`auth-username-status is-${usernameCheckIsCurrent ? usernameAvailability.status : "checking"}`} role="status" aria-live="polite">
                {(usernameCheckIsCurrent ? usernameAvailability.status : "checking") === "checking" ? <span className="auth-spinner auth-spinner--dark" aria-hidden="true" /> : null}
                {usernameCheckIsCurrent ? usernameAvailability.message : "Checking availability…"}
              </span> : null}
            </label>
            <label>
              <span>Email address</span>
              <input type="email" autoComplete="email" placeholder="you@example.com" value={email} onChange={(event) => setEmail(event.target.value)} aria-invalid={Boolean(fieldErrors.email)} aria-describedby={fieldErrors.email ? "signup-email-error" : undefined} required />
              <FieldError id="signup-email-error" message={fieldErrors.email} />
            </label>
          </> : <label>
            <span>Email or username</span>
            <input autoComplete="username" placeholder="Email address or username" value={identifier} onChange={(event) => setIdentifier(event.target.value)} aria-invalid={Boolean(fieldErrors.username)} aria-describedby={fieldErrors.username ? "login-identifier-error" : undefined} required />
            <FieldError id="login-identifier-error" message={fieldErrors.username} />
          </label>}

          <label>
            <span>Password</span>
            <div className="auth-password-wrap">
              <input type={showPassword ? "text" : "password"} autoComplete={isSignup ? "new-password" : "current-password"} placeholder={isSignup ? "At least 8 characters" : "Enter your password"} value={password} onChange={(event) => setPassword(event.target.value)} aria-invalid={Boolean(fieldErrors.password)} aria-describedby={fieldErrors.password ? "auth-password-error" : undefined} required minLength={8} />
              <button className="auth-show-password" type="button" onClick={() => setShowPassword((value) => !value)} aria-label={showPassword ? "Hide password" : "Show password"}>{showPassword ? "Hide" : "Show"}</button>
            </div>
            <FieldError id="auth-password-error" message={fieldErrors.password} />
          </label>

          {isSignup ? <>
            <div className="auth-strength" aria-label={`Password strength ${passwordScore} of 4`}><i className={passwordScore > 0 ? "active" : ""}/><i className={passwordScore > 1 ? "active" : ""}/><i className={passwordScore > 2 ? "active" : ""}/><i className={passwordScore > 3 ? "active" : ""}/><span>{password ? ["Weak", "Fair", "Good", "Strong"][Math.max(0, passwordScore - 1)] : "Use letters, a number, and a symbol"}</span></div>
            <label>
              <span>Confirm password</span>
              <input type="password" autoComplete="new-password" placeholder="Repeat your password" value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} aria-invalid={Boolean(fieldErrors.password_confirm)} aria-describedby={fieldErrors.password_confirm ? "signup-confirm-error" : undefined} required />
              <FieldError id="signup-confirm-error" message={fieldErrors.password_confirm} />
            </label>
          </> : null}
        </div>

        {!isSignup ? <div className="auth-form-meta"><span>Protected session</span><Link to="/forgot-password">Forgot password?</Link></div> : null}
        <button className="auth-submit" type="submit" disabled={busy || (isSignup ? !username.trim() || !email.trim() || (usernameCheckIsCurrent && ["checking", "unavailable"].includes(usernameAvailability.status)) : !identifier.trim()) || !password}>{busy ? <><span className="auth-spinner" />Please wait…</> : isSignup ? "Create account" : "Sign in"}</button>
        <p className="auth-switch">{isSignup ? "Already have an account?" : "New to Crescentsphere?"} <Link to={isSignup ? "/login" : "/register"}>{isSignup ? "Sign in" : "Create an account"}</Link></p>
        <p className="auth-legal">By continuing, you agree to responsible use and acknowledge our privacy practices.</p>
      </form>
    </AuthFormShell>
  );
}
