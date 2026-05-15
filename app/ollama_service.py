from dataclasses import dataclass
import base64
import hashlib
import math
import re
from typing import Any

import httpx
from ollama import Client

from app.config import Settings, get_settings


def _read(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _normalize_host(host: str) -> str:
    if host.startswith(("http://", "https://")):
        normalized = host.rstrip("/")
    else:
        normalized = f"http://{host.rstrip('/')}"

    return normalized.replace("://0.0.0.0", "://127.0.0.1")


@dataclass
class ModelReply:
    model: str
    content: str
    prompt_tokens: int
    output_tokens: int
    total_duration_ms: float
    eval_duration_ms: float
    tokens_per_second: float
    fallback: bool = False
    error: str = ""


class OllamaService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.host = _normalize_host(self.settings.ollama_host)
        self.client = Client(host=self.host)

    def list_models(self) -> dict[str, Any]:
        try:
            response = self.client.list()
            models = _read(response, "models", [])
            names = [_read(model, "model", _read(model, "name", "")) for model in models]
            return {"available": True, "models": [name for name in names if name]}
        except Exception as client_exc:
            try:
                response = httpx.get(
                    f"{self.host}/api/tags",
                    timeout=5,
                    trust_env=False,
                )
                response.raise_for_status()
                models = response.json().get("models", [])
                names = [model.get("model") or model.get("name") for model in models]
                return {"available": True, "models": [name for name in names if name]}
            except Exception as http_exc:
                return {
                    "available": False,
                    "models": [],
                    "error": f"client={client_exc}; http={http_exc}",
                }

    def embed_text(self, text: str) -> list[float]:
        dimensions = self.settings.semantic_cache_embedding_dimensions
        if self.settings.fake_llm:
            return self._fake_embedding(text, dimensions)

        response = self.client.embed(
            model=self.settings.ollama_embedding_model,
            input=text,
            dimensions=dimensions,
        )
        embeddings = _read(response, "embeddings", [])
        if not embeddings:
            return []
        embedding = [float(value) for value in embeddings[0]]
        if len(embedding) != dimensions:
            raise ValueError(
                f"Embedding dimension mismatch: expected {dimensions}, received {len(embedding)}."
            )
        return embedding

    def chat(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        think: bool | str | None = None,
        temperature: float = 0.2,
        num_predict: int = 320,
        num_ctx: int | None = None,
    ) -> ModelReply:
        if self.settings.fake_llm:
            return self._fake_reply(model, system_prompt, user_prompt)

        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "options": {"temperature": temperature, "num_predict": num_predict},
        }
        if num_ctx:
            payload["options"]["num_ctx"] = num_ctx
        if think is not None:
            payload["think"] = think

        try:
            response = self.client.chat(**payload)
            message = _read(response, "message", {})
            content = _read(message, "content", "") or _read(message, "thinking", "")
            prompt_tokens = int(_read(response, "prompt_eval_count", 0) or 0)
            output_tokens = int(_read(response, "eval_count", 0) or 0)
            total_duration_ms = float(_read(response, "total_duration", 0) or 0) / 1_000_000
            eval_duration_ms = float(_read(response, "eval_duration", 0) or 0) / 1_000_000
            tokens_per_second = 0.0
            if output_tokens and eval_duration_ms:
                tokens_per_second = output_tokens / (eval_duration_ms / 1000)

            return ModelReply(
                model=model,
                content=content.strip(),
                prompt_tokens=prompt_tokens,
                output_tokens=output_tokens,
                total_duration_ms=round(total_duration_ms, 2),
                eval_duration_ms=round(eval_duration_ms, 2),
                tokens_per_second=round(tokens_per_second, 2),
            )
        except Exception as exc:
            fallback = self._fake_reply(model, system_prompt, user_prompt)
            fallback.fallback = True
            fallback.error = str(exc)
            return fallback

    def describe_image(
        self,
        *,
        model: str,
        image_bytes: bytes,
        system_prompt: str,
        user_prompt: str,
        num_predict: int = 220,
    ) -> ModelReply:
        if self.settings.fake_llm:
            return self._fake_reply(model, system_prompt, "Image attachment: " + user_prompt)

        image_payload = base64.b64encode(image_bytes).decode("utf-8")
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt, "images": [image_payload]},
            ],
            "stream": False,
            "options": {"temperature": 0.2, "num_predict": num_predict},
        }

        try:
            response = self.client.chat(**payload)
            message = _read(response, "message", {})
            content = _read(message, "content", "") or _read(message, "thinking", "")
            prompt_tokens = int(_read(response, "prompt_eval_count", 0) or 0)
            output_tokens = int(_read(response, "eval_count", 0) or 0)
            total_duration_ms = float(_read(response, "total_duration", 0) or 0) / 1_000_000
            eval_duration_ms = float(_read(response, "eval_duration", 0) or 0) / 1_000_000
            tokens_per_second = 0.0
            if output_tokens and eval_duration_ms:
                tokens_per_second = output_tokens / (eval_duration_ms / 1000)

            return ModelReply(
                model=model,
                content=content.strip(),
                prompt_tokens=prompt_tokens,
                output_tokens=output_tokens,
                total_duration_ms=round(total_duration_ms, 2),
                eval_duration_ms=round(eval_duration_ms, 2),
                tokens_per_second=round(tokens_per_second, 2),
            )
        except Exception as exc:
            fallback = self._fake_reply(model, system_prompt, "Image attachment: " + user_prompt)
            fallback.fallback = True
            fallback.error = str(exc)
            return fallback

    def _fake_reply(self, model: str, system_prompt: str, user_prompt: str) -> ModelReply:
        role = system_prompt.split(".")[0].strip() or "Agent"
        if "final response" in system_prompt.lower() or "telegram user" in system_prompt.lower():
            content = "Here is a concise answer based on the completed agent work."
        else:
            content = f"{role}: {user_prompt[:220].strip()}"
        prompt_tokens = max(1, len(user_prompt.split()))
        output_tokens = max(1, len(content.split()))
        return ModelReply(
            model=model,
            content=content,
            prompt_tokens=prompt_tokens,
            output_tokens=output_tokens,
            total_duration_ms=1.0,
            eval_duration_ms=1.0,
            tokens_per_second=float(output_tokens),
            fallback=self.settings.fake_llm,
        )

    def _fake_embedding(self, text: str, dimensions: int) -> list[float]:
        vector = [0.0] * dimensions
        tokens = re.findall(r"[a-z0-9]+", text.lower()) or [text.lower()]
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % dimensions
            vector[index] += 1.0

        norm = math.sqrt(sum(value * value for value in vector))
        if not norm:
            return vector
        return [round(value / norm, 8) for value in vector]


ollama_service = OllamaService()
