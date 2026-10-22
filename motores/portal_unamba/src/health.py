"""Comprobaciones previas: Ollama accesible y modelos descargados."""

import json
import urllib.request

from motores.portal_unamba.src.config import OLLAMA_BASE_URL


def ensure_ollama_ready(models: list[str]) -> None:
    # Sin proxy del sistema: Ollama es local y un proxy corporativo rompería la conexión.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"{OLLAMA_BASE_URL}/api/tags", timeout=5) as response:
            data = json.load(response)
    except Exception as exc:
        raise RuntimeError(
            f"No puedo conectar con Ollama en {OLLAMA_BASE_URL}. Abre la aplicación de Ollama "
            f"(o ejecuta 'ollama serve') y vuelve a intentarlo. Detalle: {exc}"
        ) from exc

    installed = set()
    for item in data.get("models", []):
        installed.update({item.get("name", ""), item.get("model", "")})
    missing = []
    for model in dict.fromkeys(models):
        wanted = model if ":" in model else f"{model}:latest"
        if wanted not in installed:
            missing.append(model)
    if missing:
        commands = "\n".join(f"  ollama pull {m}" for m in missing)
        raise RuntimeError(f"Faltan modelos en Ollama. Ejecuta:\n{commands}")
