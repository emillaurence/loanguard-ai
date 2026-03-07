"""Shared utilities for the Layer 2 regulatory extraction pipeline (notebooks 211-214)."""

import json
import time

import anthropic


def strip_fences(text: str) -> str:
    """Remove markdown code fences if Claude adds them despite instructions."""
    text = text.strip()
    lines = text.splitlines()
    if lines and lines[0].startswith('```'):
        lines = lines[1:]
    if lines and lines[-1].strip() == '```':
        lines = lines[:-1]
    return '\n'.join(lines).strip()


def call_claude_stream(
    client: anthropic.Anthropic,
    model: str,
    max_tokens: int,
    system: str,
    messages: list,
    temperature: float = 0.0,
) -> str:
    """Call Claude via streaming and return the response text.

    Streaming is required by the Anthropic SDK when max_tokens is large enough
    that the response could exceed the 10-minute non-streaming timeout.

    Raises RuntimeError if the response is truncated (stop_reason == 'max_tokens').
    """
    with client.messages.stream(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=messages,
        temperature=temperature,
    ) as stream:
        response = stream.get_final_message()

    if response.stop_reason == 'max_tokens':
        raise RuntimeError(
            f'Response truncated: Claude hit max_tokens ({max_tokens}). '
            f'Reduce batch size or increase MAX_TOKENS.'
        )

    return response.content[0].text


def call_claude_stream_json(
    client: anthropic.Anthropic,
    model: str,
    max_tokens: int,
    system: str,
    messages: list,
    temperature: float = 0.0,
) -> object:
    """Call Claude via streaming, strip fences, and parse JSON.

    Retries once with a JSON-fix prompt on a parse error.
    Raises RuntimeError on truncation; raises json.JSONDecodeError if both attempts fail.
    """
    raw = call_claude_stream(client, model, max_tokens, system, messages, temperature=temperature)
    raw = strip_fences(raw)

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f'    JSON parse error: {e}. Retrying with correction prompt...')
        time.sleep(2)
        fix_text = call_claude_stream(
            client,
            model,
            max_tokens,
            system='Fix the following to be valid JSON. Return ONLY the fixed JSON, no markdown fences, no preamble.',
            messages=[{'role': 'user', 'content': f'Fix this JSON:\n{raw}'}],
            temperature=temperature,
        )
        return json.loads(strip_fences(fix_text))


def serialise_row(row: dict) -> dict:
    """Serialise any nested dicts/lists in a row to JSON strings for CSV storage."""
    out = {}
    for k, v in row.items():
        if isinstance(v, (dict, list)):
            out[k] = json.dumps(v)
        else:
            out[k] = v
    return out
