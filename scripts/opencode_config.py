#!/usr/bin/env python3
"""Adiciona/atualiza o provider "colab" (Ollama na tailnet) no config do opencode.

Preserva os outros providers, faz backup do arquivo e aceita .json ou .jsonc
(comentários são removidos ao regravar).

Uso:
  opencode_config.py --base-url http://colab-gpu.x.ts.net:11434/v1 [--models a,b] [--context 32768] [--default MODEL]
Sem --models, lista os modelos direto da API (/api/tags).
"""
import argparse
import json
import os
import shutil
import sys
import time
import urllib.request
from pathlib import Path

PROVIDER_ID = "colab"


def strip_jsonc(text: str) -> str:
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        else:
            out.append(c)
        i += 1
    # vírgulas finais são aceitas em jsonc
    import re
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def find_config() -> Path:
    if os.environ.get("OPENCODE_CONFIG"):
        return Path(os.environ["OPENCODE_CONFIG"])
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "opencode"
    for name in ("opencode.jsonc", "opencode.json", "config.json"):
        if (base / name).exists():
            return base / name
    return base / "opencode.json"


def api_models(base_url: str) -> list[str]:
    root = base_url.rstrip("/").removesuffix("/v1")
    with urllib.request.urlopen(f"{root}/api/tags", timeout=10) as r:
        return [m["name"] for m in json.load(r).get("models", [])]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--models", default="")
    ap.add_argument("--context", type=int, default=32768)
    ap.add_argument("--default", dest="default_model", default="")
    ap.add_argument("--config", default="")
    a = ap.parse_args()

    models = [m.strip() for m in a.models.split(",") if m.strip()]
    if not models:
        try:
            models = api_models(a.base_url)
        except Exception as e:  # noqa: BLE001
            print(f"erro: não consegui listar modelos em {a.base_url} ({e}); use --models", file=sys.stderr)
            return 1
    if not models:
        print("erro: nenhum modelo encontrado", file=sys.stderr)
        return 1

    path = Path(a.config) if a.config else find_config()
    cfg = {"$schema": "https://opencode.ai/config.json"}
    if path.exists():
        raw = path.read_text()
        try:
            cfg = json.loads(raw)
        except json.JSONDecodeError:
            cfg = json.loads(strip_jsonc(raw))
        backup = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(path, backup)
        print(f"backup: {backup}")

    providers = cfg.setdefault("provider", {})
    prev = providers.get(PROVIDER_ID, {})
    prev_models = prev.get("models", {})
    providers[PROVIDER_ID] = {
        "npm": "@ai-sdk/openai-compatible",
        "name": "Colab GPU (tailnet)",
        "options": {"baseURL": a.base_url},
        "models": {
            m: {
                **prev_models.get(m, {}),
                "name": prev_models.get(m, {}).get("name", f"{m} (Colab)"),
                "tool_call": True,
                "limit": {"context": a.context, "output": min(8192, a.context // 4)},
            }
            for m in models
        },
    }
    if a.default_model:
        cfg["model"] = f"{PROVIDER_ID}/{a.default_model}"

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)

    print(f"opencode: provider '{PROVIDER_ID}' -> {a.base_url} em {path}")
    for m in models:
        print(f"  - {PROVIDER_ID}/{m}")
    if a.default_model:
        print(f"modelo padrão: {cfg['model']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
