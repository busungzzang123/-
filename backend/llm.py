"""Gemini API 호출 계층.

SPEC.md 5-4-1(Gemini 호출 방법), 6번(최근 피드백 반영 / 프롬프트 말미 지시)을 그대로 구현한다.
- requests 로 직접 호출, timeout=30, try/except 로 감싼다.
- generationConfig.responseMimeType / responseSchema 를 반드시 지정한다.
- 원본 예외 / API 키 / 요청 URL 이 호출부로 새어나가지 않도록,
  실패는 AIRequestError(status) 또는 AIResponseFormatError 로만 변환해서 던진다.
"""

import json
import os

import requests

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-2.5-flash"

_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "recipe_name": {"type": "STRING"},
        "servings": {"type": "INTEGER"},
        "feasible": {"type": "BOOLEAN"},
        "note": {"type": "STRING"},
        "steps": {"type": "STRING"},
    },
    "required": ["recipe_name", "servings", "feasible", "note", "steps"],
}

_REQUIRED_KEYS = ("recipe_name", "servings", "feasible", "note", "steps")


class AIRequestError(Exception):
    """AI 호출 자체 실패 (타임아웃 / 연결 실패 / 비200 응답). status 는 노출 안전한 값만."""

    def __init__(self, status):
        self.status = status
        super().__init__(f"AI request failed (status={status})")


class AIResponseFormatError(Exception):
    """AI 응답을 예상 스키마의 dict 로 파싱하지 못함."""


def _build_prompt(items: list, feedback: list, servings: int) -> str:
    lines = [
        "당신은 냉장고에 있는 재료로 만들 수 있는 요리를 추천하는 요리사입니다.",
        f"요청 인분수: {servings}인분",
        "",
        "현재 냉장고에 있는 재료 (같은 이름이 여러 번 나오면 합쳐서 이해할 것):",
    ]
    for it in items:
        lines.append(f"- {it['name']}: {it['amount']}")

    if feedback:
        lines.append("")
        lines.append("최근 사용자 피드백 (참고용):")
        for fb in feedback:
            name = fb.get("recipe_name") or "이전 레시피"
            rating = fb.get("rating") or ""
            comment = fb.get("comment")
            if comment:
                lines.append(f"이전에 추천한 '{name}'은(는) {rating} (이유: {comment})")
            else:
                lines.append(f"이전에 추천한 '{name}'은(는) {rating}")

    lines.append("")
    lines.append("- 재료가 부족해도 항상 만들 수 있는 레시피를 하나는 제안할 것. 추천을 거부하지 말 것.")
    lines.append(
        "- 요청받은 인분수를 그대로 만들기 어려우면 servings 값을 실제로 추천 가능한 인분수로 낮추고, "
        "feasible을 false로, note에 그 이유를 적을 것."
    )
    lines.append(
        "- steps는 \"1. ...\\n2. ...\" 형태의 번호 매겨진 여러 줄 문자열로 작성할 것 (배열 아님)."
    )
    lines.append("- 반드시 지정된 JSON 스키마 형식으로만 응답할 것.")
    return "\n".join(lines)


def recommend_recipe(items: list, feedback: list, servings: int) -> dict:
    """Gemini 를 호출해 {recipe_name, servings, feasible, note, steps} dict 를 돌려준다.

    실패 시 AIRequestError / AIResponseFormatError 를 던진다.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    model = os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)

    if not api_key:
        # 키/URL 을 노출하지 않고, 노출 안전한 토큰만 status 로 전달
        raise AIRequestError("no_api_key")

    prompt = _build_prompt(items, feedback, servings)
    body = {
        "contents": [
            {"parts": [{"text": prompt}]}
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _RESPONSE_SCHEMA,
        },
    }
    url = f"{GEMINI_BASE_URL}/models/{model}:generateContent?key={api_key}"

    try:
        resp = requests.post(url, json=body, timeout=30)
    except requests.exceptions.Timeout:
        raise AIRequestError("timeout")
    except requests.exceptions.ConnectionError:
        raise AIRequestError("connection_error")
    except requests.exceptions.RequestException:
        raise AIRequestError("request_error")

    if resp.status_code != 200:
        raise AIRequestError(resp.status_code)

    try:
        payload = resp.json()
        text = payload["candidates"][0]["content"]["parts"][0]["text"]
        data = json.loads(text)
    except (KeyError, IndexError, TypeError, ValueError):
        # ValueError 는 json.JSONDecodeError (resp.json() / json.loads 양쪽) 를 포함
        raise AIResponseFormatError()

    if not isinstance(data, dict) or any(k not in data for k in _REQUIRED_KEYS):
        raise AIResponseFormatError()

    try:
        return {
            "recipe_name": str(data["recipe_name"]),
            "servings": int(data["servings"]),
            "feasible": bool(data["feasible"]),
            "note": str(data["note"]),
            "steps": str(data["steps"]),
        }
    except (TypeError, ValueError):
        raise AIResponseFormatError()
