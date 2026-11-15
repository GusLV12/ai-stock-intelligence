import { expect, test } from "@playwright/test";
import { login, watchErrors } from "./helpers.mjs";

test("Quant Lab declara datos insuficientes en vez de inventar resultados", async ({ page }) => {
  const errors = watchErrors(page);
  await login(page);
  await page.goto("/quant");
  await expect(page.getByRole("heading", { name: "Quant Lab" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Datos disponibles" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Nuevo experimento" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Experimentos" })).toBeVisible();
  expect(errors).toEqual([]);
});
