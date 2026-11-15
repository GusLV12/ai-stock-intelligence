import { expect, test } from "@playwright/test";
import { login, watchErrors } from "./helpers.mjs";

test("Liquidez: tabla de la cartera y consulta de una orden hipotética, con límites declarados", async ({ page }) => {
  const errors = watchErrors(page);
  await login(page);
  await page.goto("/tecnico");
  await page.getByRole("tab", { name: "Liquidez" }).click();
  await expect(page.getByRole("heading", { name: "Liquidez de tu cartera" })).toBeVisible();

  // AAA tiene historial ficticio (volumen ~1,100 títulos/día): liquidez muy baja y posición visible; BBB no tiene historial.
  const aaa = page.getByRole("row", { name: /^AAA muy baja/ });
  await expect(aaa).toBeVisible();
  await expect(aaa).toContainText("0.402 %"); // 485 títulos entre cuentas demo / volumen diario
  await expect(aaa).toContainText(/pb \(±/); // las velas ficticias tienen un rango fijo: el spread sí es distinguible
  await expect(page.getByRole("row", { name: /^BBB sin datos/ })).toBeVisible();
  await expect(page.getByText(/no es una garantía de ejecución/)).toBeVisible();

  await page.getByRole("combobox", { name: "Emisora" }).selectOption("AAA");
  await page.getByLabel("Títulos").fill("110");
  await page.getByRole("button", { name: "Calcular" }).click();
  await expect(page.getByText(/del volumen diario \(tamaño (visible|alto)\)/)).toBeVisible();
  await expect(page.getByText(/Impacto aproximado.*no una predicción de costo/)).toBeVisible();
  expect(errors).toEqual([]);
});
