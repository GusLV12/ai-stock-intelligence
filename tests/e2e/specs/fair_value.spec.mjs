import { expect, test } from "@playwright/test";

test("cálculo previo, modos e historial sin datos reales", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.clock.setFixedTime(new Date("2026-11-24T02:30:00Z"));
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/fair-value");
  await page.getByRole("button", { name: "Cargar ejemplo ficticio" }).click();
  await expect(page.getByText("Datos de ejemplo: no corresponden")).toBeVisible();
  await page.getByRole("button", { name: "2. DCF" }).click();
  await page.getByLabel("FCFF del último año").fill("125");
  await page.getByRole("button", { name: "Modo avanzado" }).click();
  await expect(page.getByLabel("FCFF del último año")).toHaveValue("125");
  await page.getByRole("button", { name: "Modo guiado" }).click();
  await expect(page.getByLabel("FCFF del último año")).toHaveValue("125");
  await page.getByRole("button", { name: "Calcular sin guardar" }).click();
  await expect(page.getByText("Cálculo sin guardar", { exact: false })).toBeVisible();
  await expect(page.getByText("Aún no hay valoraciones guardadas.")).toBeVisible();
  await page.getByRole("button", { name: "Guardar valoración" }).click();
  await expect(page.getByText(/Guardada como valoración/)).toBeVisible();
  await page.getByRole("button", { name: "Ver detalle" }).first().click();
  await expect(page.getByText("Supuestos guardados")).toBeVisible();
});

test("cargar una emisora muestra procedencia y datos faltantes", async ({ page }) => {
  await page.goto("/fair-value");
  await page.getByLabel("Buscar acción por símbolo o nombre").fill("AAA");
  await page.getByRole("button", { name: "Cargar acción" }).first().click();
  await expect(page.getByRole("heading", { name: "Acción seleccionada" })).toBeVisible();
  await expect(page.locator(".fv-source")).toContainText("Fuente:");
  await expect(page.locator(".fv-source")).toContainText("Estados financieros:");
});
