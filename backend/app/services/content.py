"""Builds message subject/body from event payloads and digests."""
from typing import Any, Dict, List, Optional, Tuple


def _title(payload: Dict[str, Any], fallback: str) -> str:
    return str(payload.get("title") or fallback)


def single_content(workflow_name: str, payload: Dict[str, Any]) -> Tuple[str, str]:
    body = str(payload.get("message") or payload.get("body") or "")
    return _title(payload, workflow_name), body or _title(payload, workflow_name)


def digest_content(workflow_name: str, events: List[Dict[str, Any]], summary: Dict[str, Any]) -> Tuple[str, str]:
    subject = f"{workflow_name}: {len(events)} updates" if len(events) > 1 else _title(events[0]["payload"], workflow_name)
    parts = [f"TL;DR: {summary['tldr']}"]
    if summary.get("action_items"):
        parts.append("Action items:\n" + "\n".join(f"- {a}" for a in summary["action_items"]))
    lines = [f"- {_title(e['payload'], 'Update')}: {e['payload'].get('message', '')}".rstrip(": ") for e in events]
    parts.append("All updates:\n" + "\n".join(lines))
    return subject, "\n\n".join(parts)
