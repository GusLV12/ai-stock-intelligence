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
  await expect(page.getByLabel("Cuenta virtual")).toHaveValue(/\d+/);

  await page.getByLabel("Emisora").fill("AAA");
  await page.getByLabel("Cantidad").fill("5");
  await page.getByLabel("Nota (por qué)").fill("prueba e2e");
  await page.getByRole("button", { name: "Simular orden" }).click();

  // Compra a mercado: se ejecuta con deslizamiento adverso (más cara que la referencia) y queda como posición.
  const aviso = await page.getByRole("status").textContent();
  const [, ejecutada, referencia] = aviso.match(/Ejecutada a ([\d.]+) \(referencia ([\d.]+)\)/);
  expect(Number(ejecutada)).toBeGreaterThan(Number(referencia));
  expect(Number(ejecutada) / Number(referencia) - 1).toBeCloseTo(0.0005, 4); // 5 pb por defecto
  await expect(page.getByRole("row", { name: new RegExp(`^AAA 5 ${ejecutada} ${referencia}`) })).toBeVisible();
  await page.getByRole("button", { name: /Órdenes \(/ }).click();
  await expect(page.getByRole("row", { name: new RegExp(`Compra 5 Mercado Ejecutada ${referencia} ${ejecutada}`) })).toBeVisible();

  await page.getByRole("button", { name: /Diario \(/ }).click();
  await page.getByLabel(/Qué piensas y qué esperas/).fill("Entrada de práctica e2e");
  await page.getByRole("button", { name: "Guardar en el diario" }).click();
  await expect(page.getByText("Entrada de práctica e2e")).toBeVisible();
  expect(errors).toEqual([]);
});

test("paper trading: una orden sin precio conocido se rechaza y no se inventa", async ({ page }) => {
  await login(page);
  await page.goto("/paper");
  await expect(page.getByLabel("Cuenta virtual")).toBeVisible();
  await page.getByLabel("Emisora").fill("ZZZZ");
  await page.getByLabel("Cantidad").fill("1");
  await page.getByRole("button", { name: "Simular orden" }).click();
  await expect(page.getByText(/rechazada|REJECTED|sin precio|no encontrada/i).first()).toBeVisible();
});

test("paper trading: navegación móvil y movimiento reducido", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await login(page);
  await page.goto("/paper");
  await expect(page.getByRole("heading", { name: "Nueva orden simulada" })).toBeVisible();
  const orders = page.getByRole("button", { name: /Órdenes \(/ });
  await orders.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Órdenes simuladas" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Reprocesar pendientes" })).toBeVisible();
  await page.getByText("Ver guía completa").click();
  await expect(page.getByText(/Primero crea una cuenta/)).toBeVisible();
});

test("solo las propuestas cargadas se atribuyen al experimento", async ({ page }) => {
  await login(page);
  await page.route("**/api/v1/quant/experiments", (route) => route.fulfill({ json: [{ id: 42, name: "Ejemplo", verdict: "NO_PROMOVIDO" }] }));
  await page.route("**/api/v1/paper/accounts/*/proposal?*", (route) => route.fulfill({ json: { warnings: [], proposals: [{ ticker: "AAA", side: "BUY", quantity: "1", reason: "Ejemplo" }] } }));
  const sent = [];
  await page.route("**/api/v1/paper/accounts/*/orders", (route) => {
    if (route.request().method() === "POST") {
      sent.push(route.request().postDataJSON());
      return route.fulfill({ status: 201, json: { status: "PENDING" } });
    }
    return route.continue();
  });
  await page.goto("/paper");
  await page.getByLabel("Experimento").selectOption("42");
  await page.getByLabel("Emisora").fill("AAA");
  await page.getByLabel("Cantidad").fill("1");
  await page.getByRole("button", { name: "Simular orden" }).click();
  await expect.poll(() => sent.length).toBe(1);
  expect(sent[0]).not.toHaveProperty("experiment");
  await page.getByRole("button", { name: "Ver propuesta" }).click();
  await page.getByRole("button", { name: "Cargar en el formulario" }).click();
  await page.getByRole("button", { name: "Simular orden" }).click();
  await expect.poll(() => sent.length).toBe(2);
  expect(sent[1].experiment).toBe(42);
  await page.getByRole("button", { name: "Cargar en el formulario" }).click();
  await page.getByLabel("Cantidad").fill("2");
  await page.getByRole("button", { name: "Simular orden" }).click();
  await expect.poll(() => sent.length).toBe(3);
  expect(sent[2]).not.toHaveProperty("experiment");
});
