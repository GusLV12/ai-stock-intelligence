import { test as setup } from "@playwright/test";
import { STATE } from "../playwright.config.mjs";
import { loginWithForm } from "./helpers.mjs";

setup("iniciar sesión una vez y guardar la sesión", async ({ page }) => {
  await loginWithForm(page);
  await page.context().storageState({ path: STATE });
});
