"""A small Ollama client. The only place in the bench that touches the network.

Everything above it takes a `complete(prompt) -> Completion` callable, so agents are
testable against a scripted model and the bench never needs a server to be exercised.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

DEFAULT_HOST = "http://localhost:11434"

#: Small models truncate long answers and then emit invalid JSON, so the ceiling is
#: generous while the prompts keep the answers short.
DEFAULT_NUM_PREDICT = 1200
DEFAULT_TIMEOUT = 300.0


@dataclass(slots=True)
class Completion:
    text: str
    seconds: float = 0.0
    tokens: int = 0
    error: str | None = None


class OllamaClient:
    def __init__(
        self,
        model: str,
        *,
        host: str = DEFAULT_HOST,
        temperature: float = 0.0,
        num_predict: int = DEFAULT_NUM_PREDICT,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.model = model
        self.host = host.rstrip("/")
        self.temperature = temperature
        self.num_predict = num_predict
        self.timeout = timeout

    def __call__(self, prompt: str) -> Completion:
        payload = json.dumps({
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            # Ollama's JSON mode; it does not guarantee a parse, only biases towards one.
            "format": "json",
            "options": {
                "temperature": self.temperature,
                "num_predict": self.num_predict,
                # Same seed every run, so a benchmark can be replayed.
                "seed": 42,
            },
        }).encode()
        request = urllib.request.Request(
            f"{self.host}/api/generate", payload, {"Content-Type": "application/json"}
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read())
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            return Completion("", time.monotonic() - started, 0, f"{type(error).__name__}: {error}")
        return Completion(
            text=body.get("response", ""),
            seconds=time.monotonic() - started,
            tokens=body.get("eval_count", 0) or 0,
        )


def available_models(host: str = DEFAULT_HOST, timeout: float = 5.0) -> list[str]:
    """Models the local server has pulled, or an empty list when it is not running."""
    try:
        with urllib.request.urlopen(f"{host.rstrip('/')}/api/tags", timeout=timeout) as response:
            body = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return []
    return sorted(model["name"] for model in body.get("models", []))
