"""Agent trajectory logging.

Every tool call, every agent decision, and every token count is appended to a
JSONL file under ``trajectories/``. This is what makes an agent run auditable
after the fact — for the operator, and for the evaluation harness.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import TRAJECTORY_DIR


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


class TrajectoryLogger:
    """Append-only trajectory for one run."""

    def __init__(self, run_id: str, scenario_id: str, directory: Path | None = None) -> None:
        self.run_id = run_id
        self.scenario_id = scenario_id
        self.directory = directory or TRAJECTORY_DIR
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / f"{run_id}.jsonl"
        self.records: list[dict] = []
        self._step = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    def _write(self, record: dict) -> None:
        self._step += 1
        record = {
            "run_id": self.run_id,
            "scenario_id": self.scenario_id,
            "step_number": self._step,
            "timestamp": datetime.now(UTC).isoformat(),
            **record,
        }
        self.records.append(record)
        with self.path.open("a") as handle:
            handle.write(json.dumps(_jsonable(record)) + "\n")

    def log_tool(
        self,
        agent: str,
        tool: str,
        arguments: dict,
        result: Any,
        latency_ms: float,
        decision: str | None = None,
        reason_code: str | None = None,
    ) -> None:
        self._write(
            {
                "kind": "tool_call",
                "agent_name": agent,
                "tool_name": tool,
                "tool_arguments": arguments,
                "tool_result": result,
                "decision": decision,
                "reason_code": reason_code,
                "latency_ms": round(latency_ms, 1),
            }
        )

    def log_agent(
        self,
        agent: str,
        output: Any,
        latency_ms: float,
        input_tokens: int = 0,
        output_tokens: int = 0,
        retry_count: int = 0,
        error: str | None = None,
    ) -> None:
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self._write(
            {
                "kind": "agent_output",
                "agent_name": agent,
                "output": output,
                "latency_ms": round(latency_ms, 1),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "retry_count": retry_count,
                "error": error,
            }
        )

    def log_prompt(self, agent: str, prompt: str) -> None:
        """Record what the agent was actually asked.

        A trajectory that starts at the first tool call is missing its premise;
        this makes each one followable from the instructions to the result.
        """
        self._write({"kind": "agent_prompt", "agent_name": agent, "prompt": prompt})

    def log_event(self, source: str, event: str, **detail: Any) -> None:
        self._write({"kind": "event", "agent_name": source, "event": event, "detail": detail})

    def timer(self) -> "Timer":
        return Timer()

    def summary(self) -> dict:
        tool_calls = [r for r in self.records if r["kind"] == "tool_call"]
        agent_calls = [r for r in self.records if r["kind"] == "agent_output"]
        return {
            "run_id": self.run_id,
            "scenario_id": self.scenario_id,
            "steps": self._step,
            "tool_calls": len(tool_calls),
            "agent_calls": len(agent_calls),
            "input_tokens": self.total_input_tokens,
            "output_tokens": self.total_output_tokens,
            "agent_latency_ms": round(sum(r["latency_ms"] for r in agent_calls), 1),
        }


class Timer:
    """Millisecond stopwatch used around tool and agent calls."""

    def __enter__(self) -> "Timer":
        self._start = time.perf_counter()
        self.ms = 0.0
        return self

    def __exit__(self, *exc: object) -> None:
        self.ms = (time.perf_counter() - self._start) * 1000.0
