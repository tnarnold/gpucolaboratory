#!/usr/bin/env python3
"""Adiciona/atualiza o provider "colab" (Ollama na tailnet) no config do opencode.

Preserva os outros providers, faz backup do arquivo e aceita .json ou .jsonc
(comentários são removidos ao regravar).

Uso:
  opencode_config.py --base-url http://colab-gpu.x.ts.net:11434/v1 [--models a,b] [--context 32768] [--default MODEL]
  opencode_config.py --remove
Sem --models, lista os modelos direto da API (/api/tags).
--remove tira o provider e, se o modelo padrão era do Colab, restaura o anterior.
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
        # modelos de embedding não servem para chat
        return [m["name"] for m in json.load(r).get("models", []) if "embed" not in m["name"]]


def load(path: Path) -> dict:
    if not path.exists():
        return {"$schema": "https://opencode.ai/config.json"}
    raw = path.read_text()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return json.loads(strip_jsonc(raw))


def save(path: Path, cfg: dict) -> None:
    if path.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup, n = path.with_name(f"{path.name}.bak-{stamp}"), 1
        while backup.exists():
            backup, n = path.with_name(f"{path.name}.bak-{stamp}-{n}"), n + 1
        shutil.copy2(path, backup)
        print(f"backup: {backup}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def remove(path: Path, prev_model_file: Path) -> int:
    cfg = load(path)
    changed = cfg.get("provider", {}).pop(PROVIDER_ID, None) is not None
    if str(cfg.get("model", "")).startswith(f"{PROVIDER_ID}/"):
        prev = prev_model_file.read_text().strip() if prev_model_file.exists() else ""
        if prev:
            cfg["model"] = prev
            print(f"modelo padrão restaurado: {prev}")
        else:
            del cfg["model"]
        changed = True
    prev_model_file.unlink(missing_ok=True)
    if not changed:
        print(f"opencode: provider '{PROVIDER_ID}' não estava configurado")
        return 0
    save(path, cfg)
    print(f"opencode: provider '{PROVIDER_ID}' removido de {path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="")
    ap.add_argument("--models", default="")
    ap.add_argument("--context", type=int, default=32768)
    ap.add_argument("--default", dest="default_model", default="")
    ap.add_argument("--config", default="")
    ap.add_argument("--remove", action="store_true")
    a = ap.parse_args()

    path = Path(a.config) if a.config else find_config()
    prev_model_file = path.with_name(f".{PROVIDER_ID}-prev-model")
    if a.remove:
        return remove(path, prev_model_file)
    if not a.base_url:
        ap.error("--base-url é obrigatório (ou use --remove)")

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

    cfg = load(path)
    original = json.dumps(cfg, sort_keys=True)

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
        current = str(cfg.get("model", ""))
        if current and not current.startswith(f"{PROVIDER_ID}/"):
            prev_model_file.write_text(current + "\n")  # restaurado no --remove
        cfg["model"] = f"{PROVIDER_ID}/{a.default_model}"

    if json.dumps(cfg, sort_keys=True) == original:
        print(f"opencode: provider '{PROVIDER_ID}' já atualizado ({a.base_url})")
        return 0
    save(path, cfg)

    print(f"opencode: provider '{PROVIDER_ID}' -> {a.base_url} em {path}")
    for m in models:
        print(f"  - {PROVIDER_ID}/{m}")
    if a.default_model:
        print(f"modelo padrão: {cfg['model']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
