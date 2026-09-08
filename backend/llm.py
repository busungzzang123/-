"""Gemini API 호출 (SDK 미사용, requests 직접).

- responseMimeType/responseSchema 로 구조화 JSON 강제 (제안서 6장, 9-4)
- timeout=30, 모든 예외를 LLMError / LLMParseError 로 변환 (원본 예외·API 키·URL 노출 금지)
"""
from __future__ import annotations

import json
import os

import requests

from db import GENRE_KEYS, GENRE_LABELS

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
_TIMEOUT = 30
_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "recipe_name": {"type": "STRING"},
        "servings": {"type": "INTEGER"},
        "feasible": {"type": "BOOLEAN"},
        "note": {"type": "STRING"},
        "steps": {"type": "STRING"},
        "genre_dist": {
            "type": "OBJECT",
            "properties": {k: {"type": "NUMBER"} for k in GENRE_KEYS},
            "required": list(GENRE_KEYS),
        },
        "difficulty": {"type": "NUMBER"},
    },
    "required": ["recipe_name", "servings", "feasible", "note", "steps", "genre_dist", "difficulty"],
}


class LLMError(Exception):
    """호출 자체 실패 (타임아웃/네트워크/4xx/5xx). status 문자열 포함."""

    def __init__(self, status: str):
        self.status = status
        super().__init__(status)


class LLMParseError(Exception):
    """응답 구조가 예상과 다름 (키 없음/JSON 파싱 실패)."""


def _build_prompt(items: list[dict], preference: dict, feedback: list[dict], servings: int) -> str:
    lines = ["당신은 요리 추천 AI입니다. 아래 냉장고 재료로 만들 수 있는 요리를 하나 추천하세요.", "", "[냉장고 재료]"]
    for it in items:
        lines.append(f"- {it['name']}: {it['amount']}")

    weights = preference["genre_weights"]
    liked = [GENRE_LABELS[k] for k in GENRE_KEYS if weights.get(k, 0.0) > 0.15]
    disliked = [GENRE_LABELS[k] for k in GENRE_KEYS if weights.get(k, 0.0) < -0.15]
    target = min(preference["skill_level"] + 0.3, 5.0)
    taste = []
    if liked:
        taste.append(f"- 선호 장르: {', '.join(liked)}")
    if disliked:
        taste.append(f"- 비선호 장르: {', '.join(disliked)}")
    taste.append(f"- 목표 난이도: 약 {target:.1f} / 5")
    if taste:
        lines += ["", "[사용자 취향]"] + taste

    if feedback:
        lines += ["", "[최근 피드백]"]
        for f in feedback:
            s = f"이전에 추천한 '{f['recipe_name']}'은(는) {f['verdict']}"
            if f.get("comment"):
                s += f" (이유: {f['comment']})"
            lines.append(s)

    genre_help = ", ".join(f"{k}({GENRE_LABELS[k]})" for k in GENRE_KEYS)
    lines += [
        "",
        "[요청]",
        f"- 인분수: {servings}인분",
        "- 재료가 부족해도 항상 만들 수 있는 레시피를 하나는 제안할 것. 추천을 거부하지 말 것.",
        "- 요청받은 인분수를 그대로 만들기 어려우면 servings 값을 실제로 추천 가능한 인분수로 낮추고, feasible을 false로, note에 그 이유를 적을 것.",
        '- steps는 "1. ...\\n2. ..." 형태의 번호 매겨진 여러 줄 문자열로 작성할 것 (배열 금지).',
        f"- genre_dist는 다음 8개 장르의 비중을 0~1 숫자로, 합이 1이 되도록 채울 것: {genre_help}",
        "- difficulty는 1(아주 쉬움)~5(매우 어려움) 사이 숫자.",
        "- 반드시 지정된 JSON 스키마 형식으로만 응답할 것.",
    ]
    return "\n".join(lines)


def _normalize_genre_dist(raw: dict) -> dict:
    dist = {}
    for k in GENRE_KEYS:
        try:
            dist[k] = max(0.0, float(raw.get(k, 0.0)))
        except (TypeError, ValueError):
            dist[k] = 0.0
    total = sum(dist.values())
    if total <= 0:
        return {k: round(1.0 / len(GENRE_KEYS), 4) for k in GENRE_KEYS}
    return {k: round(v / total, 4) for k, v in dist.items()}


def recommend_recipe(items: list[dict], preference: dict, feedback: list[dict], servings: int) -> dict:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise LLMError("status=no_api_key")

    payload = {
        "contents": [{"parts": [{"text": _build_prompt(items, preference, feedback, servings)}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _RESPONSE_SCHEMA,
        },
    }

    try:
        resp = requests.post(
            _ENDPOINT.format(model=GEMINI_MODEL),
            params={"key": api_key},
            json=payload,
            timeout=_TIMEOUT,
        )
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
        raise LLMError("status=network")
    except requests.exceptions.RequestException:
        raise LLMError("status=request_failed")

    if resp.status_code != 200:
        raise LLMError(f"status={resp.status_code}")

    try:
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        data = json.loads(text)
        recipe = {
            "recipe_name": str(data["recipe_name"]),
            "servings": int(data["servings"]),
            "feasible": bool(data["feasible"]),
            "note": str(data["note"]),
            "steps": str(data["steps"]),
            "genre_dist": _normalize_genre_dist(data["genre_dist"]),
            "difficulty": max(1.0, min(5.0, float(data["difficulty"]))),
        }
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        raise LLMParseError()

    return recipe
