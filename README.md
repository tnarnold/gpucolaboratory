# colab-llm

Sobe LLMs na GPU do **Google Colab** com um comando e entrega a API (Ollama + compatível com OpenAI) **só dentro da sua tailnet** do Tailscale.

```
seu PC ── colab-llm up ──► Colab CLI ──► VM com GPU no Colab
                                          ├─ tailscaled (userspace, nó efêmero)
                                          └─ ollama :11434 ──► GPU
seus apps ──── tailnet ────► http://colab-gpu:11434/v1
```

```console
$ ./colab-llm up --gpu L4 --models qwen3:30b-a3b
==> Criando VM no Colab: GPU=L4 sessão=colab-llm
==> Configurando a VM (Tailscale + Ollama + modelos: qwen3:30b-a3b)
...
Pronto! API na tailnet:
  OpenAI-compat : http://colab-gpu.<tailnet>.ts.net:11434/v1
```

## Requisitos

- Linux ou macOS com `bash`, `curl` e `python3`.
- [Colab CLI](https://github.com/googlecolab/google-colab-cli) oficial do Google:
  ```bash
  uv tool install google-colab-cli
  ```
  Sem `uv`, use `pipx install google-colab-cli` ou `pip install google-colab-cli`.
- [Tailscale](https://tailscale.com/download) instalado e logado nesta máquina.
- Conta Google com Colab. No **Free** só tem T4. **Pro/Pro+** liberam L4, A100, H100 e G4, conforme a disponibilidade.

## Instalação

```bash
git clone https://github.com/<você>/colab-llm && cd colab-llm
cp config.example.env config.env   # opcional
```

### Tailscale: auth key (recomendado)

Sem a chave, cada `up` mostra um link de login do Tailscale para você clicar. Com a chave, fica 100% automático:

1. Abra [admin → Settings → Keys](https://login.tailscale.com/admin/settings/keys) → **Generate auth key**.
   Marque **Reusable**, **Ephemeral** e **Pre-approved**. Se quiser, adicione uma tag como `tag:colab`.
2. Salve a chave (fica só na sua máquina, com permissão 600):
   ```bash
   mkdir -p ~/.config/colab-llm && read -rs -p 'TS key: ' k && echo "$k" > ~/.config/colab-llm/ts_authkey && chmod 600 ~/.config/colab-llm/ts_authkey
   ```

As auth keys expiram em no máximo 90 dias. Para nunca expirar, use um **OAuth client** (escopo `auth_keys`, com tag), coloque o `tskey-client-...` no mesmo arquivo e defina `TS_TAGS=tag:colab`.

Exemplo de ACL liberando a API só para os membros da tailnet:

```jsonc
"tagOwners": { "tag:colab": ["autogroup:admin"] },
"grants": [
  { "src": ["autogroup:member"], "dst": ["tag:colab"], "ip": ["tcp:11434"] }
]
```

### Colab: primeiro login

No primeiro comando, o Colab CLI mostra um link do Google. Abra, autorize e cole o código no terminal. O token fica em `~/.config/colab-cli/`.

## Uso

```bash
./colab-llm up                                   # usa config.env / padrões (T4, qwen2.5:7b)
./colab-llm up --gpu A100 --high-mem --models qwen2.5:32b,nomic-embed-text
./colab-llm status                               # sessão + modelos carregados na GPU
./colab-llm pull llama3.1:8b                     # baixa mais um modelo
./colab-llm logs                                 # log do Ollama
./colab-llm shell                                # console na VM
./colab-llm keepalive 10                         # ping no kernel a cada 10 min (opcional)
./colab-llm down                                 # desliga a VM e para de gastar unidades
```

### Consumindo a API

```bash
curl http://colab-gpu:11434/v1/chat/completions -H 'Content-Type: application/json' -d '{"model":"qwen2.5:7b","messages":[{"role":"user","content":"oi"}]}'
```

```python
from openai import OpenAI
client = OpenAI(base_url="http://colab-gpu:11434/v1", api_key="ollama")
print(client.chat.completions.create(model="qwen2.5:7b", messages=[{"role": "user", "content": "oi"}]).choices[0].message.content)
```

```bash
OLLAMA_HOST=http://colab-gpu:11434 ollama run qwen2.5:7b
```

Funciona com Open WebUI, Continue, aider, LiteLLM e qualquer cliente OpenAI: `base_url=http://colab-gpu:11434/v1`, qualquer `api_key`.

### opencode

```bash
./colab-llm opencode                       # adiciona o provider "colab" com os modelos da VM
./colab-llm opencode --default qwen3:30b-a3b   # e define como modelo padrão
```

O comando adiciona o provider `colab` ao `~/.config/opencode/opencode.json(c)` e mantém os outros providers. Antes de alterar, faz um backup `.bak-<data>`. Depois, no opencode, escolha o modelo com `/models` → `colab/...`.

Se a VM estiver ligada, ele lista os modelos pela API. Se estiver desligada, usa os `MODELS` do config.

Para quem só consome a API de outra máquina da tailnet:

```bash
python3 scripts/opencode_config.py --base-url http://colab-gpu.<tailnet>.ts.net:11434/v1
```

Para o opencode, use modelos com suporte a *tool calling*, como `qwen3`, `qwen2.5-coder`, `llama3.1` e `mistral-small`, e contexto de pelo menos 32k (`OLLAMA_CONTEXT_LENGTH`, que já é o padrão). Modelos pequenos (7B) funcionam, mas erram mais as ferramentas. Se a GPU permitir, prefira `qwen3:30b-a3b` (L4 ou maior).

### Que modelo cabe?

Valores com a quantização padrão do Ollama (Q4):

| GPU  | VRAM     | Exemplos |
|------|----------|----------|
| T4   | 15 GB    | `qwen2.5:7b`, `llama3.1:8b`, `gemma3:12b`, `qwen2.5-coder:14b` |
| L4   | 22 GB    | `qwen3:30b-a3b`, `gemma3:27b`, `mistral-small` |
| A100 | 40/80 GB | `qwen2.5:32b`, `llama3.3:70b` (80 GB) |
| H100 | 80 GB    | `llama3.3:70b`, `qwen2.5:72b` |

## Como funciona

- `colab-llm up` usa `colab new` para criar a VM. Depois envia [`remote/setup.sh`](remote/setup.sh) e um arquivo temporário de configuração, que inclui a chave e é apagado logo após ser lido.
- Na VM, o script:
  - roda o `tailscaled` em **userspace networking**, porque o Colab não tem `/dev/net/tun`;
  - usa `--state=mem:`, o que transforma o nó em **efêmero**: ele some da tailnet quando a VM morre;
  - sobe o `ollama serve` com watchdog e baixa os modelos.
- Depois, a máquina local espera o nó aparecer na tailnet e testa `/api/version` antes de dizer "pronto".

## Limitações e avisos

- **Tempo de sessão:** o Colab encerra VMs ociosas e tem limite de duração (maior no Pro/Pro+). Quando cair, rode `up` de novo. Os modelos são baixados outra vez.
- **Custo:** GPU no Pro consome *compute units*. Rode `down` quando terminar. Veja o saldo com `colab usage`.
- **Termos do Colab:** o Google restringe alguns usos, principalmente no plano gratuito (ex.: proxies, uso não interativo). Expor uma API por túnel pode ser enquadrado nisso. Leia o [FAQ do Colab](https://research.google.com/colaboratory/faq.html) e use por sua conta e risco.
- **Sem autenticação na API:** quem estiver na tailnet consegue usar. Restrinja com ACL (exemplo acima).
- **Hostname:** se já existir `colab-gpu` (ex.: VM anterior ainda não expirada), o Tailscale cria `colab-gpu-1`. O `up` mostra o nome real no final.

## Alternativa sem CLI

[`notebook/colab_llm_tailnet.ipynb`](notebook/colab_llm_tailnet.ipynb) faz o mesmo pela interface web do Colab:

1. Abra o notebook no Colab.
2. Crie o secret `TS_AUTHKEY`.
3. Clique em *Run all*.

## Licença

MIT
