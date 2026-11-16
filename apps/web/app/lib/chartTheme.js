/**
 * Paleta única para los gráficos (lightweight-charts). Los valores replican los tokens de globals.css:
 * si cambias allí --card, --fg, --line, --pos, --neg o --accent-fg, actualízalos aquí.
 */
const DARK = {
  bg: "#12161d",
  text: "#e8ecf2",
  grid: "#232b37",
  up: "#4ade9a",
  down: "#ff7b72",
  a: "#86a9ff",
  b: "#f0d58a",
  c: "#cdb6f0",
};

const LIGHT = {
  bg: "#ffffff",
  text: "#121826",
  grid: "#dde2ea",
  up: "#12803f",
  down: "#c4332b",
  a: "#2455d6",
  b: "#6b4e00",
  c: "#51317f",
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
