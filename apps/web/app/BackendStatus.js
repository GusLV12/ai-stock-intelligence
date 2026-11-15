"use client";

import { useEffect, useState } from "react";

const LABELS = {
  loading: "Consultando…",
  ok: "Backend operativo",
  degraded: "Backend activo, base de datos con error",
  unreachable: "Backend no disponible",
};

export default function BackendStatus() {
  const [state, setState] = useState("loading");

  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/v1/health", { signal: controller.signal, cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d) => setState(d.status === "ok" ? "ok" : "degraded"))
      .catch((e) => {
        if (e.name !== "AbortError") setState("unreachable");
      });
    return () => controller.abort();
  }, []);

  return (
    <p role="status" aria-live="polite" data-state={state}>
      <strong>Estado:</strong> {LABELS[state]}
    </p>
  );
}
