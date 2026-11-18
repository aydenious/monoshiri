"""
Vercel AI SDK Data Stream Protocol Implementation

This module implements the Vercel AI SDK streaming protocol for use with
@ai-sdk/react's useChat and useCompletion hooks.

Protocol Reference:
- Uses Server-Sent Events (SSE) format
- Requires 'x-vercel-ai-ui-message-stream: v1' header
- Supports text, reasoning, sources, files, tools, data, and error parts
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.services.streaming.types import ActivityData


def generate_id() -> str:
    """Generate a unique ID for stream parts."""
    return f"msg_{uuid.uuid4().hex}"


@dataclass
class StreamContext:
    """
    Maintains context for streaming operations.
    Tracks active text and reasoning blocks.
    """

    message_id: str = field(default_factory=generate_id)
    active_text_id: str | None = None
    active_reasoning_id: str | None = None
    step_count: int = 0


class VercelStreamingService:
    """
    Implements the Vercel AI SDK Data Stream Protocol.

    This service formats messages according to the SSE-based protocol
    that the AI SDK frontend expects. All messages are formatted as:
        data: {json_object}\n\n

    Usage:
        service = VercelStreamingService()

        # Start a message
        yield service.format_message_start()

        # Stream text content
        text_id = service.generate_text_id()
        yield service.format_text_start(text_id)
        yield service.format_text_delta(text_id, "Hello, ")
        yield service.format_text_delta(text_id, "world!")
        yield service.format_text_end(text_id)

        # Finish the message
        yield service.format_finish()
        yield service.format_done()
    """

    def __init__(self):
        self.context = StreamContext()

    @staticmethod
    def _format_sse(data: Any) -> str:
        """
        Format data as a Server-Sent Event.

        Args:
            data: The data to format (will be JSON serialized if not a string)

        Returns:
            str: SSE formatted string
        """
        if isinstance(data, str):
            return f"data: {data}\n\n"
        return f"data: {json.dumps(data)}\n\n"

    @staticmethod
    def generate_text_id() -> str:
        """Generate a unique ID for a text block."""
        return f"text_{uuid.uuid4().hex}"

    @staticmethod
    def generate_reasoning_id() -> str:
        """Generate a unique ID for a reasoning block."""
        return f"reasoning_{uuid.uuid4().hex}"

    @staticmethod
    def generate_tool_call_id() -> str:
        """Generate a unique ID for a tool call."""
        return f"call_{uuid.uuid4().hex}"

    # =========================================================================
    # Message Lifecycle Parts
    # =========================================================================

    def format_message_start(self, message_id: str | None = None) -> str:
        """
        Format the start of a new message.

        Args:
            message_id: Optional custom message ID. If not provided, one is generated.

        Returns:
            str: SSE formatted message start part

        Example output:
            data: {"type":"start","messageId":"msg_abc123"}
        """
        if message_id:
            self.context.message_id = message_id
        else:
            self.context.message_id = generate_id()

        return self._format_sse({"type": "start", "messageId": self.context.message_id})

    def format_finish(self) -> str:
        """
        Format the finish message part.

        Returns:
            str: SSE formatted finish part

        Example output:
            data: {"type":"finish"}
        """
        return self._format_sse({"type": "finish"})

    def format_done(self) -> str:
        """
        Format the stream termination marker.

        This should be the last thing sent in a stream.

        Returns:
            str: SSE formatted done marker

        Example output:
            data: [DONE]
        """
        return "data: [DONE]\n\n"

    # =========================================================================
    # Text Parts (start/delta/end pattern)
    # =========================================================================

    def format_text_start(self, text_id: str | None = None) -> str:
        """
        Format the start of a text block.

        Args:
            text_id: Optional custom text block ID. If not provided, one is generated.

        Returns:
            str: SSE formatted text start part

        Example output:
            data: {"type":"text-start","id":"text_abc123"}
        """
        if text_id is None:
            text_id = self.generate_text_id()
        self.context.active_text_id = text_id
        return self._format_sse({"type": "text-start", "id": text_id})

    def format_text_delta(self, text_id: str, delta: str) -> str:
        """
        Format a text delta (incremental content).

        Args:
            text_id: The text block ID
            delta: The incremental text content

        Returns:
            str: SSE formatted text delta part

        Example output:
            data: {"type":"text-delta","id":"text_abc123","delta":"Hello"}
        """
        return self._format_sse({"type": "text-delta", "id": text_id, "delta": delta})

    def format_text_end(self, text_id: str) -> str:
        """
        Format the end of a text block.

        Args:
            text_id: The text block ID

        Returns:
            str: SSE formatted text end part

        Example output:
            data: {"type":"text-end","id":"text_abc123"}
        """
        if self.context.active_text_id == text_id:
            self.context.active_text_id = None
        return self._format_sse({"type": "text-end", "id": text_id})

    # =========================================================================
    # Reasoning Parts (start/delta/end pattern)
    # =========================================================================

    def format_reasoning_start(self, reasoning_id: str | None = None) -> str:
        """
        Format the start of a reasoning block.

        Args:
            reasoning_id: Optional custom reasoning block ID.

        Returns:
            str: SSE formatted reasoning start part

        Example output:
            data: {"type":"reasoning-start","id":"reasoning_abc123"}
        """
        if reasoning_id is None:
            reasoning_id = self.generate_reasoning_id()
        self.context.active_reasoning_id = reasoning_id
        return self._format_sse(
            {
                "type": "reasoning-start",
                "id": reasoning_id,
                "startedAt": datetime.now(UTC).isoformat(),
            }
        )

    def format_reasoning_delta(self, reasoning_id: str, delta: str) -> str:
        """
        Format a reasoning delta (incremental reasoning content).

        Args:
            reasoning_id: The reasoning block ID
            delta: The incremental reasoning content

        Returns:
            str: SSE formatted reasoning delta part

        Example output:
            data: {"type":"reasoning-delta","id":"reasoning_abc123","delta":"Let me think..."}
        """
        return self._format_sse(
            {"type": "reasoning-delta", "id": reasoning_id, "delta": delta}
        )

    def format_reasoning_end(self, reasoning_id: str) -> str:
        """
        Format the end of a reasoning block.

        Args:
            reasoning_id: The reasoning block ID

        Returns:
            str: SSE formatted reasoning end part

        Example output:
            data: {"type":"reasoning-end","id":"reasoning_abc123"}
        """
        if self.context.active_reasoning_id == reasoning_id:
            self.context.active_reasoning_id = None
        return self._format_sse(
            {
                "type": "reasoning-end",
                "id": reasoning_id,
                "completedAt": datetime.now(UTC).isoformat(),
            }
        )

    # =========================================================================
    # Source Parts
    # =========================================================================

    # =========================================================================
    # File Part
    # =========================================================================

    # =========================================================================
    # Custom Data Parts
    # =========================================================================

    def format_data(self, data_type: str, data: Any) -> str:
        """
        Format custom data with a type-specific suffix.

        The type will be prefixed with 'data-' automatically.

        Args:
            data_type: The custom data type suffix (e.g., "weather", "chart")
            data: The data payload

        Returns:
            str: SSE formatted data part

        Example output:
            data: {"type":"data-weather","data":{"location":"SF","temperature":100}}
        """
        return self._format_sse({"type": f"data-{data_type}", "data": data})

    def format_terminal_info(self, text: str, message_type: str = "info") -> str:
        """
        Format terminal info as custom data (SurfSense specific).

        Args:
            text: The terminal message text
            message_type: The message type (info, error, success, warning)

        Returns:
            str: SSE formatted terminal info data part
        """
        return self.format_data("terminal-info", {"text": text, "type": message_type})

    def format_activity(self, snapshot: ActivityData) -> str:
        """Format one canonical backend-owned activity snapshot."""
        return self.format_data("activity", snapshot)

    def format_thread_title_update(self, thread_id: int, title: str) -> str:
        """
        Format a thread title update notification (SurfSense specific).

        This is sent after the first response in a thread to update the
        auto-generated title based on the conversation content.

        Args:
            thread_id: The ID of the thread being updated
            title: The new title for the thread

        Returns:
            str: SSE formatted thread title update data part

        Example output:
            data: {"type":"data-thread-title-update","data":{"threadId":123,"title":"New Title"}}
        """
        return self.format_data(
            "thread-title-update",
            {
                "threadId": thread_id,
                "title": title,
            },
        )

    def format_interrupt_request(
        self, interrupt_value: dict[str, Any], *, interrupt_id: str | None = None
    ) -> str:
        """Format an interrupt request for human-in-the-loop approval.

        Args:
            interrupt_value: The interrupt payload from either:
                - interrupt_on config: {action_requests: [...], review_configs: [...]}
                - interrupt() primitive: {type: "...", message: "...", action: {...}, context: {...}}
            interrupt_id: langgraph ``Interrupt.id``. The only stable handle for
                parent-side interrupts (doom-loop, permission asks) that carry no
                ``tool_call_id``; the frontend uses it to render and resume them.

        Returns:
            str: SSE formatted interrupt request data part
        """
        normalized_payload = self._normalize_interrupt_payload(interrupt_value)
        if interrupt_id is not None:
            normalized_payload = {**normalized_payload, "interrupt_id": interrupt_id}
        return self.format_data("interrupt-request", normalized_payload)

    def _normalize_interrupt_payload(
        self, interrupt_value: dict[str, Any]
    ) -> dict[str, Any]:
        """Normalize interrupt payloads from different sources into a consistent format.

        Handles two interrupt sources:
        1. interrupt_on config (Deep Agent built-in): Already has action_requests/review_configs
        2. interrupt() primitive (custom tool code): Has type/action/context (message is optional)

        Args:
            interrupt_value: Raw interrupt payload from Deep Agent

        Returns:
            dict: Normalized payload with action_requests, review_configs, and optional context/message
        """
        if interrupt_value.get("type") == "structured_question":
            return dict(interrupt_value)
        if "action_requests" in interrupt_value and "review_configs" in interrupt_value:
            return interrupt_value

        interrupt_type = interrupt_value.get("type", "unknown")
        message = interrupt_value.get("message")
        action = interrupt_value.get("action", {})
        context = interrupt_value.get("context", {})

        normalized = {
            "action_requests": [
                {
                    "name": action.get("tool", "unknown_tool"),
                    "args": action.get("params", {}),
                }
            ],
            "review_configs": [
                {
                    "action_name": action.get("tool", "unknown_tool"),
                    "allowed_decisions": ["approve", "edit", "reject"],
                }
            ],
            "interrupt_type": interrupt_type,
            "context": context,
        }
        if message:
            normalized["message"] = message
        return normalized

    # =========================================================================
    # Error Part
    # =========================================================================

    def format_error(
        self,
        message: str,
        error_code: str | None = None,
        diagnostic: str | None = None,
        extra: dict[str, object] | None = None,
    ) -> str:
        """
        Format an error message.

        Args:
            message: Display-ready error message
            error_code: Optional machine-readable error code for frontend branching
            diagnostic: Optional raw diagnostic text; clients must not render it

        Returns:
            str: SSE formatted error part

        Example output:
            data: {"type":"error","message":"Something went wrong","errorCode":"SOME_CODE"}
        """
        payload: dict[str, object] = dict(extra or {})
        payload.update({"type": "error", "message": message})
        if error_code:
            payload["errorCode"] = error_code
        if diagnostic:
            payload["diagnostic"] = diagnostic
        return self._format_sse(payload)

    # =========================================================================
    # Tool Parts
    # =========================================================================

    def format_tool_input_start(
        self,
        tool_call_id: str,
        tool_name: str,
        *,
        langchain_tool_call_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """
        Format the start of tool input streaming.

        Args:
            tool_call_id: The unique tool call identifier. May be EITHER the
                synthetic ``call_<run_id>`` id derived from LangGraph
                ``run_id`` (unmatched chunk fallback when no ``index`` was
                registered) OR the authoritative LangChain ``tool_call.id``
                (when the provider streams ``tool_call_chunks`` we register
                the ``index`` and reuse the lc-id as the card id so live
                ``tool-input-delta`` events route without a downstream join).
                Either way, the same id is preserved across
                ``tool-input-start`` / ``-delta`` / ``-available`` /
                ``tool-output-available`` for one call.
            tool_name: The name of the tool being called.
            langchain_tool_call_id: Optional authoritative LangChain
                ``tool_call.id``. When set, surfaces as
                ``langchainToolCallId`` so the frontend can join this card
                to the action-log row written by ``ActionLogMiddleware``.

        Returns:
            str: SSE formatted tool input start part

        Example output:
            data: {"type":"tool-input-start","toolCallId":"call_abc123","toolName":"getWeather"}
        """
        payload: dict[str, Any] = {
            "type": "tool-input-start",
            "toolCallId": tool_call_id,
            "toolName": tool_name,
        }
        if langchain_tool_call_id:
            payload["langchainToolCallId"] = langchain_tool_call_id
        if metadata:
            payload["metadata"] = metadata
        return self._format_sse(payload)

    def format_tool_input_delta(self, tool_call_id: str, input_text_delta: str) -> str:
        """
        Format incremental tool input.

        Args:
            tool_call_id: The tool call identifier
            input_text_delta: The incremental input text

        Returns:
            str: SSE formatted tool input delta part

        Example output:
            data: {"type":"tool-input-delta","toolCallId":"call_abc123","inputTextDelta":"San Fran"}
        """
        return self._format_sse(
            {
                "type": "tool-input-delta",
                "toolCallId": tool_call_id,
                "inputTextDelta": input_text_delta,
            }
        )

    def format_tool_input_available(
        self,
        tool_call_id: str,
        tool_name: str,
        input_data: dict[str, Any],
        *,
        langchain_tool_call_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """
        Format the completion of tool input.

        Args:
            tool_call_id: The tool call identifier
            tool_name: The name of the tool
            input_data: The complete tool input parameters
            langchain_tool_call_id: Optional authoritative LangChain
                ``tool_call.id`` (see ``format_tool_input_start``).

        Returns:
            str: SSE formatted tool input available part

        Example output:
            data: {"type":"tool-input-available","toolCallId":"call_abc123","toolName":"getWeather","input":{"city":"SF"}}
        """
        payload: dict[str, Any] = {
            "type": "tool-input-available",
            "toolCallId": tool_call_id,
            "toolName": tool_name,
            "input": input_data,
        }
        if langchain_tool_call_id:
            payload["langchainToolCallId"] = langchain_tool_call_id
        if metadata:
            payload["metadata"] = metadata
        return self._format_sse(payload)

    def format_tool_output_available(
        self,
        tool_call_id: str,
        output: Any,
        *,
        langchain_tool_call_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """
        Format tool execution output.

        Args:
            tool_call_id: The tool call identifier
            output: The tool execution result
            langchain_tool_call_id: Optional authoritative LangChain
                ``tool_call.id`` extracted from ``ToolMessage.tool_call_id``.
                When set, the frontend can backfill any card whose
                ``langchainToolCallId`` was not yet known at
                ``tool-input-start`` time.

        Returns:
            str: SSE formatted tool output available part

        Example output:
            data: {"type":"tool-output-available","toolCallId":"call_abc123","output":{"weather":"sunny"}}
        """
        payload: dict[str, Any] = {
            "type": "tool-output-available",
            "toolCallId": tool_call_id,
            "output": output,
        }
        if langchain_tool_call_id:
            payload["langchainToolCallId"] = langchain_tool_call_id
        if metadata:
            payload["metadata"] = metadata
        return self._format_sse(payload)

    # =========================================================================
    # Step Parts
    # =========================================================================

    def format_start_step(self) -> str:
        """
        Format the start of a step (one LLM API call).

        Returns:
            str: SSE formatted start step part

        Example output:
            data: {"type":"start-step"}
        """
        self.context.step_count += 1
        return self._format_sse({"type": "start-step"})

    def format_finish_step(self) -> str:
        """
        Format the completion of a step.

        This is necessary for correctly processing multiple stitched
        assistant calls, e.g., when calling tools in the backend.

        Returns:
            str: SSE formatted finish step part

        Example output:
            data: {"type":"finish-step"}
        """
        return self._format_sse({"type": "finish-step"})

    # =========================================================================
    # Convenience Methods
    # =========================================================================

    def reset(self) -> None:
        """Reset the streaming context for a new message."""
        self.context = StreamContext()
