import { expect, test } from "@playwright/test";
import { login, watchErrors } from "./helpers.mjs";

test("Screener: lee estados con origen SEC, calcula el F-Score y declara la fuente y los límites", async ({ page }) => {
  const errors = watchErrors(page);
  await login(page);
  await page.goto("/screener");
  await expect(page.getByRole("heading", { name: "Screener" })).toBeVisible();
  await expect(page.getByText(/ingest_sec_financials/)).toBeVisible();

  await page.getByRole("button", { name: "Ejecutar y guardar" }).click();
  const fila = page.getByRole("row", { name: /^AAA Aprobada/ });
  await expect(fila).toBeVisible();
  await expect(fila).toContainText("FY2024 v1 · SEC");
  await expect(fila).toContainText("Parcial"); // estados de 2024 frente a la fecha actual: desactualizados (> 15 meses)
  await expect(page.getByText(/no es una recomendación de compra/).first()).toBeVisible();
  expect(errors).toEqual([]);
});
