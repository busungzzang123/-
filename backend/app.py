"""FastAPI 라우트 핸들러 (로직은 얇게, DB 접근은 db.py / Gemini 호출은 llm.py).

규칙(SPEC + 제안서 9장):
- 성공 응답은 항상 200 OK (201/204 미사용, DELETE 도 body 있는 200)
- JSON 키는 snake_case
- 자체 에러는 HTTPException -> {"detail": "문자열"}, FastAPI 자동 검증은 {"detail": [...]}
- rating/reason 검증은 Pydantic Literal 금지: str 로 받고 수동 if + HTTPException(422, "문자열")
"""
import json
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import db
import llm

load_dotenv()

app = FastAPI(title="냉장고털기 API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_RATINGS = {"좋았음", "별로였음"}
_REASONS = {"맛", "난이도", "양", "기타"}


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


class ItemIn(BaseModel):
    name: str
    amount: str


class RecommendIn(BaseModel):
    servings: int


class FeedbackIn(BaseModel):
    rating: str
    reason: Optional[str] = None
    comment: Optional[str] = None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/fridge/items")
def add_item(body: ItemIn):
    name = body.name.strip()
    amount = body.amount.strip()
    if not name or not amount:
        raise HTTPException(400, "재료 이름과 양을 모두 입력해주세요")
    return db.add_item(name, amount)


@app.get("/api/fridge/items")
def list_items():
    return db.list_items()


@app.delete("/api/fridge/items/{item_id}")
def delete_item(item_id: int):
    if not db.delete_item(item_id):
        raise HTTPException(404, "재료를 찾을 수 없습니다")
    return {"success": True}


@app.post("/api/recommend")
def recommend(body: RecommendIn):
    if body.servings <= 0:
        raise HTTPException(400, "1인분 이상으로 설정해주세요")

    items = db.list_items()
    if not items:
        raise HTTPException(400, "냉장고에 재료가 없습니다")

    preference = db.get_preference()
    feedback = db.recent_feedback(3)

    try:
        recipe = llm.recommend_recipe(items, preference, feedback, body.servings)
    except llm.LLMError as e:
        raise HTTPException(502, f"AI 추천 실패 ({e.status})")
    except llm.LLMParseError:
        raise HTTPException(502, "AI 응답 형식이 예상과 다릅니다")

    recipe_id = db.save_recipe(recipe)
    return {
        "recipe_id": recipe_id,
        "recipe_name": recipe["recipe_name"],
        "servings": recipe["servings"],
        "feasible": recipe["feasible"],
        "note": recipe["note"],
        "steps": recipe["steps"],
        "genre_dist": recipe["genre_dist"],
        "difficulty": recipe["difficulty"],
    }


@app.post("/api/recipes/{recipe_id}/feedback")
def add_feedback(recipe_id: int, body: FeedbackIn):
    if body.rating not in _RATINGS:
        raise HTTPException(422, "rating은 좋았음 또는 별로였음이어야 합니다")
    if body.reason is not None and body.reason not in _REASONS:
        raise HTTPException(422, "reason은 맛/난이도/양/기타 중 하나여야 합니다")

    recipe = db.get_recipe(recipe_id)
    if recipe is None:
        raise HTTPException(404, "레시피를 찾을 수 없습니다")

    preference = db.add_feedback(
        recipe_id=recipe_id,
        verdict=body.rating,
        reason=body.reason,
        comment=body.comment,
        genre_dist=json.loads(recipe["genre_dist"]),
        difficulty=recipe["difficulty"],
    )
    return {"success": True, "preference": preference}
