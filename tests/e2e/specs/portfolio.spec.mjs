import { expect, test } from "@playwright/test";
import os from "node:os";
import path from "node:path";
import { login, watchErrors } from "./helpers.mjs";

test("el portafolio muestra las posiciones ficticias sin errores", async ({ page }, testInfo) => {
  const errors = watchErrors(page);
  await login(page);
  for (const t of ["AAA", "BBB", "CCC", "DDD"]) {
    await expect(page.getByRole("rowheader", { name: t, exact: true })).toBeVisible();
  }
  await expect(page.locator(".portfolio-stat").filter({ hasText: "Valor de mercado" })).toContainText("USD");
  await expect(page.locator(".portfolio-stat").filter({ hasText: "Ganancia no realizada" })).toContainText("USD");
  await expect(page.getByRole("row", { name: /AAA/ })).toContainText("USD");
  await page.screenshot({ path: testInfo.outputPath("portafolio-escritorio.png"), fullPage: true });
  expect(errors).toEqual([]);
});

test("la conversión de referencia identifica pesos mexicanos", async ({ page }) => {
  await page.route(/\/api\/v1\/portfolio\/dashboard(?:\?.*)?$/, async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    data.fx = { pair: "USD/MXN", rate: "18.5", provider: "DEMO", observed_at: "2026-11-13T18:00:00Z", total_value_mxn: "5000.00", is_fallback: false };
    await route.fulfill({ response, json: data });
  });
  await login(page);
  const conversion = page.locator(".portfolio-stat").filter({ hasText: "USD en MXN (referencia)" });
  await expect(conversion).toContainText("MXN");
  await expect(conversion).toContainText("USD/MXN 18.5");
});

test("conciliación GBM accesible en móvil con movimiento reducido", async ({ page }) => {
  const errors = watchErrors(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await login(page);
  await expect(page.getByRole("heading", { name: "Tus 3 posiciones principales" })).toBeVisible();
  await page.getByRole("button", { name: "Importar y exportar GBM" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Actualizar con GBM" })).toBeVisible();
  await expect(page.getByLabel("Cuenta GBM")).toHaveValue(/.+/);
  await page.getByLabel("Archivo XLSX").setInputFiles({ name: "invalido.xlsx", mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buffer: Buffer.from("archivo no válido") });
  await page.getByRole("button", { name: "Cargar y comparar" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "No se pudo leer el XLSX" })).toBeVisible();
  await page.getByLabel("Archivo XLSX").setInputFiles(path.join(os.tmpdir(), "asi-e2e-gbm_1794613125783.xlsx"));
  await page.getByRole("button", { name: "Cargar y comparar" }).click();
  await expect(page.getByRole("heading", { name: "Resultado de la conciliación" })).toBeVisible();
  await expect(page.getByText("No se crearon movimientos.")).toBeVisible();
  await page.getByRole("button", { name: "Posiciones", exact: true }).click();
  await expect(page.getByRole("rowheader", { name: "AAA", exact: true })).toBeVisible();
  expect(errors.filter((e) => !e.includes("400"))).toEqual([]);
});

test("todas las pantallas cargan con sesión y sin errores de JavaScript", async ({ page }) => {
  const errors = watchErrors(page);
  await login(page);
  for (const path of ["/tecnico", "/quant", "/paper", "/inteligencia", "/screener", "/fair-value", "/calculadoras", "/asistente", "/ventas"]) {
    const res = await page.goto(path);
    expect(res.status(), path).toBeLessThan(400);
    await expect(page.getByRole("heading", { level: 1 }).first(), path).toBeVisible();
    await expect(page.getByLabel("Usuario"), `${path} no debe pedir login`).toHaveCount(0);
  }
  expect(errors).toEqual([]);
});
