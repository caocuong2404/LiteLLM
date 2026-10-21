# LiteLLM proxy

A **LiteLLM proxy** in Docker that fronts several LLM providers behind one
OpenAI- and Anthropic-compatible endpoint, with a stable model alias per route.

Callers ask for `claude-opus-5`; the proxy dispatches to whichever upstream that
alias points at. Swapping a provider means editing one line of
[`config.yaml`](config.yaml), not the calling code.

> Intended for development, demo and testing — not production.

## Run it

```bash
cp .env.example .env    # then fill in the provider keys
docker compose up -d
```

The proxy serves **http://127.0.0.1:4000**. Stop with `docker compose down`.

## Model aliases

Each alias in [`config.yaml`](config.yaml) is one upstream route:

| Alias                | Upstream model                       | Provider   | Notes                             |
| -------------------- | ------------------------------------ | ---------- | --------------------------------- |
| `claude-haiku-5`     | `minimax/minimax-m3.1-flash:free`    | OpenRouter | free tier, via `openrouter/`      |
| `claude-opus-5`      | `mimo-v2.6-pro`                      | MiMo       | Anthropic-shaped API, custom base |
| `claude-sonnet-5`    | `mimo-v2.5-pro`                      | MiMo       | same provider as above            |
| `claude-haiku-5-plus`| `minimax/minimax-m3.1-flash:free`    | Kilo       | Anthropic-shaped API, custom base |
| `claude-haiku-5-olm` | `minimax/minimax-m3.1-flash:cloud`   | Ollama     | sends an `Authorization` header   |

`GET /v1/models` lists the aliases as they are actually served.

## Call it

Both the OpenAI and the Anthropic request shapes are accepted, so an existing
client can point at the proxy unchanged.

```bash
# OpenAI shape
curl http://127.0.0.1:4000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"claude-opus-5","messages":[{"role":"user","content":"hello"}]}'

# Anthropic shape
curl http://127.0.0.1:4000/v1/messages \
  -H 'Content-Type: application/json' \
  -d '{"model":"claude-opus-5","max_tokens":1024,
       "messages":[{"role":"user","content":"hello"}]}'
```

## Telemetry

The proxy exports OpenTelemetry v2 **GenAI metrics** — TTFT, TPOT, token usage
and cost — over OTLP/HTTP to the collector in the sibling
[`../OpenTelemetry`](../OpenTelemetry) instance, which is published on the
host's `:4318`. Inside the container `localhost` is the container itself, so
`OTEL_ENDPOINT` points at `host.docker.internal:4318`.

Start the collector first, then the proxy, and the Grafana dashboard
**LiteLLM — LLM requests by provider & model** fills in with the traffic.

## The metric attribution patch

[`patches/sitecustomize.py`](patches/sitecustomize.py) is loaded at interpreter
start via `PYTHONPATH` in [`docker-compose.yml`](docker-compose.yml). It fixes
two attribution gaps in LiteLLM's own GenAI metric recorder:

1. `gen_ai.request.model` was stamped from the *dispatched* deployment model
   (`anthropic/mimo-v2.6-pro` → `mimo-v2.6-pro`) while the span path used the
   *model group* — the alias the caller asked for (`claude-opus-5`). Two routes
   sharing an upstream model name collapsed into a single metric series. The
   recorder now prefers the group.
2. Provider labels came only from `litellm_params.custom_llm_provider`, which
   the `/v1/messages` path leaves empty, so those requests landed in an
   unlabelled series. The provider is now recovered from the logging payload's
   own `custom_llm_provider`, which every path populates.

The patch is defensive throughout: if a future LiteLLM release changes shape,
it leaves the default behaviour alone rather than breaking telemetry.

## Security

The proxy binds to **loopback only** (`127.0.0.1:4000`) and, with
`LITELLM_DANGEROUSLY_PERMIT_WEAK_OR_UNSET_MASTER_KEY=true`, serves
**unauthenticated** requests. That is the point of a local lab — but it means
anything running on the machine can spend the API keys in your `.env`.

To require a key, set `LITELLM_MASTER_KEY` in `.env`, drop the
`...PERMIT_WEAK_OR_UNSET_MASTER_KEY` line, and pass
`Authorization: Bearer <key>` on every request.

`.env` is gitignored and must stay that way. Only `.env.example` — placeholders
only — belongs in the repository.

## Layout

```
config.yaml                    the routes: alias -> upstream
docker-compose.yml             the proxy instance
patches/sitecustomize.py       the metric attribution patch
.env.example                   placeholder credentials
```

`config.yaml` and the patch directory are mounted read-only into the
container; the proxy itself is the upstream `ghcr.io/berriai/litellm:latest`
image.
