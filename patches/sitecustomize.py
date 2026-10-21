"""Metric attribution patch for the LiteLLM proxy.

Two gaps in LiteLLM's GenAI metric recorder (``plumbing/metrics.py``) stop a
multi-provider dashboard from saying which route served a request:

1. ``gen_ai.request.model`` is stamped from ``kwargs["model"]`` — the *dispatched*
   deployment model (``anthropic/mimo-v2.6-pro`` -> ``mimo-v2.6-pro``) — while the
   span path uses the model *group*, the alias the caller asked for
   (``claude-opus-5``). Two routes sharing an upstream model name collapse into one
   series. ``_common_attributes`` is wrapped to prefer the group.

2. Provider labels come only from ``litellm_params.custom_llm_provider``, which the
   ``/v1/messages`` path leaves empty, so those requests land in an unlabelled
   series. The provider is recovered from the logging payload's own
   ``custom_llm_provider``, which is populated on every path.

Loaded automatically by Python's ``site`` module via PYTHONPATH in
docker-compose.yml. Defensive throughout: if this LiteLLM version changes shape it
leaves the default behaviour alone rather than breaking telemetry.
"""

import sys


def _model_group(payload, kwargs):
    """The alias the caller asked for, from wherever this version carries it."""
    if isinstance(payload, dict):
        group = payload.get("model_group") or (payload.get("metadata") or {}).get("model_group")
    else:
        group = getattr(payload, "model_group", None) or (getattr(payload, "metadata", None) or {}).get("model_group")
    return group or kwargs.get("model_group") or None


def _provider_from(params, payload, kwargs):
    """The litellm provider name: explicit when present, else inferred.

    ``litellm_params.custom_llm_provider`` is missing on the ``/v1/messages`` path.
    The logging payload carries it as a bare name (``anthropic``); a ``provider/model``
    string is the last resort and yields its prefix.
    """
    explicit = params.get("custom_llm_provider")
    if isinstance(explicit, str) and explicit:
        return explicit

    if isinstance(payload, dict):
        from_payload = payload.get("custom_llm_provider")
    else:
        from_payload = getattr(payload, "custom_llm_provider", None)
    if isinstance(from_payload, str) and from_payload:
        return from_payload

    for source in (params.get("model"), kwargs.get("model")):
        if isinstance(source, str) and "/" in source:
            return source.split("/", 1)[0]
    return None


def _apply():
    from litellm.integrations.otel.plumbing import metrics as metrics_mod

    original = metrics_mod.GenAIMetricRecorder._common_attributes
    genai = metrics_mod.GenAI

    def _common_attributes(self, kwargs):
        attrs = original(self, kwargs)
        try:
            payload = kwargs.get("standard_logging_object")
            group = _model_group(payload, kwargs)
            if group:
                attrs[genai.REQUEST_MODEL] = str(group)

            if not attrs.get(genai.SYSTEM) and not attrs.get(genai.PROVIDER_NAME):
                provider = _provider_from(kwargs.get("litellm_params") or {}, payload, kwargs)
                if provider:
                    attrs[genai.SYSTEM] = provider
                    attrs[genai.PROVIDER_NAME] = metrics_mod.resolve_provider(provider)
        except Exception:  # never let attribution break metric emission
            pass
        return attrs

    metrics_mod.GenAIMetricRecorder._common_attributes = _common_attributes


try:
    _apply()
except Exception as exc:  # noqa: BLE001 - startup must not fail
    sys.stderr.write(f"[litellm-otel-patch] metric attribution not applied: {exc}\n")
