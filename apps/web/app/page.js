import BackendStatus from "./BackendStatus";

export default function Home() {
  return (
    <main style={{ maxWidth: 640, margin: "0 auto", padding: "48px 16px" }}>
      <h1>AI Stock Intelligence</h1>
      <p>Etapa 0 — preparación y arquitectura base.</p>
      <BackendStatus />
    </main>
  );
}
