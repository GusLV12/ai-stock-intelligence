import { expect, test } from "@playwright/test";
import { login, watchErrors } from "./helpers.mjs";

test("paper trading: crear cuenta, simular una compra y dejar una nota en el diario", async ({ page }) => {
  const errors = watchErrors(page);
  await login(page);
  await page.goto("/paper");
  await expect(page.getByRole("heading", { name: "Paper trading" })).toBeVisible();

  await page.getByLabel("Nombre").fill("Cuenta e2e");
  await page.getByLabel("Capital inicial (USD)").fill("10000");
  await page.getByRole("button", { name: "Crear cuenta virtual" }).click();
  await expect(page.getByRole("cell", { name: "Cuenta e2e" })).toBeVisible();
  await page.getByRole("row", { name: /Cuenta e2e/ }).getByRole("button", { name: "Abrir" }).click();

  await page.getByLabel("Emisora").fill("AAA");
  await page.getByLabel("Cantidad").fill("5");
  await page.getByLabel("Nota (por qué)").fill("prueba e2e");
  await page.getByRole("button", { name: "Simular orden" }).click();

  // Compra a mercado: se ejecuta con deslizamiento adverso (más cara que la referencia) y queda como posición.
  const aviso = await page.getByRole("status").textContent();
  const [, ejecutada, referencia] = aviso.match(/Ejecutada a ([\d.]+) \(referencia ([\d.]+)\)/);
  expect(Number(ejecutada)).toBeGreaterThan(Number(referencia));
  expect(Number(ejecutada) / Number(referencia) - 1).toBeCloseTo(0.0005, 4); // 5 pb por defecto
  await expect(page.getByRole("row", { name: new RegExp(`Compra 5 Mercado Ejecutada ${referencia} ${ejecutada}`) })).toBeVisible();
  await expect(page.getByRole("row", { name: new RegExp(`^AAA 5 ${ejecutada} ${referencia}`) })).toBeVisible();

  await page.getByLabel(/Qué piensas y qué esperas/).fill("Entrada de práctica e2e");
  await page.getByRole("button", { name: "Guardar en el diario" }).click();
  await expect(page.getByText("Entrada de práctica e2e")).toBeVisible();
  expect(errors).toEqual([]);
});

test("paper trading: una orden sin precio conocido se rechaza y no se inventa", async ({ page }) => {
  await login(page);
  await page.goto("/paper");
  await page.getByRole("row", { name: /Cuenta e2e/ }).getByRole("button", { name: "Abrir" }).click();
  await page.getByLabel("Emisora").fill("ZZZZ");
  await page.getByLabel("Cantidad").fill("1");
  await page.getByRole("button", { name: "Simular orden" }).click();
  await expect(page.getByText(/rechazada|REJECTED|sin precio|no encontrada/i).first()).toBeVisible();
});
