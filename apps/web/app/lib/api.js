// Cliente mínimo del backend. Todo va a /api/* (proxy de Next hacia Django, mismo origen).

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function csrfToken() {
  const r = await fetch("/api/v1/auth/csrf", { cache: "no-store" });
  if (!r.ok) throw new ApiError(r.status, "No se pudo iniciar la sesión segura.");
  return (await r.json()).csrf_token;
}

function messageFrom(data, fallback) {
  if (data && typeof data === "object") {
    const first = data.detail ?? Object.values(data)[0];
    if (typeof first === "string") return first;
    if (Array.isArray(first) && typeof first[0] === "string") return first[0];
  }
  return fallback;
}

export async function api(path, { method = "GET", body } = {}) {
  const headers = {};
  if (method !== "GET") {
    // Django rota el token tras el login, así que se pide uno fresco en cada escritura.
    headers["X-CSRFToken"] = await csrfToken();
    headers["Content-Type"] = "application/json";
  }
  let res;
  try {
    res = await fetch(path, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "No se pudo conectar con el servidor.");
  }
  if (res.status === 204) return null;
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const fallback =
      res.status === 429 ? "Demasiados intentos. Espera un minuto." : `Error ${res.status}`;
    throw new ApiError(res.status, messageFrom(data, fallback));
  }
  return data;
}

const money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });

export function fmtMoney(value, currency = "USD") {
  if (value == null) return "—";
  const n = Number(value);
  return currency === "USD"
    ? money.format(n)
    : new Intl.NumberFormat("en-US", { style: "currency", currency }).format(n);
}

// Signo explícito + flecha: la ganancia/pérdida no depende solo del color.
export function fmtSigned(value, suffix = "") {
  if (value == null) return "—";
  const n = Number(value);
  const arrow = n > 0 ? "▲" : n < 0 ? "▼" : "•";
  const sign = n > 0 ? "+" : n < 0 ? "−" : "";
  return `${arrow} ${sign}${Math.abs(n).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}${suffix}`;
}

export function signClass(value) {
  const n = Number(value);
  return n > 0 ? "pos" : n < 0 ? "neg" : "";
}
