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

// "7.5" -> "0.075" sin pasar por números de punto flotante (evita 0.07099999…).
export function pctToDecimal(text) {
  const t = String(text ?? "").trim().replace(",", ".");
  if (!/^-?\d+(\.\d+)?$/.test(t)) return t; // que el backend valide lo que no sea número
  const neg = t.startsWith("-");
  const [int, frac = ""] = t.replace("-", "").split(".");
  const decimals = frac.length + 2; // dividir entre 100 desplaza el punto dos lugares
  const padded = (int + frac).padStart(decimals + 1, "0");
  const cut = padded.length - decimals;
  return `${neg ? "-" : ""}${padded.slice(0, cut)}.${padded.slice(cut)}`;
}

// Descarga una respuesta no JSON (p. ej. CSV) de un POST protegido con CSRF.
export async function apiDownload(path, body) {
  const token = await csrfToken();
  let res;
  try {
    res = await fetch(path, {
      method: "POST",
      headers: { "X-CSRFToken": token, "Content-Type": "application/json" },
      body: JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "No se pudo conectar con el servidor.");
  }
  if (!res.ok) {
    const data = await res.json().catch(() => null);
    throw new ApiError(res.status, messageFrom(data, `Error ${res.status}`));
  }
  return res.blob();
}

// Sube un archivo (multipart) a un endpoint protegido con CSRF; devuelve el JSON de la respuesta.
export async function apiUpload(path, file, fields = {}) {
  const token = await csrfToken();
  const form = new FormData();
  form.append("file", file);
  for (const [name, value] of Object.entries(fields)) form.append(name, String(value));
  let res;
  try {
    res = await fetch(path, { method: "POST", headers: { "X-CSRFToken": token }, body: form, cache: "no-store" });
  } catch {
    throw new ApiError(0, "No se pudo conectar con el servidor.");
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const rows = Array.isArray(data?.errors) ? data.errors.slice(0, 5).map((e) => `fila ${e.row}: ${e.error}`).join("; ") : "";
    throw new ApiError(res.status, [messageFrom(data, `Error ${res.status}`), rows].filter(Boolean).join(" — "));
  }
  return data;
}

// Descarga un archivo con GET (p. ej. CSV de una corrida).
export async function apiDownloadGet(path) {
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) throw new ApiError(res.status, `Error ${res.status}`);
  return res.blob();
}
