import { useState } from "react";
import { Lock, LogIn, ShieldCheck, Loader2 } from "lucide-react";
import { login, setToken } from "../api";

export default function LoginScreen({ onSuccess }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    const result = await login(email.trim(), password);
    setBusy(false);
    if (result?.token) {
      setToken(result.token);
      onSuccess(result);
    } else {
      setError("Login failed — check credentials or that the stack is running.");
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

        <div className="login-card__hint">
          <Lock size={11} />
          Default: admin@cloudguardian.ai / admin123 (set ADMIN_EMAIL / ADMIN_PASSWORD in .env)
        </div>
      </form>
    </div>
  );
}
