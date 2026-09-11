import { useEffect, useState } from "react";
import { Lock, LogIn, ShieldCheck, Loader2 } from "lucide-react";
import { login, setToken, setRefreshToken, ENDPOINTS } from "../api";

export default function LoginScreen({ onSuccess }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [oidcBusy, setOidcBusy] = useState(false);

  // Complete a GitHub SSO redirect. /auth/oidc/callback lands here with
  // ?oidc_token=&refresh_token= after the code has been verified server-side.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const oidcToken = params.get("oidc_token");
    if (!oidcToken) return;
    setToken(oidcToken);
    setRefreshToken(params.get("refresh_token"));
    // strip the credential out of the address bar so a later sign-out reload
    // can never auto-authenticate from a stale URL
    window.history.replaceState({}, "", window.location.pathname);
    onSuccess({ token: oidcToken });
  }, [onSuccess]);

  async function handleSubmit(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    const result = await login(email.trim(), password);
    setBusy(false);
    if (result?.token) {
      setToken(result.token);
      setRefreshToken(result.refresh_token);
      onSuccess(result);
    } else {
      setError("Login failed — check credentials or that the stack is running.");
    }
  }

  async function handleOidc() {
    setError("");
    setOidcBusy(true);
    const res = await fetch(`${ENDPOINTS.decisionEngine}/auth/oidc/login`).catch(
      () => null
    );
    if (res?.ok) {
      const data = await res.json();
      if (data.authorization_url) window.location.href = data.authorization_url;
      else {
        setError("OIDC not configured — see the README hardening section.");
        setOidcBusy(false);
      }
    } else {
      setError("OIDC is not reachable — check that the decision-engine is up.");
      setOidcBusy(false);
    }
  }

  return (
    <div className="login-screen">
      <form className="login-card" onSubmit={handleSubmit}>
        <div className="login-card__mark">
          <ShieldCheck size={22} strokeWidth={2.2} />
        </div>
        <h1 className="login-card__title">CloudGuardian AI</h1>
        <p className="login-card__subtitle">Autonomous Reliability Platform · Operator Access</p>

        <label className="login-field">
          <span className="login-field__label">EMAIL</span>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="admin@cloudguardian.ai"
            autoComplete="username"
            required
          />
        </label>

        <label className="login-field">
          <span className="login-field__label">PASSWORD</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
            autoComplete="current-password"
            required
          />
        </label>

        {error && <div className="login-card__error">{error}</div>}

        <button type="submit" className="login-btn" disabled={busy}>
          {busy ? <Loader2 size={14} className="spin" /> : <LogIn size={14} />}
          Authenticate
        </button>

        <div className="login-card__divider"><span>or</span></div>

        <button
          type="button"
          className="login-btn login-btn--oidc"
          onClick={handleOidc}
          disabled={oidcBusy}
        >
          {oidcBusy ? <Loader2 size={14} className="spin" /> : <ShieldCheck size={14} />}
          {oidcBusy ? "Connecting to GitHub…" : "Sign up / Sign in with GitHub"}
        </button>

        <div className="login-card__hint">
          <Lock size={11} />
          Credentials come from Vault (monitoring/vault/vault-secrets.env) — or use GitHub SSO above
        </div>
        <div className="login-card__hint">
          New here? GitHub SSO signs you in with your existing GitHub account — there is no separate
          sign-up form.
        </div>
      </form>
    </div>
  );
}
