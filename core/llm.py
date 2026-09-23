"""Thin wrapper around Nebius Token Factory (OpenAI-compatible API)."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Iterator

from openai import BadRequestError, OpenAI

NEBIUS_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"

# Model families in order of preference, matched against the live catalogue.
PREFERRED_FAMILIES = ["nemotron", "minimax", "qwen", "deepseek", "gpt-oss", "kimi", "glm"]
# Non-chat models (embeddings, image, audio, safety, vision) hidden from the picker.
EXCLUDED_KEYWORDS = ["embed", "bge", "e5-", "flux", "stable-diffusion", "sdxl", "whisper", "guard", "rerank", "vl"]
# Exact models to put first when available (tested with this app).
PREFERRED_MODELS = ["nvidia/nemotron-3-super-120b-a12b"]
FALLBACK_MODELS = ["nvidia/nemotron-3-super-120b-a12b", "MiniMaxAI/MiniMax-M3"]

# Reasoning models (e.g. Nemotron 3) spend thousands of tokens thinking before they answer,
# and that counts against max_tokens, so limits must leave plenty of room.
MAX_TOKENS = 16_000
NO_THINKING = {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)
_CITATION = re.compile(r"\s*【[^】]*】")


def make_client(api_key: str) -> OpenAI:
    return OpenAI(base_url=NEBIUS_BASE_URL, api_key=api_key, timeout=180, max_retries=2)


def list_chat_models(api_key: str) -> list[str]:
    """Chat models currently served by Nebius, NVIDIA Nemotron first. Raises on API errors."""
    live = [m.id for m in make_client(api_key).models.list().data]
    chat = [m for m in live if not any(k in m.lower() for k in EXCLUDED_KEYWORDS)]

    def rank(model_id: str) -> tuple[int, str]:
        if model_id in PREFERRED_MODELS:
            return -1, str(PREFERRED_MODELS.index(model_id))
        name = model_id.lower()
        family = next((i for i, f in enumerate(PREFERRED_FAMILIES) if f in name), len(PREFERRED_FAMILIES))
        return family, name

    return sorted(chat, key=rank)


def clean(text: str) -> str:
    """Remove reasoning traces and citation markers some models emit."""
    text = _THINK_BLOCK.sub("", text)
    if "</think>" in text:  # opening tag was part of the prompt template
        text = text.split("</think>", 1)[1]
    return _CITATION.sub("", text).strip()


def _visible_prefix(text: str) -> str:
    """Part of a partially streamed reply that is safe to show (no reasoning or citation markers).

    Only ever grows as more text arrives: trailing whitespace is held back until something
    follows it, because a marker that arrives next removes the space before it.
    """
    text = _CITATION.sub("", _THINK_BLOCK.sub("", text))
    for opener in ("<think>", "【"):  # hold back an unfinished reasoning block or citation marker
        if opener in text:
            text = text[: text.index(opener)]
    for i in range(1, len("<think>")):  # hold back a half-received tag
        if text.endswith("<think>"[:i]):
            text = text[:-i]
            break
    return text.rstrip()


def extract_json(raw: str) -> Any:
    """Parse the first JSON value in a model reply, tolerating fences and prose."""
    raw = clean(raw)
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    starts = [i for i in (raw.find("{"), raw.find("[")) if i != -1]
    if not starts:
        raise ValueError("No JSON found in model reply")
    value, _ = json.JSONDecoder().raw_decode(raw[min(starts):])
    return value


@dataclass
class Usage:
    seconds: float
    tokens: int | None
    model: str

    def label(self) -> str:
        short = self.model.split("/")[-1]
        tokens = f" · {self.tokens:,} tokens" if self.tokens else ""
        return f"⚡ {self.seconds:.1f}s{tokens} · {short} on Nebius"

    def __add__(self, other: "Usage") -> "Usage":
        tokens = (self.tokens or 0) + (other.tokens or 0) or None
        return Usage(self.seconds + other.seconds, tokens, self.model)


class LLM:
    """Chat-completion helper bound to one model and temperature."""

    def __init__(self, api_key: str, model: str, temperature: float = 0.3, thinking: bool = True):
        self.client = make_client(api_key)
        self.model = model
        self.temperature = temperature
        self.thinking = thinking  # False = "fast mode": ask the model to skip its reasoning phase
        self.last_usage: Usage | None = None
        self._json_mode = "json_schema"  # downgraded automatically if unsupported

    def _create(self, **kwargs: Any):
        """chat.completions.create, dropping the no-thinking switch if a model rejects it."""
        if not self.thinking:
            try:
                return self.client.chat.completions.create(**kwargs, **NO_THINKING)
            except BadRequestError as e:
                if "chat_template_kwargs" not in str(e) and "enable_thinking" not in str(e):
                    raise
                self.thinking = True  # this model has no switch; run it normally
        return self.client.chat.completions.create(**kwargs)

    def _messages(self, system: str, user: str) -> list[dict]:
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def complete(self, system: str, user: str, max_tokens: int = MAX_TOKENS, temperature: float | None = None,
                 **extra: Any) -> tuple[str, Usage]:
        start = time.perf_counter()
        response = self._create(
            model=self.model,
            temperature=self.temperature if temperature is None else temperature,
            max_tokens=max_tokens,
            messages=self._messages(system, user),
            **extra,
        )
        tokens = response.usage.total_tokens if response.usage else None
        usage = Usage(time.perf_counter() - start, tokens, self.model)
        self.last_usage = usage
        content = response.choices[0].message.content or ""
        if not content.strip() and response.choices[0].finish_reason == "length":
            raise ValueError("The model ran out of room while reasoning. Try Fast mode or a shorter document.")
        return clean(content), usage

    def stream(self, system: str, user: str, max_tokens: int = MAX_TOKENS) -> Iterator[str]:
        """Yield reply text as it arrives; sets `last_usage` when finished."""
        start = time.perf_counter()
        kwargs: dict[str, Any] = dict(
            model=self.model,
            temperature=self.temperature,
            max_tokens=max_tokens,
            messages=self._messages(system, user),
            stream=True,
        )
        try:
            response = self._create(**kwargs, stream_options={"include_usage": True})
        except BadRequestError:
            response = self._create(**kwargs)

        full, shown, tokens = "", 0, None
        for chunk in response:
            if getattr(chunk, "usage", None):
                tokens = chunk.usage.total_tokens
            if not chunk.choices:
                continue
            full += chunk.choices[0].delta.content or ""
            visible = _visible_prefix(full)
            if len(visible) > shown:
                yield visible[shown:]
                shown = len(visible)
        self.last_usage = Usage(time.perf_counter() - start, tokens, self.model)

    def complete_json(self, system: str, user: str, schema: dict, max_tokens: int = MAX_TOKENS) -> tuple[Any, Usage]:
        """Structured output: JSON-schema mode if supported, else JSON mode, else plain prompt."""
        formats = {
            "json_schema": {"type": "json_schema", "json_schema": {"name": "result", "schema": schema}},
            "json_object": {"type": "json_object"},
            "none": None,
        }
        order = list(formats)[list(formats).index(self._json_mode):]
        last_error: Exception | None = None
        for mode in order:
            extra = {"response_format": formats[mode]} if formats[mode] else {}
            try:
                text, usage = self.complete(system, user, max_tokens=max_tokens, temperature=0.1, **extra)
            except BadRequestError as e:  # this model/endpoint doesn't support the mode
                last_error = e
                continue
            self._json_mode = mode
            try:
                return extract_json(text), usage
            except (ValueError, json.JSONDecodeError) as e:
                last_error = e
        raise ValueError(f"Model did not return valid JSON: {last_error}")
