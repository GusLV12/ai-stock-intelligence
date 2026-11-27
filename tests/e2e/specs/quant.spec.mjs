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
  await expect(page.getByText("Sin experimento seleccionado")).toBeVisible();
  await expect(page.getByRole("button", { name: "Correr experimento" })).toBeVisible();
  await page.getByText("Ver guía completa").click();
  await expect(page.getByText("Comprueba cuántas emisoras hay y la fecha del último cierre", { exact: false })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(page.getByRole("heading", { name: "Nuevo experimento" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Guía rápida" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("muestra la simulación y permite revisar un experimento sin ejecutarlo", async ({ page }) => {
  const errors = watchErrors(page);
  await login(page);
  const model = { label: "Regresión ridge", valid: { rank_ic: { mean: 0.03 } }, test: { rank_ic: { mean: 0.02, t_adjusted: 1, pct_positive: 0.6 } }, folds: [], backtest: { dates: ["2025-01-02", "2025-01-03"], config: { capital: 100, top_k: 3, rebalance_days: 20, costs: { commission_bps: 1, half_spread_bps: 2, slippage_bps: 2 } }, strategy: { equity: [100, 101], total_return: 0.01, turnover_one_way_annual: 0, cost_drag_pct_capital: 0, max_drawdown: 0 }, benchmark_equal_weight: { equity: [100, 100] }, benchmark_buy_hold: { equity: [100, 99] }, excess_vs_equal_weight: 0.01, notes: [] } };
  const summary = { id: 99, name: "Ejemplo de prueba", horizon: 20, verdict: "NO_PROMOVIDO", candidate_label: model.label, test_rank_ic: 0.02 };
  const detail = { ...summary, trials: 1, dataset_hash: "abc1234567890123", result_hash: "def1234567890123", quant_version: "test", config: { as_of: "2025-01-03" }, result: { candidate: "ridge", models: { ridge: model }, promotion: { reasons: ["No cumple el criterio."], checks: [{ id: "test", label: "Prueba", value: 1, direction: ">=", threshold: 2, passed: false }] }, meta: { seed: 0, rows: 100, universe: [1], split: { mode: "walk_forward", folds: [], gap_sessions: 21 }, caveats: [], tickers: {} }, latest_scores: [] } };
  await page.route("**/api/v1/quant/experiments", (route) => route.fulfill({ json: [summary] }));
  await page.route("**/api/v1/quant/experiments/99", (route) => route.fulfill({ json: detail }));
  await page.goto("/quant");
  await expect(page.getByRole("heading", { name: "Simulación fuera de muestra · base 100" })).toBeVisible();
  await expect(page.getByRole("img", { name: "Curvas de capital de la estrategia y sus referencias" })).toBeVisible();
  await expect(page.getByText("No promovido").first()).toBeVisible();
  await page.getByText("Ver criterios y motivos del veredicto").click();
  await expect(page.getByText("No cumple el criterio.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Volver a correr y comparar" })).toBeVisible();
  expect(errors).toEqual([]);
});
