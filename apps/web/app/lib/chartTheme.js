/**
 * Paleta única para los gráficos (lightweight-charts). Los valores replican los tokens de globals.css:
 * si cambias allí --card, --fg, --line, --pos, --neg o --accent-fg, actualízalos aquí.
 */
const DARK = {
  bg: "#11131a",
  text: "#eceef4",
  grid: "#1f2330",
  up: "#35d6a1",
  down: "#ff6b7d",
  a: "#a79cff",
  b: "#f0c36a",
  c: "#5fd0e8",
};

const LIGHT = {
  bg: "#ffffff",
  text: "#14151f",
  grid: "#e0e2ec",
  up: "#0f8a5f",
  down: "#cf2f48",
  a: "#4c3dd0",
  b: "#8a5a00",
  c: "#0a7ea4",
};

export function chartPalette() {
  const dark = typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches;
  return dark ? DARK : LIGHT;
}

/** Opciones base compartidas por todos los gráficos. */
export function chartOptions(p, height) {
  return {
    height,
    layout: { background: { color: p.bg }, textColor: p.text, fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif" },
    grid: { vertLines: { color: p.grid }, horzLines: { color: p.grid } },
    rightPriceScale: { borderColor: p.grid },
    timeScale: { borderColor: p.grid },
  };
}
