"""Mide si el LLM local (Ollama) genera factores en JSON, seguros y correctos. Solo habla con Ollama en esta máquina
y solo le envía las descripciones de las tareas (ningún dato tuyo)."""

import json

from django.core.management.base import BaseCommand, CommandError

from modules.assistant.ollama import OllamaError, _validate_url, post_json
from modules.quant.llm_eval import TASKS, evaluate


class Command(BaseCommand):
    help = "Evalúa la fiabilidad de un modelo local para generar factores (criterio previo a RD-Agent(Q))."

    def add_arguments(self, parser):
        parser.add_argument("--model", default="qwen3:1.7b")
        parser.add_argument("--repeats", type=int, default=3)
        parser.add_argument("--timeout", type=int, default=240)

    def handle(self, *args, **o):
        base = _validate_url("http://127.0.0.1:11434")

        def generate(system, prompt):
            payload = {
                "model": o["model"], "stream": False, "think": False, "format": "json",
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                "options": {"num_ctx": 4096, "num_predict": 600, "temperature": 0.2},
            }  # fmt: skip
            return post_json(f"{base}/api/chat", payload, o["timeout"]).get("message", {}).get("content", "")

        try:
            generate("Responde {}", "ping")
        except OllamaError as exc:
            raise CommandError(f"Ollama no responde: {exc} Inícialo con `ollama serve`.") from exc
        self.stdout.write(f"Evaluando {o['model']}: {len(TASKS)} tareas x {o['repeats']} repeticiones...")
        res = evaluate(generate, repeats=o["repeats"])
        for m, t in res["thresholds"].items():
            self.stdout.write(f"  {m}: {res['rates'][m]:.0%} (mínimo {t:.0%})")
        self.stdout.write(f"  respuestas con saltos de línea reparados: {res['repaired_rate']:.0%}")
        for task, rate in res["by_task"].items():
            self.stdout.write(f"    {task}: correcto {rate:.0%}")
        self.stdout.write(f"Veredicto: {res['verdict']}")
        fails = [r for r in res["rows"] if r["error"]][:5]
        for r in fails:
            self.stdout.write(f"    {r['task']}: {r['error']}")
        self.stdout.write("Detalle JSON: " + json.dumps(res["rates"]))
