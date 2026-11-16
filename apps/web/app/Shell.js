"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const ICONS = {
  portafolio: (
    <>
      <rect x="3" y="3" width="7" height="9" rx="1.5" />
      <rect x="14" y="3" width="7" height="5" rx="1.5" />
      <rect x="14" y="12" width="7" height="9" rx="1.5" />
      <rect x="3" y="16" width="7" height="5" rx="1.5" />
    </>
  ),
  ventas: (
    <>
      <path d="M6 3h12v18l-3-2-3 2-3-2-3 2z" />
      <path d="M9 8h6M9 12h6" />
    </>
  ),
  tecnico: (
    <>
      <path d="M7 4v3M7 17v3M17 3v4M17 15v5" />
      <rect x="5" y="7" width="4" height="10" rx="1" />
      <rect x="15" y="7" width="4" height="8" rx="1" />
    </>
  ),
  fairValue: (
    <>
      <path d="M12 4v16M7 20h10M5 7h14" />
      <path d="M5 7l-3 7a3 3 0 0 0 6 0zM19 7l-3 7a3 3 0 0 0 6 0z" />
    </>
  ),
  screener: <path d="M3 5h18l-7 8v6l-4-2v-4z" />,
  inteligencia: (
    <>
      <path d="M12 3v9h9" />
      <path d="M20.5 15A9 9 0 1 1 9 3.5" />
    </>
  ),
  quant: (
    <>
      <path d="M9 3h6M10 3v6l-5.5 9.5A1.5 1.5 0 0 0 5.8 21h12.4a1.5 1.5 0 0 0 1.3-2.5L14 9V3" />
      <path d="M7.5 15h9" />
    </>
  ),
  paper: (
    <>
      <path d="M6 3h9l4 4v14H6z" />
      <path d="M14 3v5h5M9 13h7M9 17h5" />
    </>
  ),
  calculadoras: (
    <>
      <rect x="5" y="3" width="14" height="18" rx="2" />
      <path d="M8.5 7.5h7M9 12h.01M12 12h.01M15 12h.01M9 16h.01M12 16h.01M15 16h.01" />
    </>
  ),
  asistente: <path d="M4 5h16v11H9l-5 4z" />,
};

/* Grupos separados por una línea fina: resumen, análisis, laboratorio y herramientas. */
const GROUPS = [
  [
    ["/", "Portafolio", "portafolio"],
    ["/ventas", "Ventas por ejercicio", "ventas"],
  ],
  [
    ["/tecnico", "Técnico", "tecnico"],
    ["/fair-value", "Fair Value", "fairValue"],
    ["/screener", "Screener", "screener"],
    ["/inteligencia", "Inteligencia", "inteligencia"],
  ],
  [
    ["/quant", "Quant Lab", "quant"],
    ["/paper", "Paper trading", "paper"],
  ],
  [
    ["/calculadoras", "Calculadoras", "calculadoras"],
    ["/asistente", "Asistente", "asistente"],
  ],
];

function isActive(pathname, href) {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
}

export default function Shell({ children }) {
  const pathname = usePathname();
  if (pathname === "/login") return children;

  return (
    <div className="shell">
      <a className="skip" href="#contenido">Saltar al contenido</a>
      <aside className="sidebar">
        <Link className="brand" href="/" aria-label="AI Stock Intelligence, inicio">
          <span className="brand-mark" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M3 17l5-6 4 4 8-9" />
            </svg>
          </span>
          <span className="brand-name">AI Stock<br />Intelligence</span>
        </Link>
        <nav aria-label="Menú principal">
          <ul className="sidenav">
            {GROUPS.flatMap((group, gi) => [
              ...(gi > 0 ? [<li key={`sep-${gi}`} className="sep" role="separator" />] : []),
              ...group.map(([href, label, icon]) => (
                <li key={href}>
                  <Link href={href} title={label} aria-current={isActive(pathname, href) ? "page" : undefined}>
                    <svg viewBox="0 0 24 24" aria-hidden="true">{ICONS[icon]}</svg>
                    <span className="lbl">{label}</span>
                  </Link>
                </li>
              )),
            ])}
          </ul>
        </nav>
      </aside>
      <div className="shell-main" id="contenido" tabIndex={-1}>{children}</div>
    </div>
  );
}
