"""Corre un experimento del Quant Lab con los cierres guardados y lo registra (inmutable). Es investigación:
no genera órdenes ni recomendaciones."""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from modules.quant.runner import DEFAULT_MODELS, MODEL_LABELS, ExperimentError
from modules.quant.services import create_experiment


class Command(BaseCommand):
    help = "Corre y guarda un experimento cuantitativo (baselines, ridge, LightGBM) con evaluación fuera de muestra."

    def add_arguments(self, parser):
        parser.add_argument("--user", required=True, help="Usuario dueño del experimento.")
        parser.add_argument("--horizon", type=int, default=20, choices=[5, 20])
        parser.add_argument("--models", default=",".join(DEFAULT_MODELS))
        parser.add_argument("--seed", type=int, default=0)
        parser.add_argument("--top-k", type=int, default=3, dest="top_k")
        parser.add_argument("--name", default="")

    def handle(self, *args, **o):
        user = get_user_model().objects.filter(username=o["user"]).first()
        if user is None:
            raise CommandError(f"Usuario {o['user']} no existe.")
        models = [m.strip() for m in o["models"].split(",") if m.strip()]
        bad = [m for m in models if m not in MODEL_LABELS]
        if bad:
            raise CommandError(f"Modelo desconocido: {bad[0]}. Opciones: {', '.join(MODEL_LABELS)}.")
        config = {
            "horizon": o["horizon"],
            "models": models,
            "seed": o["seed"],
            "top_k": o["top_k"],
            "delay": 1,
        }
        try:
            e = create_experiment(user, o["name"] or f"Experimento h={o['horizon']}", config)
        except (ValueError, ExperimentError) as exc:
            raise CommandError(str(exc)) from exc
        r = e.result
        self.stdout.write(
            f"Experimento #{e.id} · datos {e.dataset_hash[:10]} · resultado {e.result_hash[:10]} · intento {e.trials} sobre estos datos"
        )
        sp = r["meta"]["split"]
        self.stdout.write(
            f"Universo {len(r['meta']['universe'])} emisoras · entrenamiento {sp['train'][2]} fechas · validación {sp['valid'][2]} · prueba {sp['test'][2]} (purga {sp['gap_sessions']})"
        )
        self.stdout.write("Modelo                        Rank IC valid   Rank IC prueba   t ajustado")
        for m in r["models"].values():
            if "test" not in m:
                self.stdout.write(f"  {m['label']:<26} no disponible: {m.get('unavailable', '')}")
                continue
            v, t = m["valid"]["rank_ic"], m["test"]["rank_ic"]
            fmt = lambda x: "—" if x is None else f"{x:+.3f}"
            self.stdout.write(
                f"  {m['label']:<26} {fmt(v['mean']):>10} {fmt(t['mean']):>15} {fmt(t['t_adjusted']):>12}"
            )
        p = r["promotion"]
        self.stdout.write(f"Candidato: {r['candidate']} -> {p['verdict']}")
        for reason in p["reasons"]:
            self.stdout.write(f"  - {reason}")
        self.stdout.write("Investigación: no es una recomendación de inversión ni autoriza operar.")
