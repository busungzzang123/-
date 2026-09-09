"""냉장고털기 백엔드 - FastAPI 라우트 핸들러.

SPEC.md 5번(API 명세) / 3번(CORS) / 13번(백엔드 확인 흐름)을 그대로 구현한다.
- SQLite 접근은 db.py, Gemini 호출은 llm.py 로 분리한다.
- 자체 에러는 HTTPException -> {"detail": "문자열"}.
- 요청 형식/타입 오류는 FastAPI 자동 422 -> {"detail": [ ... ]} (배열, 그대로 둔다).
- rating 검증은 Literal 이 아니라 str + 수동 if + HTTPException(422, 문자열).
- 성공 응답은 항상 200 (DELETE 도 body 있는 200).

로컬 실행: uvicorn app:app --reload --port 8000
프로덕션: uvicorn app:app --host 0.0.0.0 --port $PORT   (워커 1개, --workers 금지)
"""

from datetime import date, timedelta
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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

_VALID_RATINGS = ("좋았음", "별로였음")
MAX_MAIN_ITEMS = 2
CATEGORIES = ("채소", "육류", "수산", "유제품", "조미료", "가공식품", "기타")


@app.on_event("startup")
def _startup():
    db.init_db()


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request, exc):
    """어떤 예외든 raw Python 에러가 그대로 클라이언트에 노출되지 않게 한다.

    (HTTPException / RequestValidationError 는 FastAPI 기본 핸들러가 먼저 처리한다.)
    """
    return JSONResponse(status_code=500, content={"detail": "서버 오류가 발생했습니다"})


# ---------------------------------------------------------------------------
# 요청 모델
# ---------------------------------------------------------------------------

class ItemCreate(BaseModel):
    name: str
    amount: str
    # category 는 Literal 대신 str + 수동 검증 (rating과 같은 이유 - SPEC.md 5-5 참고).
    category: str = "기타"
    expiry_days: Optional[int] = None


class RecommendRequest(BaseModel):
    servings: int


class MainUpdate(BaseModel):
    is_main: bool


class FeedbackCreate(BaseModel):
    # rating 은 str 로 받고 핸들러에서 직접 검증한다 (Literal 금지 - SPEC.md 5-5).
    rating: str
    comment: Optional[str] = None


# ---------------------------------------------------------------------------
# 헬스체크
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# 1) 재료 추가 (장보기)
# ---------------------------------------------------------------------------

@app.post("/api/fridge/items")
def create_item(payload: ItemCreate):
    name = payload.name.strip()
    amount = payload.amount.strip()
    if not name or not amount:
        raise HTTPException(status_code=400, detail="재료 이름과 양을 모두 입력해주세요")

    category = payload.category if payload.category in CATEGORIES else "기타"

    expiry_date = None
    if payload.expiry_days is not None:
        if payload.expiry_days < 0:
            raise HTTPException(status_code=400, detail="유통기한은 0 이상의 정수여야 합니다")
        expiry_date = (date.today() + timedelta(days=payload.expiry_days)).isoformat()

    return db.add_item(name, amount, category, expiry_date)


# ---------------------------------------------------------------------------
# 2) 냉장고 현황 조회
# ---------------------------------------------------------------------------

@app.get("/api/fridge/items")
def get_items():
    return db.list_items()


# ---------------------------------------------------------------------------
# 3) 재료 삭제
# ---------------------------------------------------------------------------

@app.delete("/api/fridge/items/{item_id}")
def remove_item(item_id: int):
    deleted = db.delete_item(item_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="재료를 찾을 수 없습니다")
    return {"success": True}


# ---------------------------------------------------------------------------
# 3-1) 메인재료 태그 (최대 2개 - 태그된 재료마다 레시피를 하나씩 추천한다)
# ---------------------------------------------------------------------------

@app.patch("/api/fridge/items/{item_id}/main")
def update_item_main(item_id: int, payload: MainUpdate):
    item = db.get_item(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="재료를 찾을 수 없습니다")

    if payload.is_main and not item["is_main"] and db.count_main_items() >= MAX_MAIN_ITEMS:
        raise HTTPException(
            status_code=400,
            detail=f"메인재료는 최대 {MAX_MAIN_ITEMS}개까지 선택할 수 있어요",
        )

    return db.set_item_main(item_id, payload.is_main)


# ---------------------------------------------------------------------------
# 4) 레시피 추천
# ---------------------------------------------------------------------------

@app.post("/api/recommend")
def recommend(payload: RecommendRequest):
    if payload.servings <= 0:
        raise HTTPException(status_code=400, detail="1인분 이상으로 설정해주세요")

    items = db.list_items()
    if not items:
        # AI 호출 자체를 하지 않는다 (불필요한 API 비용 방지)
        raise HTTPException(status_code=400, detail="냉장고에 재료가 없습니다")

    feedback = db.recent_feedback(3)

    # 메인재료로 태그된 재료(최대 MAX_MAIN_ITEMS개)마다 레시피를 하나씩 추천한다.
    # 태그된 재료가 없으면 예전처럼 특정 재료에 얽매이지 않는 레시피 1개를 추천한다.
    main_items = [it for it in items if it["is_main"]][:MAX_MAIN_ITEMS]
    foci = [it["name"] for it in main_items] if main_items else [None]

    # 모든 Gemini 호출이 성공한 뒤에만 DB에 저장한다 (일부만 저장되는 것 방지).
    results = []
    for focus in foci:
        try:
            result = llm.recommend_recipe(items, feedback, payload.servings, focus_ingredient=focus)
        except llm.AIRequestError as exc:
            raise HTTPException(status_code=502, detail=f"AI 추천 실패 (status={exc.status})")
        except llm.AIResponseFormatError:
            raise HTTPException(status_code=502, detail="AI 응답 형식이 예상과 다릅니다")
        results.append(result)

    recipes = []
    for result in results:
        recipe_id = db.add_recipe(
            result["recipe_name"],
            result["servings"],
            result["feasible"],
            result["note"],
            result["steps"],
        )
        recipes.append({
            "recipe_id": recipe_id,
            "recipe_name": result["recipe_name"],
            "servings": result["servings"],
            "feasible": result["feasible"],
            "note": result["note"],
            "steps": result["steps"],
        })

    return {"recipes": recipes}


# ---------------------------------------------------------------------------
# 5) 레시피 후기 남기기
# ---------------------------------------------------------------------------

@app.post("/api/recipes/{recipe_id}/feedback")
def create_feedback(recipe_id: int, payload: FeedbackCreate):
    if payload.rating not in _VALID_RATINGS:
        raise HTTPException(
            status_code=422, detail="rating은 좋았음 또는 별로였음이어야 합니다"
        )

    if db.get_recipe(recipe_id) is None:
        raise HTTPException(status_code=404, detail="레시피를 찾을 수 없습니다")

    comment = payload.comment
    if comment is not None:
        comment = comment.strip() or None

    db.add_feedback(recipe_id, payload.rating, comment)
    return {"success": True}
