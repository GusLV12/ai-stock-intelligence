import { expect, test } from "@playwright/test";
import { login, watchErrors } from "./helpers.mjs";

test("el portafolio muestra las posiciones ficticias sin errores", async ({ page }) => {
  const errors = watchErrors(page);
  await login(page);
  for (const t of ["AAA", "BBB", "CCC", "DDD"]) {
    await expect(page.getByRole("rowheader", { name: t, exact: true })).toBeVisible();
  }
  expect(errors).toEqual([]);
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
