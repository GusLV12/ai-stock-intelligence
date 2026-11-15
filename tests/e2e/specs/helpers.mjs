import { expect } from "@playwright/test";
import { DEMO_PASS, DEMO_USER } from "../playwright.config.mjs";

/** Con sesión guardada (proyecto "app") basta abrir la raíz; el formulario solo se usa en setup y en auth.spec. */
export async function login(page) {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Mi portafolio" })).toBeVisible();
}

export async function loginWithForm(page) {
  await page.goto("/login");
  await page.getByLabel("Usuario").fill(DEMO_USER);
  await page.getByLabel("Contraseña").fill(DEMO_PASS);
  await page.getByRole("button", { name: "Entrar" }).click();
  await expect(page.getByRole("heading", { name: "Mi portafolio" })).toBeVisible();
}

/** Recoge errores de consola y de página para fallar si la pantalla rompe en silencio. */
export function watchErrors(page) {
  const errors = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("console", (m) => {
    if (m.type() === "error" && !/favicon|Failed to load resource.*40[13]/.test(m.text())) errors.push(`console: ${m.text()}`);
  });
  return errors;
}
