"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { api, ApiError } from "../lib/api";

const EXAMPLES = ["¿Cuánto vale mi portafolio?", "¿Cuáles son mis 3 posiciones más grandes?", "¿Qué compré en los últimos 90 días?"];

export default function Asistente() {
  const router = useRouter();
  const [messages, setMessages] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const inputRef = useRef(null);

  async function send(text) {
    const message = text.trim();
    if (!message || busy) return;
    setError("");
    setBusy(true);
    const history = messages.map((m) => ({ role: m.role, content: m.content }));
    setMessages((prev) => [...prev, { role: "user", content: message }]);
    try {
      const data = await api("/api/v1/assistant/chat", { method: "POST", body: { message, history } });
      setMessages((prev) => [...prev, { role: "assistant", content: data.answer, meta: data }]);
    } catch (err) {
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
        router.replace("/login");
        return;
      }
      setError(err.message);
    } finally {
      setBusy(false);
      inputRef.current?.focus();
    }
  }

  function onSubmit(e) {
    e.preventDefault();
    const input = inputRef.current;
    const text = input.value;
    input.value = "";
    send(text);
  }

  return (
    <main className="wrap">
      <header className="top">
        <div>
          <h1>Asistente</h1>
          <p className="muted">Modelo local. Explica tus datos; los cálculos los hace el sistema, no el modelo.</p>
        </div>
        <nav className="actions" aria-label="Principal">
          <Link className="btn ghost" href="/">Portafolio</Link>
        </nav>
      </header>

      <p className="notice" role="note">
        No es asesoría de inversión. Revisa siempre la fuente y la fecha de los precios: no son tiempo real.
      </p>

      <section className="chat" aria-label="Conversación" aria-live="polite">
        {messages.length === 0 && (
          <div className="card empty">
            <h2>Pregunta sobre tu portafolio</h2>
            <div className="row-actions">
              {EXAMPLES.map((q) => (
                <button key={q} className="ghost" onClick={() => send(q)} disabled={busy}>{q}</button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m, i) => (
          <article key={i} className={`bubble ${m.role}`}>
            <span className="who">{m.role === "user" ? "Tú" : "Asistente"}</span>
            <p>{m.content}</p>
            {m.meta && (
              <>
                {m.meta.unverified_numbers.length > 0 && (
                  <p className="notice warn" role="alert">
                    Cifras no verificadas con tus datos: {m.meta.unverified_numbers.join(", ")}. No las tomes como válidas.
                  </p>
                )}
                <small className="src">
                  {m.meta.tool_calls.length > 0
                    ? `Herramientas: ${m.meta.tool_calls.map((c) => (c.ok ? c.name : `${c.name} (falló)`)).join(", ")}`
                    : "Sin consultar herramientas"}
                  {m.meta.sources.find((s) => s.providers) &&
                    ` · Precios de ${m.meta.sources.find((s) => s.providers).providers.join(", ")}` +
                      ` (el más antiguo, ${m.meta.sources.find((s) => s.providers).oldest_age_hours} h)`}
                  {` · ${m.meta.model}`}
                </small>
              </>
            )}
          </article>
        ))}
        {busy && <p role="status" className="muted">Pensando… (en CPU puede tardar un minuto)</p>}
        {error && <p role="alert" className="error">{error}</p>}
      </section>

      <form className="composer" onSubmit={onSubmit}>
        <label htmlFor="q" className="sr">Pregunta</label>
        <input id="q" ref={inputRef} maxLength={2000} placeholder="Escribe tu pregunta…" autoComplete="off" disabled={busy} />
        <button type="submit" disabled={busy}>Enviar</button>
      </form>
    </main>
  );
}
