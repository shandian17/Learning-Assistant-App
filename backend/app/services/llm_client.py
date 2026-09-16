import json
import re
import time
from typing import Any, Callable

import httpx
from flask import current_app


class LLMConfigurationError(RuntimeError):
    pass


class LLMServiceError(RuntimeError):
    pass


class LLMResponseError(RuntimeError):
    pass


RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


def parse_json_content(content: str) -> Any:
    """Parse model JSON while tolerating a Markdown fence or leading `json`."""
    if not isinstance(content, str) or not content.strip():
        raise LLMResponseError("大模型返回了空内容")
    cleaned = content.strip().lstrip("\ufeff")
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        cleaned = fenced.group(1).strip()
    cleaned = re.sub(r"^json\s*:?[\r\n\t ]*", "", cleaned, count=1, flags=re.IGNORECASE)
    try:
        return json.loads(cleaned)
    except (TypeError, ValueError) as error:
        raise LLMResponseError("大模型未返回合法 JSON") from error


class LLMClient:
    """OpenAI-compatible DeepSeek chat client with bounded transient retries."""

    def __init__(self) -> None:
        config = current_app.config
        self.base_url = config["LLM_BASE_URL"].rstrip("/")
        self.api_url = f"{self.base_url}/chat/completions" if self.base_url else ""
        self.api_key = config["LLM_API_KEY"]
        self.model = config["LLM_MODEL"]
        self.timeout = config["LLM_TIMEOUT_SECONDS"]
        self.max_attempts = min(3, max(1, int(config.get("LLM_MAX_ATTEMPTS", 3))))

        missing = [
            name
            for name, value in (
                ("LLM_BASE_URL", self.base_url),
                ("LLM_API_KEY", self.api_key),
                ("LLM_MODEL", self.model),
            )
            if not value
        ]
        if missing:
            raise LLMConfigurationError(f"缺少大模型配置：{', '.join(missing)}")

    def chat(self, messages: list[dict[str, str]], **options: Any) -> dict[str, Any]:
        return self._request(messages, lambda data: data, options)

    def _request(self, messages, transform: Callable[[dict[str, Any]], Any], options: dict[str, Any]):
        payload = {"model": self.model, "messages": messages, **options}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        last_error: Exception | None = None

        for attempt in range(self.max_attempts):
            try:
                response = httpx.post(self.api_url, headers=headers, json=payload, timeout=self.timeout)
                if response.status_code in RETRYABLE_STATUS_CODES:
                    raise httpx.HTTPStatusError(
                        "transient LLM status", request=response.request, response=response
                    )
                response.raise_for_status()
                return transform(response.json())
            except httpx.HTTPStatusError as error:
                last_error = error
                if error.response.status_code not in RETRYABLE_STATUS_CODES:
                    break
            except (httpx.RequestError, ValueError, LLMResponseError) as error:
                last_error = error
            if attempt + 1 < self.max_attempts:
                time.sleep(0.5 * (2**attempt))

        raise LLMServiceError("大模型服务暂不可用或返回了无效响应") from last_error

    def chat_text(self, messages: list[dict[str, str]], **options: Any) -> str:
        return self._request(messages, self._extract_text, options)

    @staticmethod
    def _extract_text(result: dict[str, Any]) -> str:
        try:
            content = result["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise LLMResponseError("大模型响应缺少回答内容") from error
        if not isinstance(content, str) or not content.strip():
            raise LLMResponseError("大模型返回了空内容")
        return content.strip()

    def chat_json(self, messages: list[dict[str, str]], **options: Any) -> Any:
        options.setdefault("response_format", {"type": "json_object"})
        return self._request(messages, lambda data: parse_json_content(self._extract_text(data)), options)
