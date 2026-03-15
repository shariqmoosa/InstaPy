"""
Unified AI model client — wraps Claude (Anthropic), OpenAI, and Google Gemini
behind a single interface so the shopping agent is not locked to one provider.

Usage:
    from shopping_agent.models import get_model_client

    # Claude
    client = get_model_client("claude", api_key="sk-ant-...")

    # OpenAI
    client = get_model_client("openai", api_key="sk-...", model="gpt-4o")

    # Gemini
    client = get_model_client("gemini", api_key="AIza...", model="gemini-1.5-flash")

    # Auto-detect from env vars
    client = get_model_client("auto")

All clients expose:
    response = client.chat(messages, tools, system_prompt)
    response.content        -> list of blocks: {type, text} or {type, tool_use, id, name, input}
    response.stop_reason    -> "end_turn" | "tool_use"
    response.input_tokens   -> int
    response.output_tokens  -> int
    response.cache_hit_tokens -> int (0 if provider doesn't support caching)
"""
import json
import os
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Normalised response type
# ---------------------------------------------------------------------------

@dataclass
class ModelResponse:
    content: list          # list of dicts: {type:text, text:str} or {type:tool_use, id, name, input}
    stop_reason: str       # "end_turn" or "tool_use"
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit_tokens: int = 0

    def text(self):
        """Return concatenated text from all text blocks."""
        return " ".join(b["text"] for b in self.content if b.get("type") == "text")

    def tool_calls(self):
        """Return list of tool_use blocks."""
        return [b for b in self.content if b.get("type") == "tool_use"]


# ---------------------------------------------------------------------------
# Tool schema conversion helpers
# ---------------------------------------------------------------------------

def _to_openai_tools(schemas):
    """Convert Anthropic-style tool schemas to OpenAI function format."""
    return [
        {
            "type": "function",
            "function": {
                "name": s["name"],
                "description": s.get("description", ""),
                "parameters": s.get("input_schema", {"type": "object", "properties": {}}),
            },
        }
        for s in schemas
    ]


def _to_gemini_tools(schemas):
    """Convert Anthropic-style tool schemas to Gemini FunctionDeclaration format."""
    try:
        from google.generativeai.types import FunctionDeclaration, Tool
    except ImportError:
        raise ImportError("google-generativeai is required: pip install google-generativeai")

    declarations = []
    for s in schemas:
        input_schema = s.get("input_schema", {})
        props = input_schema.get("properties", {})
        required = input_schema.get("required", [])
        parameters = {
            "type": "object",
            "properties": {
                k: {
                    "type": v.get("type", "string"),
                    "description": v.get("description", ""),
                }
                for k, v in props.items()
            },
        }
        if required:
            parameters["required"] = required
        declarations.append(
            FunctionDeclaration(
                name=s["name"],
                description=s.get("description", ""),
                parameters=parameters,
            )
        )
    return Tool(function_declarations=declarations)


# ---------------------------------------------------------------------------
# Messages format conversion
# ---------------------------------------------------------------------------

def _to_openai_messages(messages, system_prompt):
    """Convert Anthropic-style messages to OpenAI chat format."""
    result = []
    if system_prompt:
        result.append({"role": "system", "content": system_prompt})

    for msg in messages:
        role = msg["role"]
        content = msg["content"]

        if role == "user":
            if isinstance(content, str):
                result.append({"role": "user", "content": content})
            elif isinstance(content, list):
                # Could be tool results or text blocks
                parts = []
                tool_result_msgs = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        tool_result_msgs.append({
                            "role": "tool",
                            "tool_call_id": block["tool_use_id"],
                            "content": block["content"],
                        })
                    elif isinstance(block, dict) and block.get("type") == "text":
                        parts.append(block["text"])
                    elif isinstance(block, str):
                        parts.append(block)
                if parts:
                    result.append({"role": "user", "content": " ".join(parts)})
                result.extend(tool_result_msgs)

        elif role == "assistant":
            if isinstance(content, str):
                result.append({"role": "assistant", "content": content})
            elif isinstance(content, list):
                text_parts = []
                tool_calls = []
                for block in content:
                    if hasattr(block, "type"):
                        # Anthropic SDK objects
                        if block.type == "text":
                            text_parts.append(block.text)
                        elif block.type == "tool_use":
                            tool_calls.append({
                                "id": block.id,
                                "type": "function",
                                "function": {
                                    "name": block.name,
                                    "arguments": json.dumps(block.input),
                                },
                            })
                    elif isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif block.get("type") == "tool_use":
                            tool_calls.append({
                                "id": block["id"],
                                "type": "function",
                                "function": {
                                    "name": block["name"],
                                    "arguments": json.dumps(block.get("input", {})),
                                },
                            })
                msg = {"role": "assistant", "content": " ".join(text_parts) or None}
                if tool_calls:
                    msg["tool_calls"] = tool_calls
                result.append(msg)

    return result


def _to_gemini_contents(messages):
    """Convert Anthropic-style messages to Gemini contents format."""
    try:
        from google.generativeai.types import content_types
    except ImportError:
        raise ImportError("google-generativeai is required")

    result = []
    for msg in messages:
        role = "user" if msg["role"] == "user" else "model"
        content = msg["content"]

        if isinstance(content, str):
            result.append({"role": role, "parts": [content]})
        elif isinstance(content, list):
            parts = []
            for block in content:
                if hasattr(block, "type"):
                    if block.type == "text":
                        parts.append(block.text)
                    elif block.type == "tool_use":
                        parts.append({"function_call": {"name": block.name, "args": block.input}})
                elif isinstance(block, dict):
                    if block.get("type") == "text":
                        parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_result":
                        parts.append({
                            "function_response": {
                                "name": block.get("tool_use_id", "tool"),
                                "response": {"result": block.get("content", "")},
                            }
                        })
                    elif block.get("type") == "tool_use":
                        parts.append({"function_call": {
                            "name": block["name"],
                            "args": block.get("input", {}),
                        }})
            if parts:
                result.append({"role": role, "parts": parts})
    return result


# ---------------------------------------------------------------------------
# Claude client
# ---------------------------------------------------------------------------

class ClaudeClient:
    def __init__(self, api_key, model="claude-haiku-4-5"):
        try:
            import anthropic as _anthropic
        except ImportError:
            raise ImportError("anthropic is required: pip install anthropic")
        self.model = model
        self._client = _anthropic.Anthropic(api_key=api_key)

    def chat(self, messages, tools, system_prompt=""):
        # Cache system prompt and tool list for cost savings
        cached_system = [{"type": "text", "text": system_prompt,
                          "cache_control": {"type": "ephemeral"}}]
        cached_tools = list(tools)
        if cached_tools:
            last = dict(cached_tools[-1])
            last["cache_control"] = {"type": "ephemeral"}
            cached_tools[-1] = last

        response = self._client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=cached_system,
            tools=cached_tools,
            messages=messages,
        )

        blocks = []
        for b in response.content:
            if b.type == "text":
                blocks.append({"type": "text", "text": b.text})
            elif b.type == "tool_use":
                blocks.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})

        stop = "tool_use" if response.stop_reason == "tool_use" else "end_turn"
        u = response.usage
        return ModelResponse(
            content=blocks,
            stop_reason=stop,
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_hit_tokens=getattr(u, "cache_read_input_tokens", 0),
        )


# ---------------------------------------------------------------------------
# OpenAI client
# ---------------------------------------------------------------------------

class OpenAIClient:
    def __init__(self, api_key, model="gpt-4o-mini"):
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("openai is required: pip install openai")
        from openai import OpenAI
        self.model = model
        self._client = OpenAI(api_key=api_key)

    def chat(self, messages, tools, system_prompt=""):
        oai_messages = _to_openai_messages(messages, system_prompt)
        oai_tools = _to_openai_tools(tools)

        response = self._client.chat.completions.create(
            model=self.model,
            max_tokens=2048,
            tools=oai_tools,
            messages=oai_messages,
        )

        choice = response.choices[0]
        msg = choice.message
        blocks = []

        if msg.content:
            blocks.append({"type": "text", "text": msg.content})

        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                blocks.append({
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.function.name,
                    "input": args,
                })

        finish = choice.finish_reason
        stop = "tool_use" if finish == "tool_calls" else "end_turn"
        u = response.usage
        return ModelResponse(
            content=blocks,
            stop_reason=stop,
            input_tokens=u.prompt_tokens,
            output_tokens=u.completion_tokens,
        )


# ---------------------------------------------------------------------------
# Gemini client
# ---------------------------------------------------------------------------

class GeminiClient:
    def __init__(self, api_key, model="gemini-1.5-flash"):
        try:
            import google.generativeai as genai
        except ImportError:
            raise ImportError("google-generativeai is required: pip install google-generativeai")
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        self.model_name = model
        self._genai = genai

    def chat(self, messages, tools, system_prompt=""):
        gemini_tools = _to_gemini_tools(tools)
        contents = _to_gemini_contents(messages)

        model = self._genai.GenerativeModel(
            model_name=self.model_name,
            system_instruction=system_prompt or None,
            tools=[gemini_tools],
        )
        response = model.generate_content(contents)

        blocks = []
        stop = "end_turn"
        for part in response.parts:
            if hasattr(part, "text") and part.text:
                blocks.append({"type": "text", "text": part.text})
            if hasattr(part, "function_call") and part.function_call.name:
                fc = part.function_call
                blocks.append({
                    "type": "tool_use",
                    "id": fc.name,   # Gemini doesn't give unique IDs
                    "name": fc.name,
                    "input": dict(fc.args),
                })
                stop = "tool_use"

        try:
            in_tok = response.usage_metadata.prompt_token_count
            out_tok = response.usage_metadata.candidates_token_count
        except Exception:
            in_tok = out_tok = 0

        return ModelResponse(
            content=blocks,
            stop_reason=stop,
            input_tokens=in_tok,
            output_tokens=out_tok,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

# Approximate cost per 1M tokens (USD) — for logging only
COST_TABLE = {
    # Claude
    "claude-haiku-4-5":          {"input": 0.80,  "output": 4.00},
    "claude-haiku-4-5-20251001":  {"input": 0.80,  "output": 4.00},
    "claude-sonnet-4-6":          {"input": 3.00,  "output": 15.00},
    "claude-opus-4-6":            {"input": 15.00, "output": 75.00},
    # OpenAI
    "gpt-4o":                     {"input": 2.50,  "output": 10.00},
    "gpt-4o-mini":                {"input": 0.15,  "output": 0.60},
    "gpt-4-turbo":                {"input": 10.00, "output": 30.00},
    # Gemini
    "gemini-1.5-flash":           {"input": 0.075, "output": 0.30},
    "gemini-1.5-pro":             {"input": 1.25,  "output": 5.00},
    "gemini-2.0-flash":           {"input": 0.10,  "output": 0.40},
}

_PROVIDER_DEFAULTS = {
    "claude":  "claude-haiku-4-5",
    "openai":  "gpt-4o-mini",
    "gemini":  "gemini-1.5-flash",
}


def get_model_client(provider="auto", api_key=None, model=None):
    """
    Create a normalised model client.

    provider: "claude" | "openai" | "gemini" | "auto"
              "auto" detects from env vars: ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY
    api_key:  Override the API key (otherwise reads from env var)
    model:    Override the model name
    """
    if provider == "auto":
        if os.environ.get("ANTHROPIC_API_KEY") or api_key:
            provider = "claude"
        elif os.environ.get("OPENAI_API_KEY"):
            provider = "openai"
            api_key = api_key or os.environ.get("OPENAI_API_KEY")
        elif os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
            provider = "gemini"
            api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        else:
            raise ValueError(
                "No API key found. Set ANTHROPIC_API_KEY, OPENAI_API_KEY, or GEMINI_API_KEY, "
                "or pass api_key explicitly."
            )

    model = model or _PROVIDER_DEFAULTS.get(provider)

    if provider == "claude":
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise ValueError("ANTHROPIC_API_KEY is required for Claude")
        return ClaudeClient(api_key=key, model=model)

    elif provider == "openai":
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ValueError("OPENAI_API_KEY is required for OpenAI")
        return OpenAIClient(api_key=key, model=model)

    elif provider == "gemini":
        key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise ValueError("GEMINI_API_KEY is required for Gemini")
        return GeminiClient(api_key=key, model=model)

    else:
        raise ValueError(f"Unknown provider '{provider}'. Use: claude, openai, gemini, auto")


def estimate_cost(model, input_tokens, output_tokens):
    rates = COST_TABLE.get(model, {"input": 3.00, "output": 15.00})
    return (input_tokens / 1_000_000) * rates["input"] + \
           (output_tokens / 1_000_000) * rates["output"]
