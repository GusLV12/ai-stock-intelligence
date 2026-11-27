import { expect, test } from "@playwright/test";
import { login, watchErrors } from "./helpers.mjs";

test("Screener: lee estados con origen SEC, calcula el F-Score y declara la fuente y los límites", async ({ page }) => {
  const errors = watchErrors(page);
  await login(page);
  await page.goto("/screener");
  await expect(page.getByRole("heading", { name: "Screener", exact: true })).toBeVisible();
  await expect(page.getByText(/ingest_sec_financials/)).toBeVisible();

  await page.getByRole("button", { name: "Ejecutar y guardar" }).click();
  const fila = page.getByRole("row", { name: /^AAA Aprobada/ });
  await expect(fila).toBeVisible();
  await expect(fila).toContainText("FY2024 v1 · SEC");
  await expect(fila).toContainText("Parcial"); // estados de 2024 frente a la fecha actual: desactualizados (> 15 meses)
  await expect(page.getByText(/no es una recomendación de compra/).first()).toBeVisible();
  expect(errors).toEqual([]);
});

test("Screener en móvil conserva filtros, exportación y acceso por teclado", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/screener");
  await expect(page.getByRole("heading", { name: "Resultados del screener" })).toBeVisible();
  const file = page.locator('input[type="file"]');
  await file.focus();
  await expect(file).toBeFocused();
  await page.getByLabel("F-Score mínimo (0–9)").fill("7");
  await page.getByRole("button", { name: "Ejecutar y guardar" }).click();
  await expect(page.getByRole("region", { name: "Resultados" })).toBeVisible();
  await page.getByLabel("Mostrar también las no aprobadas").check();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Exportar CSV" }).click();
  expect((await download).suggestedFilename()).toMatch(/^screener-\d+\.csv$/);
  const excel = page.waitForEvent("download");
  await page.getByRole("button", { name: "Exportar Excel" }).click();
  expect((await excel).suggestedFilename()).toMatch(/^screener-\d+\.xlsx$/);
  const template = page.waitForEvent("download");
  await page.getByRole("button", { name: "Plantilla Excel" }).click();
  expect((await template).suggestedFilename()).toBe("plantilla-estados-financieros.xlsx");
});
