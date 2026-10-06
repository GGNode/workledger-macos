"""Separate desktop UI envelopes from the user's request, without inferring authorship."""
from __future__ import annotations

import json
import re


def request_text(text: str) -> str:
    for tag in ("in-app-browser-context", "external_codex_apps_open_page"):
        text = re.sub(rf"<{tag}\b[^>]*>.*?</{tag}>\s*", "", text, flags=re.S)
    match = re.fullmatch(r"\s*<send_user_message_question_reply>\s*(.*?)\s*</send_user_message_question_reply>\s*", text, flags=re.S)
    if match:
        try:
            replies = json.loads(match[1])
            if isinstance(replies, list):
                return "\n".join(str(r["answer"]) for r in replies if isinstance(r, dict) and "answer" in r)
        except (ValueError, TypeError):
            return ""  # An invalid transport envelope is not a new user instruction.
    text = re.sub(r"^\s*## My request:\s*", "", text)
    return text.strip()
