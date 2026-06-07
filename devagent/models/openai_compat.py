"""OpenAI-compatible client. Serves llama.cpp (local Qwen), OpenAI/GPT, OpenRouter,
Together, and most other providers — all the same wire protocol."""
from __future__ import annotations

from .base import CompletionResult, ModelClient, estimate_tokens


class OpenAICompatClient(ModelClient):
    def __init__(self, name, model_id, tier, defaults, base_url, api_key, timeout_s,
                 extra_body=None):
        super().__init__(name, model_id, tier, defaults)
        from openai import OpenAI  # lazy: importing devagent must not require the SDK
        import httpx

        self.base_url = base_url
        self.extra_body = extra_body or {}  # e.g. {"chat_template_kwargs": {"enable_thinking": False}}
        # Short connect timeout so a wrong/dead endpoint fails fast; long read timeout
        # because local generation on a 27B model can legitimately take a while.
        timeout = httpx.Timeout(connect=5.0, read=float(timeout_s), write=15.0, pool=5.0)
        self._client = OpenAI(
            base_url=base_url,
            api_key=api_key or "not-needed",  # llama.cpp ignores the key
            timeout=timeout,
            max_retries=0,  # the Router owns retry/fallback policy
        )

    def complete(self, system, user, *, max_tokens=None, temperature=None, cacheable_system=False):
        kwargs = {}
        if self.extra_body:
            kwargs["extra_body"] = self.extra_body
        resp = self._client.chat.completions.create(
            model=self.model_id,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature if temperature is not None else self.defaults.get("temperature", 0.2),
            max_tokens=max_tokens or self.defaults.get("max_tokens", 8192),
            **kwargs,
        )
        msg = resp.choices[0].message
        # A reasoning model may put the answer in reasoning_content and leave content empty;
        # fall back to it so output isn't lost (best fixed with disable_thinking in config).
        text = (msg.content or "") or (getattr(msg, "reasoning_content", "") or "")
        usage = getattr(resp, "usage", None)
        if usage is not None:
            tin = getattr(usage, "prompt_tokens", 0) or 0
            tout = getattr(usage, "completion_tokens", 0) or 0
        else:  # some llama.cpp builds omit usage
            tin = estimate_tokens(system) + estimate_tokens(user)
            tout = estimate_tokens(text)
        return CompletionResult(text=text, tokens_in=tin, tokens_out=tout, model_name=self.name)
