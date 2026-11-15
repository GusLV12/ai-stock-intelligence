"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { api } from "../lib/api";

export default function LoginForm() {
  const router = useRouter();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function onSubmit(e) {
    e.preventDefault();
    setError("");
    setBusy(true);
    const form = new FormData(e.currentTarget);
    try {
      await api("/api/v1/auth/login", {
        method: "POST",
        body: { username: form.get("username"), password: form.get("password") },
      });
      router.replace("/");
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  return (
    <form className="card login" onSubmit={onSubmit} aria-labelledby="login-title">
      <h1 id="login-title">Iniciar sesión</h1>
      <label>
        Usuario
        <input name="username" autoComplete="username" required autoFocus />
      </label>
      <label>
        Contraseña
        <input name="password" type="password" autoComplete="current-password" required />
      </label>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <button type="submit" disabled={busy}>
        {busy ? "Entrando…" : "Entrar"}
      </button>
    </form>
  );
}
