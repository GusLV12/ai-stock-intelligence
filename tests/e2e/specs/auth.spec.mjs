import { expect, test } from "@playwright/test";
import { loginWithForm as login } from "./helpers.mjs";

test("sin sesión no se ve el portafolio", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Mi portafolio" })).toHaveCount(0);
  await expect(page.getByLabel("Usuario")).toBeVisible();
});

test("credenciales incorrectas muestran un error y no entran", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Usuario").fill("demo");
  await page.getByLabel("Contraseña").fill("incorrecta");
  await page.getByRole("button", { name: "Entrar" }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Mi portafolio" })).toHaveCount(0);
});

test("inicia y cierra sesión", async ({ page }) => {
  await login(page);
  await page.getByRole("button", { name: "Cerrar sesión" }).click();
  await expect(page.getByLabel("Usuario")).toBeVisible();
  await page.goto("/paper");
  await expect(page.getByRole("heading", { name: "Paper trading" })).toHaveCount(0);
});
