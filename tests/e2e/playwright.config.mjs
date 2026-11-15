// Pruebas e2e: levantan un backend y un frontend aislados con una base temporal y datos FICTICIOS (seed_demo).
// Nunca tocan tu base real. Credenciales de prueba solo para esta base desechable.
import { defineConfig } from "@playwright/test";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "../..");
const backend = path.join(root, "apps/backend");
const web = path.join(root, "apps/web");
const py =
  process.env.E2E_PYTHON ||
  path.join(backend, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const db = path.join(os.tmpdir(), "asi-e2e.sqlite3");

export const DEMO_USER = "demo";
export const DEMO_PASS = "e2e-only-demo-pass";
export const STATE = path.join(here, ".auth-state.json");
const BACK = 8010;
const FRONT = 3010;

const backendEnv = {
  DJANGO_DEBUG: "true",
  SQLITE_PATH: db,
  DEMO_PASSWORD: DEMO_PASS,
  DJANGO_CSRF_TRUSTED_ORIGINS: `http://localhost:${FRONT}`,
};

export default defineConfig({
  testDir: "./specs",
  // El login tiene límite de 5 intentos/min (a propósito): se inicia sesión UNA vez y se reutiliza la sesión guardada.
  projects: [
    { name: "setup", testMatch: /setup\.mjs/ },
    {
      name: "app",
      testMatch: /(portfolio|quant|paper|liquidity)\.spec\.mjs/,
      dependencies: ["setup"],
      use: { storageState: STATE },
    },
    { name: "auth", testMatch: /auth\.spec\.mjs/, dependencies: ["app"] },
  ],
  timeout: 60_000,
  workers: 1, // comparten una sola base
  fullyParallel: false,
  reporter: [["list"]],
  use: { baseURL: `http://localhost:${FRONT}`, trace: "retain-on-failure", screenshot: "only-on-failure" },
  webServer: [
    {
      command: `"${py}" prepare_e2e.py "${db}"`,
      cwd: here,
      env: { ...backendEnv, E2E_BACKEND_DIR: backend },
      url: `http://127.0.0.1:${BACK}/api/v1/health`,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `npx next dev -p ${FRONT}`,
      cwd: web,
      env: { BACKEND_URL: `http://127.0.0.1:${BACK}` },
      url: `http://localhost:${FRONT}/login`,
      reuseExistingServer: false,
      timeout: 180_000,
    },
  ],
});
