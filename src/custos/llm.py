"""Async client for Nemotron on build.nvidia.com or self-hosted vLLM (both OpenAI-compatible).

Everything that talks to an LLM goes through `get_llm().chat(role, ...)`:
- the role picks model + endpoint from configs/settings.yaml (compliance-checked);
- per-endpoint concurrency limits and retries on 429/5xx/timeouts;
- a sqlite response cache for deterministic calls (temperature 0 or fixed seed), so re-runs are cheap;
- JSON-schema outputs validated with pydantic, with a parse fallback and one repair retry;
- tool calls parsed into ToolCall objects;
- usage accounting per role, scoped per episode via `usage_scope()`.
Set CUSTOS_FAKE=1 (or call `set_llm(FakeLLM(...))`) for offline tests.
"""
from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import orjson
from pydantic import BaseModel, ValidationError

from .compliance import check as compliance_check
from .settings import ROOT, Settings, get_settings

THINK_RE = re.compile(r"<think>.*?</think>", re.S)
JSON_BLOCK_RE = re.compile(r"(\{.*\}|\[.*\])", re.S)


# ----------------------------------------------------------------------------- results & usage
@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    parsed: Any = None                  # pydantic model instance (or dict) when a schema was given
    usage: dict[str, int] = field(default_factory=dict)
    latency_ms: float = 0.0
    cached: bool = False
    model: str = ""
    role: str = ""


class Usage:
    def __init__(self) -> None:
        self.calls: Counter[str] = Counter()
        self.tokens: Counter[str] = Counter()
        self.latency_ms: dict[str, list[float]] = defaultdict(list)

    def add(self, role: str, r: LLMResponse) -> None:
        self.calls[role] += 1
        self.tokens[f"{role}.prompt"] += r.usage.get("prompt_tokens", 0)
        self.tokens[f"{role}.completion"] += r.usage.get("completion_tokens", 0)
        if r.cached:
            self.tokens[f"{role}.cached_calls"] += 1
        self.latency_ms[role].append(r.latency_ms)

    def snapshot(self) -> dict[str, Any]:
        return {"llm_calls": dict(self.calls), "tokens": dict(self.tokens)}


GLOBAL_USAGE = Usage()
_usage_ctx: contextvars.ContextVar[Usage | None] = contextvars.ContextVar("custos_usage", default=None)


@contextmanager
def usage_scope() -> Iterator[Usage]:
    """Collect LLM usage for one episode/request (asyncio-task safe)."""
    u = Usage()
    token = _usage_ctx.set(u)
    try:
        yield u
    finally:
        _usage_ctx.reset(token)


def _record(role: str, r: LLMResponse) -> None:
    GLOBAL_USAGE.add(role, r)
    if (u := _usage_ctx.get()) is not None:
        u.add(role, r)


# ----------------------------------------------------------------------------- cache
class ResponseCache:
    """Tiny thread-safe sqlite key/value store (WAL)."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v BLOB)")
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            row = self._db.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
        return orjson.loads(row[0]) if row else None

    def put(self, key: str, value: Any) -> None:
        blob = orjson.dumps(value)
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO kv (k, v) VALUES (?, ?)", (key, blob))


def _hash(obj: Any) -> str:
    return hashlib.sha256(orjson.dumps(obj, option=orjson.OPT_SORT_KEYS)).hexdigest()


# ----------------------------------------------------------------------------- schema helpers
def _schema_of(schema: type[BaseModel] | dict | None) -> tuple[str, dict] | None:
    if schema is None:
        return None
    if isinstance(schema, dict):
        return schema.get("title", "output"), schema
    return schema.__name__, schema.model_json_schema()


def parse_json_loose(text: str) -> Any:
    """Parse JSON from model text: strip <think>, code fences, take the outermost {...}/[...] block."""
    t = THINK_RE.sub("", text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t.strip(), flags=re.M)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        m = JSON_BLOCK_RE.search(t)
        if not m:
            raise
        return json.loads(m.group(1))


def _validate(schema: type[BaseModel] | dict, data: Any) -> Any:
    if isinstance(schema, dict):
        return data
    return schema.model_validate(data)


# ----------------------------------------------------------------------------- protocol
class LLM(Protocol):
    async def chat(
        self,
        role: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict | None = None,
        schema: type[BaseModel] | dict | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        thinking: bool | None = None,
        seed: int | None = None,
        cache: bool = True,
    ) -> LLMResponse: ...


# ----------------------------------------------------------------------------- real client
class _Retryable(Exception):
    pass


class NemotronClient:
    def __init__(self, settings: Settings | None = None):
        self.s = settings or get_settings()
        compliance_check(self.s)
        self.cache = ResponseCache(ROOT / self.s.llm["cache_path"])
        self._clients: dict[str, Any] = {}
        self._sems: dict[str, asyncio.Semaphore] = {}
        self._schema_mode: dict[str, str] = {}   # endpoint -> "response_format" | "nvext" | "prompt"
        self._rr: dict[str, int] = {}

    def _client_for(self, role: str):
        """One AsyncOpenAI per base URL; a comma-separated base_url (one vLLM server per GPU) is round-robined."""
        from openai import AsyncOpenAI

        ep = self.s.endpoint(role)
        if ep.name not in self._clients:
            urls = [u.strip() for u in ep.base_url.split(",") if u.strip()]
            self._clients[ep.name] = [AsyncOpenAI(base_url=u, api_key=ep.api_key, timeout=self.s.llm["timeout_s"],
                                                  max_retries=0) for u in urls]
            self._sems[ep.name] = asyncio.Semaphore(ep.max_concurrency * len(urls))
            self._rr[ep.name] = 0
        pool = self._clients[ep.name]
        self._rr[ep.name] = (self._rr[ep.name] + 1) % len(pool)
        return ep, pool[self._rr[ep.name]], self._sems[ep.name]

    async def _create(self, client, kwargs: dict[str, Any]):
        import openai

        delay = 1.0
        for attempt in range(int(self.s.llm["max_retries"]) + 1):
            try:
                return await client.chat.completions.create(**kwargs)
            except (openai.RateLimitError, openai.APITimeoutError, openai.APIConnectionError,
                    openai.InternalServerError) as e:
                if attempt == self.s.llm["max_retries"]:
                    raise
                await asyncio.sleep(delay + (hash((id(kwargs), attempt)) % 1000) / 1000.0)
                delay = min(delay * 2, 30.0)
                last = e  # noqa: F841
        raise RuntimeError("unreachable")

    async def chat(
        self,
        role: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict | None = None,
        schema: type[BaseModel] | dict | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        thinking: bool | None = None,
        seed: int | None = None,
        cache: bool = True,
    ) -> LLMResponse:
        import openai

        model = self.s.model(role)
        think = self.s.llm.get("thinking", False) if thinking is None else thinking
        sch = _schema_of(schema)
        req = {"model": model, "messages": messages, "tools": tools, "tool_choice": tool_choice,
               "schema": sch[1] if sch else None, "temperature": temperature, "max_tokens": max_tokens,
               "thinking": think, "seed": seed}
        key = _hash(req)
        cacheable = cache and (temperature == 0 or seed is not None)
        if cacheable and (hit := self.cache.get(key)) is not None:
            r = self._from_payload(hit, role, model, schema, cached=True)
            _record(role, r)
            return r

        ep, client, sem = self._client_for(role)
        kwargs: dict[str, Any] = {
            "model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens,
            "extra_body": {"chat_template_kwargs": {"enable_thinking": bool(think)}},
        }
        if seed is not None:
            kwargs["seed"] = seed
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"
        mode = self._schema_mode.get(ep.name, "response_format")
        if sch:
            kwargs = self._apply_schema(kwargs, sch, mode)

        t0 = time.perf_counter()
        async with sem:
            try:
                resp = await self._create(client, kwargs)
            except openai.BadRequestError:
                if not sch or mode == "prompt":
                    raise
                # endpoint rejected structured output -> fall back to prompting for JSON
                self._schema_mode[ep.name] = "prompt"
                kwargs = self._apply_schema(
                    {k: v for k, v in kwargs.items() if k != "response_format"}, sch, "prompt")
                kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": bool(think)}}
                resp = await self._create(client, kwargs)
        latency = (time.perf_counter() - t0) * 1000

        msg = resp.choices[0].message
        payload = {
            "text": THINK_RE.sub("", msg.content or "").strip(),
            "tool_calls": [
                {"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments}
                for tc in (msg.tool_calls or [])
            ],
            "usage": {"prompt_tokens": getattr(resp.usage, "prompt_tokens", 0) or 0,
                      "completion_tokens": getattr(resp.usage, "completion_tokens", 0) or 0},
            "latency_ms": latency,
        }
        try:
            r = self._from_payload(payload, role, model, schema, cached=False)
        except (ValidationError, json.JSONDecodeError, ValueError) as e:
            if not sch:
                raise
            # one repair retry: show the model its invalid output and the error
            repair = messages + [
                {"role": "assistant", "content": payload["text"][:4000]},
                {"role": "user", "content": f"That was not valid JSON for the schema ({str(e)[:300]}). "
                                            "Reply with ONLY the corrected JSON object."},
            ]
            return await self.chat(role, repair, schema=schema, temperature=temperature,
                                   max_tokens=max_tokens, thinking=think, seed=seed, cache=False)
        if cacheable:
            self.cache.put(key, payload)
        _record(role, r)
        return r

    @staticmethod
    def _apply_schema(kwargs: dict[str, Any], sch: tuple[str, dict], mode: str) -> dict[str, Any]:
        name, js = sch
        if mode == "response_format":
            kwargs["response_format"] = {"type": "json_schema",
                                         "json_schema": {"name": re.sub(r"\W", "_", name)[:60], "schema": js}}
        # always also instruct in the prompt: cheap, and makes the prompt-only fallback work
        instr = ("Respond with ONLY a JSON object (no prose, no code fences) matching this JSON schema:\n"
                 + json.dumps(js, separators=(",", ":")))
        msgs = list(kwargs["messages"])
        if msgs and msgs[0]["role"] == "system":
            msgs[0] = {**msgs[0], "content": msgs[0]["content"] + "\n\n" + instr}
        else:
            msgs.insert(0, {"role": "system", "content": instr})
        kwargs["messages"] = msgs
        return kwargs

    @staticmethod
    def _from_payload(p: dict[str, Any], role: str, model: str, schema, cached: bool) -> LLMResponse:
        calls = []
        for tc in p.get("tool_calls", []):
            args = tc["arguments"]
            if isinstance(args, str):
                args = parse_json_loose(args) if args.strip() else {}
            calls.append(ToolCall(tc["id"] or f"call_{len(calls)}", tc["name"], args))
        parsed = _validate(schema, parse_json_loose(p["text"])) if schema is not None else None
        return LLMResponse(text=p["text"], tool_calls=calls, parsed=parsed, usage=p.get("usage", {}),
                           latency_ms=0.0 if cached else p.get("latency_ms", 0.0), cached=cached,
                           model=model, role=role)


# ----------------------------------------------------------------------------- fake for tests
Handler = Callable[[list[dict[str, Any]], list[dict[str, Any]] | None, Any], Any]


class FakeLLM:
    """Deterministic offline stand-in. `handlers[role](messages, tools, schema)` returns one of:
    a str (text), a dict/list (JSON → parsed against schema), a pydantic model, or a list[ToolCall]."""

    def __init__(self, handlers: dict[str, Handler] | None = None, default: Handler | None = None):
        self.handlers = handlers or {}
        self.default = default
        self.calls: list[tuple[str, list[dict[str, Any]]]] = []

    async def chat(self, role, messages, *, tools=None, tool_choice=None, schema=None, temperature=0.0,
                   max_tokens=1024, thinking=None, seed=None, cache=True) -> LLMResponse:
        self.calls.append((role, messages))
        h = self.handlers.get(role, self.default)
        if h is None:
            raise KeyError(f"FakeLLM has no handler for role '{role}'")
        out = h(messages, tools, schema)
        if isinstance(out, list) and out and isinstance(out[0], ToolCall):
            r = LLMResponse(text="", tool_calls=out, model="fake", role=role)
        elif isinstance(out, BaseModel):
            r = LLMResponse(text=out.model_dump_json(), parsed=out, model="fake", role=role)
        elif isinstance(out, (dict, list)):
            r = LLMResponse(text=json.dumps(out), parsed=_validate(schema, out) if schema else out,
                            model="fake", role=role)
        else:
            text = str(out)
            r = LLMResponse(text=text, parsed=_validate(schema, parse_json_loose(text)) if schema else None,
                            model="fake", role=role)
        _record(role, r)
        return r


# ----------------------------------------------------------------------------- factory
_llm: LLM | None = None


def get_llm() -> LLM:
    global _llm
    if _llm is None:
        if os.getenv("CUSTOS_FAKE") == "1":
            raise RuntimeError("CUSTOS_FAKE=1: install a FakeLLM with set_llm() first")
        _llm = NemotronClient()
    return _llm


def set_llm(llm: LLM | None) -> None:
    global _llm
    _llm = llm
