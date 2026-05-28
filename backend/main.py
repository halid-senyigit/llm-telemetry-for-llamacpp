from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse


BACKEND_PORT = int(os.getenv("BACKEND_PORT", "7171"))
LLAMA_METRICS_URL = os.getenv("LLAMA_METRICS_URL", "http://localhost:6688/metrics")
POLL_INTERVAL_SECONDS = float(os.getenv("POLL_INTERVAL_SECONDS", "0.1"))
GPU_POLL_INTERVAL_SECONDS = float(os.getenv("GPU_POLL_INTERVAL_SECONDS", "1"))

METRIC_NAME_RE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{[^{}]*\})?\s+([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)$")


@dataclass
class TelemetryState:
    updated_at: float = field(default_factory=time.time)
    llama: dict[str, Any] = field(default_factory=dict)
    gpu: dict[str, Any] = field(default_factory=dict)
    status: str = "Offline"


state = TelemetryState()
subscribers: set[asyncio.Queue[str]] = set()
slot_samples: dict[int, tuple[int, int, float]] = {}
slot_context: dict[int, tuple[int, int, int]] = {}
last_prompt_tokens_total = 0
current_speed_window: list[float] = []
generation_speed_samples: list[float] = []
generation_is_active = False
last_generation_average = 0.0
last_gpu_poll = 0.0
gpu_cache: dict[str, Any] | None = None
last_props_poll = 0.0
model_name_cache = os.getenv("MODEL_NAME", "llama.cpp model")
app = FastAPI(title="LLMBrief.local telemetry", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


def parse_prometheus_metrics(text: str) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = METRIC_NAME_RE.match(line)
        if not match:
            continue
        name, value = match.groups()
        try:
            metrics[name] = float(value)
        except ValueError:
            continue
    return metrics


def first_metric(metrics: dict[str, float], candidates: list[str], default: float = 0.0) -> float:
    for candidate in candidates:
        if candidate in metrics:
            return metrics[candidate]
    return default


def average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def remember_distinct_speed(speed: float) -> float:
    rounded = round(speed, 2)
    if rounded <= 0:
        return average(current_speed_window)
    if not current_speed_window or current_speed_window[-1] != rounded:
        current_speed_window.append(rounded)
    del current_speed_window[:-4]
    return average(current_speed_window)


def session_average(active: bool, speed: float, fallback: float) -> float:
    global generation_is_active, last_generation_average

    if active and not generation_is_active:
        generation_speed_samples.clear()
        generation_is_active = True

    if active:
        if speed > 0:
            generation_speed_samples.append(round(speed, 2))
        return average(generation_speed_samples) or fallback

    if generation_is_active:
        last_generation_average = average(generation_speed_samples) or fallback
        generation_speed_samples.clear()
        generation_is_active = False

    return last_generation_average or fallback


def slots_url() -> str:
    return LLAMA_METRICS_URL.rsplit("/", 1)[0] + "/slots"


def props_url() -> str:
    return LLAMA_METRICS_URL.rsplit("/", 1)[0] + "/props"


def clean_model_name(value: str) -> str:
    name = value.replace("\\", "/").rsplit("/", 1)[-1]
    return name.removesuffix(".gguf") or value


def decoded_tokens(slot: dict[str, Any]) -> int:
    next_token = slot.get("next_token", [])
    if isinstance(next_token, list):
        return sum(int(item.get("n_decoded", 0)) for item in next_token if isinstance(item, dict))
    if isinstance(next_token, dict):
        return int(next_token.get("n_decoded", 0))
    return 0


def normalize_slots(
    slots: list[dict[str, Any]],
    ok: bool,
    prompt_tokens_total: int = 0,
    error: str | None = None,
) -> dict[str, Any]:
    global last_prompt_tokens_total

    now = time.time()
    live_tokens_per_second = 0.0
    active_slots = 0
    total_context = 0
    used_context = 0
    decoded_total = 0

    for slot in slots:
        slot_id = int(slot.get("id", 0))
        task_id = int(slot.get("id_task", -1))
        decoded = decoded_tokens(slot)
        total_context += int(slot.get("n_ctx", 0))
        decoded_total += decoded

        previous_context = slot_context.get(slot_id)
        if previous_context is None or previous_context[0] != task_id:
            prompt_for_task = max(0, prompt_tokens_total - last_prompt_tokens_total)
            if prompt_for_task == 0 and prompt_tokens_total > 0 and not slot_context:
                prompt_for_task = prompt_tokens_total
            prompt_baseline = max(0, prompt_tokens_total - prompt_for_task)
        else:
            prompt_baseline = previous_context[1]
            prompt_for_task = max(previous_context[2], prompt_tokens_total - prompt_baseline)

        slot_context[slot_id] = (task_id, prompt_baseline, prompt_for_task)
        used_context += prompt_for_task + decoded

        if slot.get("is_processing"):
            active_slots += 1
            previous = slot_samples.get(slot_id)
            if previous and previous[0] == task_id:
                previous_decoded, previous_time = previous[1], previous[2]
                elapsed = max(now - previous_time, 0.001)
                live_tokens_per_second += max(0, decoded - previous_decoded) / elapsed

        slot_samples[slot_id] = (task_id, decoded, now)

    last_prompt_tokens_total = max(last_prompt_tokens_total, prompt_tokens_total)

    return {
        "ok": ok,
        "error": error,
        "activeSlots": active_slots,
        "liveGenerationSpeed": round(live_tokens_per_second, 2),
        "decodedTokens": decoded_total,
        "contextUsed": used_context,
        "contextTotal": total_context,
        "raw": slots,
    }


def normalize_llama_metrics(
    metrics: dict[str, float],
    slots: dict[str, Any],
    ok: bool,
    error: str | None = None,
) -> dict[str, Any]:
    prompt_tokens = first_metric(metrics, ["llamacpp:prompt_tokens_total", "llama_prompt_tokens_total"])
    predicted_tokens = first_metric(metrics, ["llamacpp:tokens_predicted_total", "llama_tokens_predicted_total"])
    requests_processing = first_metric(metrics, ["llamacpp:requests_processing", "llama_requests_processing"])
    requests_deferred = first_metric(metrics, ["llamacpp:requests_deferred", "llama_requests_deferred"])
    current_speed = first_metric(
        metrics,
        [
            "llamacpp:predicted_tokens_seconds",
            "llamacpp:tokens_predicted_seconds",
            "llamacpp:generation_tokens_per_second",
            "llama_generation_tokens_per_second",
        ],
    )
    predicted_seconds = first_metric(metrics, ["llamacpp:tokens_predicted_seconds_total"])
    lifetime_avg_speed = predicted_tokens / predicted_seconds if predicted_seconds > 0 else current_speed
    live_speed = slots.get("liveGenerationSpeed", 0)
    active_slots = slots.get("activeSlots", 0)
    if active_slots:
        display_current_speed = remember_distinct_speed(live_speed)
    else:
        current_speed_window.clear()
        display_current_speed = current_speed
    avg_speed = session_average(active_slots > 0, live_speed, lifetime_avg_speed)
    prompt_speed = first_metric(
        metrics,
        [
            "llamacpp:prompt_tokens_seconds",
            "llamacpp:prompt_tokens_per_second",
            "llama_prompt_tokens_per_second",
        ],
    )
    context_used = first_metric(metrics, ["llamacpp:kv_cache_tokens", "llama_context_tokens"]) or slots.get("contextUsed", 0)
    context_total = first_metric(metrics, ["llamacpp:context_size", "llama_context_size"])

    return {
        "ok": ok,
        "source": LLAMA_METRICS_URL,
        "error": error,
        "model": model_name_cache,
        "currentGenerationSpeed": round(display_current_speed, 2),
        "averageGenerationSpeed": round(avg_speed, 2),
        "totalGenerationTokens": int(predicted_tokens),
        "promptSpeed": round(prompt_speed, 2),
        "contextUsed": int(context_used),
        "contextTotal": int(context_total or slots.get("contextTotal", 0)),
        "requestsProcessing": int(requests_processing or active_slots),
        "requestsQueued": int(requests_deferred),
        "slots": slots,
        "raw": metrics,
    }


async def fetch_llama_metrics() -> tuple[dict[str, float], bool, str | None]:
    try:
        async with httpx.AsyncClient(timeout=0.7) as client:
            response = await client.get(LLAMA_METRICS_URL)
            response.raise_for_status()
        return parse_prometheus_metrics(response.text), True, None
    except Exception as exc:
        message = str(exc) or exc.__class__.__name__
        return {}, False, message


async def refresh_model_name(force: bool = False) -> None:
    global last_props_poll, model_name_cache

    if os.getenv("MODEL_NAME"):
        model_name_cache = os.getenv("MODEL_NAME", model_name_cache)
        return

    now = time.time()
    if not force and now - last_props_poll < 5:
        return
    last_props_poll = now

    try:
        async with httpx.AsyncClient(timeout=0.7) as client:
            response = await client.get(props_url())
            response.raise_for_status()
        payload = response.json()
        model = payload.get("model_alias") or payload.get("model_path")
        if isinstance(model, str) and model.strip():
            model_name_cache = clean_model_name(model.strip())
    except Exception:
        pass


async def fetch_llama_slots() -> dict[str, Any]:
    return await fetch_llama_slots_with_prompt_total(0)


async def fetch_llama_slots_with_prompt_total(prompt_tokens_total: int) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=0.7) as client:
            response = await client.get(slots_url())
            response.raise_for_status()
        payload = response.json()
        return normalize_slots(payload if isinstance(payload, list) else [], True, prompt_tokens_total)
    except Exception as exc:
        message = str(exc) or exc.__class__.__name__
        return normalize_slots([], False, prompt_tokens_total, message)


async def fetch_llama() -> dict[str, Any]:
    await refresh_model_name()
    metrics = await fetch_llama_metrics()
    metric_values, metrics_ok, metrics_error = metrics
    prompt_tokens_total = int(first_metric(metric_values, ["llamacpp:prompt_tokens_total", "llama_prompt_tokens_total"]))
    slots = await fetch_llama_slots_with_prompt_total(prompt_tokens_total)
    ok = metrics_ok or slots.get("ok", False)
    error = metrics_error if not metrics_ok else slots.get("error")
    return normalize_llama_metrics(metric_values, slots, ok, error)


def parse_float(value: str) -> float | None:
    try:
        return float(value.strip())
    except (TypeError, ValueError):
        return None


async def fetch_gpu() -> dict[str, Any]:
    query = (
        "name,utilization.gpu,memory.used,memory.total,temperature.gpu,"
        "power.draw,power.limit,clocks.gr,clocks.mem,pstate"
    )
    command = [
        "nvidia-smi",
        f"--query-gpu={query}",
        "--format=csv,noheader,nounits",
    ]
    try:
        completed = await asyncio.to_thread(
            subprocess.run,
            command,
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or "nvidia-smi failed")
        line = completed.stdout.strip().splitlines()[0]
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 10:
            raise RuntimeError("unexpected nvidia-smi output")
        name, util, mem_used, mem_total, temp, power, power_limit, core_clock, mem_clock, pstate = parts[:10]
        return {
            "ok": True,
            "error": None,
            "name": name,
            "utilization": parse_float(util) or 0,
            "vramUsed": parse_float(mem_used) or 0,
            "vramTotal": parse_float(mem_total) or 0,
            "temperature": parse_float(temp) or 0,
            "powerDraw": parse_float(power) or 0,
            "powerLimit": parse_float(power_limit) or 0,
            "coreClock": parse_float(core_clock) or 0,
            "memoryClock": parse_float(mem_clock) or 0,
            "pstate": pstate or "N/A",
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "name": "NVIDIA GPU unavailable",
            "utilization": 0,
            "vramUsed": 0,
            "vramTotal": 0,
            "temperature": 0,
            "powerDraw": 0,
            "powerLimit": 0,
            "coreClock": 0,
            "memoryClock": 0,
            "pstate": "N/A",
        }


def compute_status(llama: dict[str, Any]) -> str:
    if not llama.get("ok"):
        return "Offline"
    if llama.get("requestsProcessing", 0) > 0:
        return "Generating"
    return "Idle"


def public_state() -> dict[str, Any]:
    return {
        "updatedAt": state.updated_at,
        "status": state.status,
        "llama": state.llama,
        "gpu": state.gpu,
        "config": {
            "backendPort": BACKEND_PORT,
            "llamaMetricsUrl": LLAMA_METRICS_URL,
            "pollIntervalSeconds": POLL_INTERVAL_SECONDS,
            "gpuPollIntervalSeconds": GPU_POLL_INTERVAL_SECONDS,
        },
    }


async def publish(payload: dict[str, Any]) -> None:
    message = f"data: {json.dumps(payload)}\n\n"
    stale: list[asyncio.Queue[str]] = []
    for queue in subscribers:
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            stale.append(queue)
    for queue in stale:
        subscribers.discard(queue)


async def poll_loop() -> None:
    global gpu_cache, last_gpu_poll

    while True:
        now = time.time()
        if gpu_cache is None or now - last_gpu_poll >= GPU_POLL_INTERVAL_SECONDS:
            llama, gpu = await asyncio.gather(fetch_llama(), fetch_gpu())
            gpu_cache = gpu
            last_gpu_poll = now
        else:
            llama = await fetch_llama()
            gpu = gpu_cache
        state.updated_at = time.time()
        state.llama = llama
        state.gpu = gpu
        state.status = compute_status(llama)
        await publish(public_state())
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


@app.on_event("startup")
async def startup() -> None:
    await refresh_model_name(force=True)
    asyncio.create_task(poll_loop())


@app.get("/api/status")
async def api_status() -> dict[str, Any]:
    return public_state()


@app.get("/api/events")
async def api_events() -> StreamingResponse:
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=4)
    subscribers.add(queue)

    async def stream():
        yield f"data: {json.dumps(public_state())}\n\n"
        try:
            while True:
                yield await queue.get()
        finally:
            subscribers.discard(queue)

    return StreamingResponse(stream(), media_type="text/event-stream")
