import { expect, test } from "@playwright/test";
import { watchErrors } from "./helpers.mjs";

test("desglose por venta accesible y exportación del ejercicio", async ({ page }, testInfo) => {
  const errors = watchErrors(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/ventas");
  await page.getByLabel("Ejercicio (hora de México)").selectOption("2026");
  const aaa = page.getByRole("button", { name: /Ver detalle de AAA/ });
  await expect(aaa).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("ventas-escritorio.png"), fullPage: true });
  await aaa.focus(); await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog")).toContainText("AAA");
  await expect(page.getByRole("dialog")).toContainText("Comisión de venta");
  await page.screenshot({ path: testInfo.outputPath("ventas-detalle.png") });
  const search = page.getByRole("searchbox", { name: "Buscar emisora" });
  await search.pressSequentially("BBB");
  await expect(search).toHaveValue("BBB");
  await expect(search).toBeFocused();
  await page.getByRole("button", { name: /Abrir detalle de BBB/ }).click();
  await expect(page.getByRole("dialog")).toContainText("BBB");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Abrir detalle de BBB/ })).toBeFocused();
  await page.getByRole("button", { name: /Ver detalle de BBB/ }).click();
  await expect(page.getByRole("dialog")).toContainText("BBB");
  await page.getByRole("button", { name: "Cerrar", exact: true }).click();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Exportar resumen CSV" }).click();
  expect((await download).suggestedFilename()).toBe("ventas-2026.csv");
  expect(errors).toEqual([]);
});

test("importar CSV exige vista previa y confirmación", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/ventas");
  await page.getByLabel("Ejercicio (hora de México)").selectOption("2026");
  await page.getByRole("button", { name: "Importar movimientos" }).first().click();
  const csv = "operation,ticker,quantity,price,fees,currency,executed_at,broker_reference\nBUY,AAA,1,100,0,USD,2026-10-01T15:00:00Z,PRUEBA-1\nSELL,AAA,1,110,1,USD,2026-11-01T15:00:00Z,PRUEBA-2\n";
  await page.getByLabel("Archivo CSV").setInputFiles({ name: "movimientos.csv", mimeType: "text/csv", buffer: Buffer.from(csv) });
  await page.getByRole("button", { name: "Revisar archivo" }).click();
  await expect(page.getByRole("heading", { name: "Vista previa" })).toBeVisible();
  await expect(page.getByText("La revisión guarda el lote, pero todavía no crea movimientos.")).toBeVisible();
  await expect(page.getByText("Lista para importar")).toHaveCount(2);
  const preview = page.getByRole("region", { name: "Vista previa de importación" });
  await expect(preview).toContainText("2026-10-01T15:00:00");
  await page.getByRole("button", { name: "Cerrar", exact: true }).click();
  await page.getByRole("button", { name: "Importar movimientos" }).first().click();
  await page.getByLabel("Archivo CSV").setInputFiles({ name: "movimientos.csv", mimeType: "text/csv", buffer: Buffer.from(csv) });
  await page.getByRole("button", { name: "Revisar archivo" }).click();
  await expect(page.getByText("Lista para importar")).toHaveCount(2);
  await page.getByRole("button", { name: "Confirmar movimientos" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Importación confirmada" })).toContainText("2 creados");
  await page.getByRole("button", { name: "Cerrar", exact: true }).click();
  await expect(page.getByRole("button", { name: /Ver detalle de AAA/ })).toHaveCount(2);
  await page.getByRole("button", { name: /Ver detalle de AAA/ }).first().click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("dialog").getByRole("button", { name: "Cerrar" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("selecciona la única cuenta aunque llegue después de abrir el formulario", async ({ page }) => {
  let releaseAccounts;
  const gate = new Promise((resolve) => { releaseAccounts = resolve; });
  await page.route("**/api/v1/accounts", async (route) => { await gate; await route.continue(); });
  await page.goto("/ventas");
  await page.getByRole("button", { name: "Importar movimientos" }).first().click();
  const account = page.getByRole("combobox", { name: "Cuenta", exact: true }).last();
  await expect(account).toHaveValue("");
  releaseAccounts();
  await expect(account).not.toHaveValue("");
  await expect(account.locator("option:checked")).toHaveText("Cuenta demo");
  await account.selectOption("");
  await expect(account).toHaveValue("");
});

test("la vista previa permanece vinculada a su cuenta y muestra los importes", async ({ page }) => {
  await page.route("**/api/v1/accounts", (route) => route.fulfill({ json: [{ id: 101, name: "Cuenta A" }, { id: 102, name: "Cuenta B" }] }));
  await page.route("**/api/v1/imports", (route) => route.fulfill({ status: 201, json: {
    id: 50, account: 101, status: "PREVIEW", rows: [{ row: 2, ticker: "AAA", status: "VALID", errors: [], data: { operation: "BUY", quantity: "1.25", price: "100", fees: "2", currency: "USD", executed_at: "2026-10-01T15:00:00+00:00" } }],
  } }));
  await page.goto("/ventas");
  const accounts = page.getByRole("combobox", { name: "Cuenta", exact: true });
  await accounts.first().selectOption("101");
  await page.getByRole("button", { name: "Importar movimientos" }).first().click();
  await page.getByLabel("Archivo CSV").setInputFiles({ name: "ficticio.csv", mimeType: "text/csv", buffer: Buffer.from("ficticio") });
  await page.getByRole("button", { name: "Revisar archivo" }).click();
  await expect(page.getByRole("heading", { name: "Vista previa" })).toBeVisible();
  await accounts.first().selectOption("102");
  await expect(accounts.last()).toHaveValue("101");
  await expect(page.getByRole("region", { name: "Vista previa de importación" })).toContainText("1.25");
  await expect(page.getByRole("region", { name: "Vista previa de importación" })).toContainText("100");
});

test("ofrece acciones cuando el ejercicio no tiene ventas", async ({ page }) => {
  await page.goto("/ventas");
  await page.getByLabel("Ejercicio (hora de México)").selectOption("2025");
  await expect(page.getByRole("heading", { name: "Sin ventas en 2025" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Ver portafolio" })).toBeVisible();
});
