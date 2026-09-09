# 냉장고털기 - 기술 명세서 (프론트/백엔드 분담용)

이 문서는 프론트엔드 개발자와 백엔드 개발자가 각자 이 파일 전체를 Claude Code에게 전달하고 개발을 지시하기 위한 것입니다. 서로 다른 파일만 건드리도록 나눠져 있어서 git push 시 충돌이 거의 나지 않습니다.

## 0. 전제 조건 (반드시 인지)

- **로그인/회원 기능 없음.** 모든 사용자가 같은 냉장고 하나를 공유합니다 (팀 전체가 보는 공용 냉장고 개념).
- **JSON 키는 전부 snake_case** 사용 (`recipe_id`, `added_at` 등). camelCase 섞지 않기.
- **성공 응답은 항상 200 OK**로 통일 (201, 204 사용 안 함 — DELETE도 body 있는 200으로 응답)

## 1. 레포 폴더 구조 (고정 — 임의로 바꾸지 말 것)

```
project-root/
  backend/
    app.py
    db.py
    llm.py
    requirements.txt
  frontend/
    index.html
  index.html        <- 루트 (GitHub Pages용, frontend/index.html과 동일하게 유지)
  README.md
```

- **백엔드 개발자**: `backend/` 폴더 안에 있는 파일만 수정
- **프론트엔드 개발자**: `frontend/index.html` 하나만 수정 (루트 `index.html`은 마지막에 복사만 함)

## 2. 기술 스택 (고정)

- **백엔드**: Python FastAPI + SQLite, Gemini API 호출에 `requests` 사용
- **프론트**: 순수 HTML/CSS/JS 단일 파일 (프레임워크 없음), `fetch`로 API 호출
- **LLM**: Google Gemini API (환경변수 `GEMINI_API_KEY`)
- **배포**: 백엔드는 Railway, 프론트는 GitHub Pages

## 3. 백엔드 환경변수 및 requirements.txt

| 변수명 | 설명 | 기본값 |
|---|---|---|
| `GEMINI_API_KEY` | Gemini API 키 | (필수, 직접 발급) |
| `GEMINI_MODEL` | 사용할 모델 | `gemini-2.5-flash` |
| `DB_PATH` | SQLite 파일 경로 | `fridge.db` (배포 시 Railway Volume 경로로 변경) |

> ⚠️ Railway에 Volume을 연결하지 않고 `DB_PATH`를 기본값(`fridge.db`, 컨테이너 로컬 디스크)으로 두면 **재배포/재시작마다 데이터가 초기화**됩니다. 이건 버그가 아니라 Volume 미설정 때문이니, "데이터가 갑자기 사라졌다"는 문의가 오면 먼저 Volume 연결 여부부터 확인할 것.

> ⚠️ 프로덕션에서는 `uvicorn app:app --host 0.0.0.0 --port $PORT` 처럼 **워커를 1개로만** 실행할 것 (`--workers` 옵션 사용 금지). SQLite는 동시 쓰기에 약해서 워커를 여러 개 띄우면 "database is locked" 에러가 날 수 있음.

**requirements.txt (정확히 이 목록만 사용, 불필요한 패키지 추가 금지)**

```
fastapi
uvicorn
requests
python-dotenv
```

**로컬 실행 명령어**: `uvicorn app:app --reload --port 8000`

**CORS 설정 (app.py에 반드시 포함)**

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
```

## 4. 데이터 모델 (SQLite 테이블)

> ⚠️ **타임스탬프 규칙**: 아래 `*_at` 컬럼들은 SQLite의 `datetime('now')` 기본값 결과를 그대로 응답에 내려주지 말 것. SQLite `datetime('now')`는 `"2026-09-08 12:00:00"`처럼 **공백**으로 날짜/시간을 구분하는데, 이 형식은 브라우저 `new Date()`가 안정적으로 파싱하지 못할 수 있음. INSERT 시 Python에서 `datetime.utcnow().isoformat()`으로 만든 문자열(`"2026-09-08T12:00:00"`, `T` 구분자)을 직접 값으로 넣고 컬럼 기본값에 의존하지 말 것.

### fridge_items
- `id`: INTEGER PRIMARY KEY AUTOINCREMENT
- `name`: TEXT NOT NULL (예: "양파") — 앞뒤 공백 제거(trim) 후 저장
- `amount`: TEXT NOT NULL (예: "1개", "반개", "200g") — 자유 텍스트, 숫자로 변환하려 하지 말 것
- `added_at`: TEXT DEFAULT (datetime('now'))
- `is_main`: INTEGER (0 또는 1, 기본값 0 — API 응답 시 반드시 Python bool로 변환. 사용자가 "메인재료"로 태그한 재료. 최대 2개까지만 허용 — 자세한 내용은 5-3-1, 5-4 참고)
- `category`: TEXT NOT NULL, 기본값 `"기타"` (허용값: 채소/육류/수산/유제품/조미료/가공식품/기타. 등록 시 이 중 하나가 아니면 서버가 조용히 "기타"로 대체한다 — 422로 거부하지 않음)
- `expiry_date`: TEXT (nullable, `"YYYY-MM-DD"` 형식). 등록 요청의 `expiry_days`(오늘부터 며칠 후인지)를 서버가 `date.today() + timedelta(days=expiry_days)`로 변환해 저장한 절대 날짜. 프론트는 이 값과 오늘 날짜를 비교해 D-day를 매번 새로 계산한다 (저장 시점의 상대값을 그대로 쓰지 않음 — 시간이 지나도 정확하도록).

### recipes
- `id`: INTEGER PRIMARY KEY AUTOINCREMENT
- `recipe_name`: TEXT
- `servings`: INTEGER
- `feasible`: INTEGER (0 또는 1로 저장 — API 응답 시 반드시 Python bool로 변환해서 내려줄 것. 0/1 그대로 내려주면 프론트에서 falsy 처리가 꼬임)
- `note`: TEXT
- `steps`: TEXT (번호 매겨진 여러 줄 텍스트, 배열 아님)
- `created_at`: TEXT DEFAULT (datetime('now'))

### recipe_feedback
- `id`: INTEGER PRIMARY KEY AUTOINCREMENT
- `recipe_id`: INTEGER (recipes.id 참조)
- `rating`: TEXT ('좋았음' 또는 '별로였음'만 허용 — 다른 값 들어오면 422로 거부)
- `comment`: TEXT (선택, 없으면 NULL)
- `created_at`: TEXT DEFAULT (datetime('now'))

## 5. API 명세

### 🚨 에러 응답 형식 규칙 (가장 중요 — 반드시 읽을 것)

FastAPI는 요청 형식 자체가 틀리면(타입 오류 등) **자동으로 422 에러를 내는데, 이때 형식이 우리가 정한 것과 다릅니다.**

- **우리가 직접 만드는 에러** (404, 400, 502 등, `HTTPException` 사용): `{"detail": "에러 메시지 문자열"}`
- **FastAPI가 자동으로 만드는 검증 에러** (422, 필드 타입이 틀렸을 때): `{"detail": [{"loc": [...], "msg": "...", "type": "..."}]}` ← **detail이 배열임!**

**→ 프론트엔드는 반드시 두 경우를 다 처리해야 함:**

```javascript
const errData = await res.json().catch(() => ({}));
const message = typeof errData.detail === "string"
  ? errData.detail
  : "요청 형식이 올바르지 않습니다"; // detail이 배열이거나 없을 때 대비
```

이 처리를 안 하면 `[object Object]` 같은 게 화면에 뜨거나 에러가 납니다.

---

### 1) 재료 추가 (장보기)

- `POST /api/fridge/items`
- 요청: `{"name": "양파", "amount": "1개"}`
- 요청에 `category`(선택, 기본값 "기타")와 `expiry_days`(선택, 0 이상의 정수 — 오늘로부터 며칠 후 유통기한인지)도 함께 보낼 수 있다: `{"name": "양파", "amount": "1개", "category": "채소", "expiry_days": 10}`
- 응답 200: `{"id": 1, "name": "양파", "amount": "1개", "added_at": "2026-09-08T12:00:00", "is_main": false, "category": "채소", "expiry_date": "2026-09-19"}`
- **검증**: name 또는 amount가 빈 문자열이거나 공백만 있으면 400 `{"detail": "재료 이름과 양을 모두 입력해주세요"}`
- **참고**: 이건 필드가 "존재하지만 빈 값"인 경우다. `name`/`amount` 키 자체가 요청 body에 없으면 FastAPI가 자동으로 422(배열 detail)를 낸다 — 둘 다 실제로 발생할 수 있으니 프론트는 두 경우 모두 대비해야 한다 (이미 위 에러 규칙대로 처리하면 자동으로 커버됨).
- `category`가 허용 목록에 없으면 422로 거부하지 않고 서버가 "기타"로 대체한다 (Literal 미사용 원칙과 같은 이유로, 프론트가 select 태그만 쓰면 애초에 잘못된 값이 안 옴)
- `expiry_days`가 음수면 400 `{"detail": "유통기한은 0 이상의 정수여야 합니다"}`. 생략하면 `expiry_date`는 `null`
- `is_main`은 등록 시점엔 항상 `false`. 등록 후 3-1번 엔드포인트로 태그한다.

### 2) 냉장고 현황 조회

- `GET /api/fridge/items`
- 응답 200: `[{"id": 1, "name": "양파", "amount": "1개", "added_at": "...", "is_main": false, "category": "채소", "expiry_date": "2026-09-19"}, ...]`
- **정렬**: `added_at` 오름차순(등록된 순서대로). `ORDER BY` 없이 조회하면 SQLite가 순서를 보장하지 않으니 반드시 `ORDER BY added_at ASC`를 쓸 것.
- **엣지케이스**: 재료가 하나도 없으면 에러가 아니라 빈 배열 `[]` 반환. 프론트는 이때 "냉장고가 비어있어요" 문구 표시.

### 3) 재료 삭제

- `DELETE /api/fridge/items/{item_id}`
- 응답 200: `{"success": true}`
- **엣지케이스**: 없는 id면 404 `{"detail": "재료를 찾을 수 없습니다"}`. `item_id`가 숫자가 아니면(예: 문자) FastAPI가 자동 422 발생 — 위 에러 규칙대로 처리.

### 3-1) 메인재료 태그 (2인 이상 메인재료가 있을 때 레시피를 여러 개 추천받기 위한 기능)

- `PATCH /api/fridge/items/{item_id}/main`
- 요청: `{"is_main": true}` (또는 `false`로 태그 해제)
- 응답 200: `{"id": 1, "name": "삼겹살", "amount": "500g", "added_at": "...", "is_main": true}` (변경된 재료 전체)
- **검증**: 이미 메인재료로 태그된 재료가 2개인 상태에서 세 번째 재료를 `is_main: true`로 요청하면 400 `{"detail": "메인재료는 최대 2개까지 선택할 수 있어요"}`. 이미 메인재료인 항목을 다시 `true`로 보내는 건(변화 없음) 카운트에 포함하지 않으므로 거부되지 않는다. `is_main: false`(해제)는 항상 허용.
- **엣지케이스**: 없는 id면 404 `{"detail": "재료를 찾을 수 없습니다"}`

### 4) 레시피 추천 (핵심 기능)

- `POST /api/recommend`
- 요청: `{"servings": 3}`
- 로직: 냉장고 전체 재료 + 최근 피드백 3개를 프롬프트에 넣어 Gemini 호출
- **메인재료 태그에 따른 분기**:
  - 메인재료로 태그된 재료가 0개면 → 특정 재료에 얽매이지 않는 레시피 1개를 추천 (기존 동작과 동일)
  - 메인재료가 1개면 → 그 재료를 메인으로 강제한 레시피 1개
  - 메인재료가 2개면 → 재료마다 하나씩, 레시피 2개 (Gemini를 재료별로 한 번씩 호출)
- 응답 200: `{"recipes": [{"recipe_id": 5, "recipe_name": "양파볶음밥", "servings": 3, "feasible": true, "note": "재료가 충분합니다", "steps": "1. ...\n2. ..."}, ...]}` — **배열은 항상 1개 또는 2개**. (⚠️ 예전 버전은 `recipes` 래핑 없이 레시피 객체 하나를 바로 내려줬으나, 메인재료 다중 추천 기능 추가로 이 형태로 변경됨. 프론트/백엔드 모두 `data.recipes` 배열을 순회하도록 구현할 것)
- `feasible`이 false면 `note`에 부족 사유 명시하고, `servings`는 AI가 실제로 추천한 인분수로 내려준다 (예: 3인분 요청했지만 고기가 부족해 2인분으로 조정했다면 `"servings": 2`, `note`에 사유 명시 — 예: "고기 200g으로는 3인분이 어려워 2인분 기준으로 추천했습니다")
- **검증**: `servings`가 없거나 정수가 아니면 → FastAPI 자동 422 (위 에러 규칙 적용). `servings`가 0 이하이면 → 400 `{"detail": "1인분 이상으로 설정해주세요"}`
- **엣지케이스 - 빈 냉장고**: 재료가 하나도 없으면 400 `{"detail": "냉장고에 재료가 없습니다"}` (AI 호출 자체를 하지 않음 — 불필요한 API 비용 방지)
- **엣지케이스 - AI 호출 실패** (타임아웃/서버에러): 502 `{"detail": "AI 추천 실패 (status=...)"}` — 절대 API 키나 요청 URL이 메시지에 포함되면 안 됨. 메인재료 2개 케이스에서는 두 번의 호출 중 하나라도 실패하면 즉시 502를 반환한다.
- **엣지케이스 - AI 응답이 JSON 파싱 불가**: 502 `{"detail": "AI 응답 형식이 예상과 다릅니다"}` (원본 응답 내용 노출 금지)
- **DB 저장 시점**: Gemini 응답을 성공적으로 받아 파싱까지 끝난 뒤에만 `recipes` 테이블에 INSERT한다. 실패하면 아무것도 저장하지 않고 바로 에러 응답을 반환한다 (부분적으로 깨진 레코드가 남지 않게). 메인재료 2개 케이스도 마찬가지로, 두 레시피 모두 성공적으로 받아온 뒤에만 두 건을 함께 INSERT한다 (하나만 저장되는 상태를 만들지 않는다).
- 백엔드는 requests 호출에 `timeout=30` 지정, `try/except`로 감싸서 원본 예외가 그대로 클라이언트에 노출되지 않게 할 것

#### 4-1. Gemini 호출 방법 (필수 — 반드시 이 형식대로 구현할 것)

이 부분이 명세 없이 구현되면 백엔드마다 응답 파싱 방식이 제각각이라 502 에러가 자주 난다. 아래 형식을 정확히 따를 것.

**요청**

```
POST https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}
Content-Type: application/json
```

```json
{
  "contents": [
    { "parts": [ { "text": "<아래 6번 섹션 프롬프트>" } ] }
  ],
  "generationConfig": {
    "responseMimeType": "application/json",
    "responseSchema": {
      "type": "OBJECT",
      "properties": {
        "recipe_name": { "type": "STRING" },
        "servings": { "type": "INTEGER" },
        "feasible": { "type": "BOOLEAN" },
        "note": { "type": "STRING" },
        "steps": { "type": "STRING" }
      },
      "required": ["recipe_name", "servings", "feasible", "note", "steps"]
    }
  }
}
```

- `responseMimeType`/`responseSchema`를 반드시 지정할 것. 이걸 빼면 Gemini가 자유 형식 텍스트(마크다운, 설명 문장 등)를 섞어서 응답하고, 그걸 `json.loads`로 파싱하려다 502가 계속 난다.
- `steps`는 "1. ...\n2. ..." 형태의 번호 매겨진 여러 줄 문자열로 나오게 스키마와 프롬프트 양쪽에서 지시한다 (배열 아님, 위 4번 섹션 데이터 모델과 동일).

**응답 파싱**

```
response.json()["candidates"][0]["content"]["parts"][0]["text"]
```

- 이 값 자체가 JSON 문자열이므로 다시 한 번 `json.loads()`를 해야 위 스키마 형태의 dict가 나온다.
- `candidates`/`content`/`parts`/`text` 키가 없거나, 두 번째 `json.loads`가 실패하면 → 502 `{"detail": "AI 응답 형식이 예상과 다릅니다"}` (KeyError, IndexError, json.JSONDecodeError를 전부 잡을 것).
- HTTP status가 200이 아니면(4xx/5xx) → 502 `{"detail": "AI 추천 실패 (status=...)"}`. `requests.exceptions.Timeout`, `ConnectionError`도 동일하게 502로 변환.

### 5) 레시피 후기 남기기

- `POST /api/recipes/{recipe_id}/feedback`
- 요청: `{"rating": "좋았음", "comment": "너무 매웠어요"}` (comment는 선택, 생략 가능)
- 응답 200: `{"success": true}`
- **검증**: rating이 "좋았음"/"별로였음" 둘 중 하나가 아니면 422 `{"detail": "rating은 좋았음 또는 별로였음이어야 합니다"}`
  - ⚠️ **구현 주의**: Pydantic 모델에서 `rating: Literal["좋았음", "별로였음"]`처럼 타입으로 제약을 걸면, 값이 틀렸을 때 FastAPI가 자동으로 배열 형태 422(`{"detail": [{"loc": ..., ...}]}`)를 내려준다 — 이 명세가 요구하는 문자열 detail과 다르다. 반드시 `rating: str`로 받고 라우트 핸들러 안에서 직접 값을 비교해 `raise HTTPException(422, "rating은 좋았음 또는 별로였음이어야 합니다")`로 처리할 것.
  - `comment`는 Pydantic 모델에서 `comment: str | None = None`처럼 기본값을 반드시 줄 것. 기본값 없이 필수 필드로 두면 comment를 생략한 요청이 전부 자동 422로 거부된다.
- **엣지케이스**: `recipe_id`가 존재하지 않으면 404 `{"detail": "레시피를 찾을 수 없습니다"}`

### 참고: 헬스체크 엔드포인트 (Railway 배포 확인용, 필수는 아니지만 권장)

- `GET /health` → `{"status": "ok"}`

## 6. "최근 피드백 3개" 반영 방식 (구체적 구현 가이드)

레시피 추천 시 아래 SQL로 최근 피드백을 가져와서 프롬프트에 텍스트로 넣습니다:

```sql
SELECT r.recipe_name, f.rating, f.comment
FROM recipe_feedback f
JOIN recipes r ON f.recipe_id = r.id
ORDER BY f.created_at DESC
LIMIT 3
```

프롬프트에 넣을 때 형식 예시:

```
이전에 추천한 '양파볶음밥'은 별로였음 (이유: 너무 매웠어요)
이전에 추천한 '된장찌개'는 좋았음
```

피드백이 하나도 없으면 이 섹션 자체를 프롬프트에서 생략 (빈 문자열 넣지 말기).

프롬프트 마지막에는 항상 아래 지시를 포함할 것 (Gemini가 "추천할 레시피가 없다"고 거부하거나 스키마를 어기는 걸 방지):

```
- 재료가 부족해도 항상 만들 수 있는 레시피를 하나는 제안할 것. 추천을 거부하지 말 것.
- 요청받은 인분수를 그대로 만들기 어려우면 servings 값을 실제로 추천 가능한 인분수로 낮추고, feasible을 false로, note에 그 이유를 적을 것.
- 반드시 지정된 JSON 스키마 형식으로만 응답할 것.
```

## 7. 프론트엔드가 지켜야 할 규칙

- `index.html` 상단에 `const BACKEND_URL = "...";` 하나로 백엔드 주소 관리. 로컬 개발 중 기본값은 `http://localhost:8000`으로 두고, **머지 직전에 반드시 Railway에 배포된 실제 백엔드 URL로 바꿀 것** (안 바꾸면 로컬에선 되는데 배포 후 아무 응답도 안 오는 것처럼 보임).
- 모든 API 호출에 `try/catch` 필수 — 네트워크 자체가 끊기는 경우(서버 다운 등)도 대비
- 위 "에러 응답 형식 규칙" 그대로 처리 (`detail`이 문자열인지 배열인지 체크)
- 냉장고 목록이 비어있을 때, 로딩 중일 때, 에러났을 때 각각 다른 문구 표시
- `steps`는 `"1. ...\n2. ..."` 형태의 줄바꿈 포함 문자열로 온다. 그냥 `<div>`에 넣으면 줄바꿈이 무시되어 한 줄로 붙어 보이니, `white-space: pre-line` CSS를 주거나 `\n` 기준으로 줄을 나눠 렌더링할 것.
- `/api/recommend` 응답은 `{"recipes": [...]}` 배열이다 (1개 또는 2개). 배열을 순회하며 레시피 카드를 그 개수만큼 렌더링하고, 각 카드마다 독립된 후기 폼(`recipe_id`도 각자 다름)을 붙일 것 — 하나의 폼/ID를 재사용하면 두 번째 레시피의 후기가 첫 번째 레시피에 잘못 붙는다.

## 8. Claude Code에 넣을 프롬프트 템플릿

**백엔드 개발자용**

> "아래 명세서(이 파일)대로 FastAPI + SQLite 백엔드를 만들어줘. 파일 구조는 backend/app.py, backend/db.py, backend/llm.py, backend/requirements.txt로 만들어줘. 에러 응답 형식이랑 검증 규칙, 엣지케이스까지 명세서에 적힌 그대로 정확히 구현해줘."

**프론트엔드 개발자용**

> "아래 API 명세(이 파일의 5, 7번 섹션)를 사용하는 단일 index.html(HTML+CSS+JS)을 만들어줘. BACKEND_URL 변수로 백엔드 주소를 설정하게 해주고, 에러 응답 형식 규칙(detail이 문자열/배열 둘 다 가능)을 반드시 반영해줘."

## 9. Git 충돌 방지 규칙

- 백엔드 개발자: `feature/backend` 브랜치에서 `backend/` 폴더만 작업
- 프론트 개발자: `feature/frontend` 브랜치에서 `frontend/index.html`만 작업
- 각자 작업 끝나면 push → PR 생성 → main에 merge
- 파일이 겹치지 않으므로 둘 다 merge되어도 충돌이 거의 없음
- 마지막에 프론트 개발자가 `cp frontend/index.html index.html`로 루트에도 반영 (GitHub Pages용)

## 10. 자주 나는 실수 체크리스트 (백엔드)

- [ ] DELETE 응답을 204(No Content)로 만들지 않았는지 (body 없는 응답은 프론트 `json()` 파싱에서 에러남 → 반드시 200 + `{"success": true}`)
- [ ] `feasible` 필드를 SQLite에서 읽은 0/1 그대로 응답에 넣지 않았는지 (`bool(row["feasible"])`로 변환했는지)
- [ ] 엔드포인트 경로 끝에 슬래시(`/`) 유무를 문서와 다르게 만들지 않았는지 (`/api/fridge/items/` vs `/api/fridge/items` — 다르면 자동 리다이렉트가 걸려 CORS 프리플라이트가 깨질 수 있음)
- [ ] 어떤 예외든 raw Python 에러가 그대로 클라이언트에 노출되지 않는지 (전부 try/except로 감싸서 HTTPException으로 변환했는지)
- [ ] Gemini 호출부에 timeout을 지정했는지
- [ ] rating 검증을 Pydantic `Literal`이 아니라 `str` + 수동 if문 + `HTTPException(422, ...)`로 구현했는지 (Literal을 쓰면 detail이 배열로 나가 명세와 달라짐)
- [ ] Gemini 요청에 `responseMimeType: "application/json"`과 `responseSchema`를 지정해서 구조화된 JSON을 강제했는지
- [ ] `added_at`/`created_at`을 SQLite `datetime('now')` 결과 그대로 쓰지 않고, Python에서 `datetime.utcnow().isoformat()`으로 만든 `T` 구분자 문자열을 INSERT에 직접 넣었는지
- [ ] uvicorn을 `--workers` 옵션으로 여러 개 띄우지 않았는지 (SQLite 동시 쓰기 이슈)
- [ ] 메인재료 2개 태그 시 Gemini 호출을 두 번(재료마다 한 번씩) 하고, 하나라도 실패하면 DB에 아무것도 저장하지 않는지 (둘 다 성공한 뒤에만 두 건을 저장)
- [ ] 메인재료 태그 시도 시 이미 2개가 태그된 상태에서 세 번째를 태그하면 400을 반환하는지 (Literal이 아닌 수동 카운트 체크)

## 11. 자주 나는 실수 체크리스트 (프론트)

- [ ] 에러 응답의 `detail`이 문자열이 아니라 배열로 올 수 있다는 것을 처리했는지
- [ ] 냉장고가 비어있을 때(`[]`)를 에러로 착각해서 에러 문구를 띄우지 않는지 (정상 상태임)
- [ ] 레시피 추천 버튼을 연타했을 때 중복 요청이 안 나가게 로딩 중엔 버튼을 비활성화했는지
- [ ] `feasible`이 `false`일 때도 `steps`는 여전히 표시해주는지 (레시피 자체는 나오되 주의 문구만 추가되는 개념)
- [ ] `BACKEND_URL`을 배포된 실제 Railway 주소로 바꿨는지 (`localhost`로 둔 채 머지하지 않았는지)
- [ ] `steps`의 `\n` 줄바꿈이 화면에서 실제로 줄바꿈으로 보이는지 (`white-space: pre-line` 등 처리했는지)
- [ ] `/api/recommend` 응답의 `servings`가 요청했던 값과 다를 수 있다는 것(feasible=false일 때)을 감안해서, 요청 시 보냈던 값이 아니라 **응답으로 받은 servings**를 화면에 표시하는지
- [ ] `/api/recommend` 응답이 레시피 객체 하나가 아니라 `{"recipes": [...]}` 배열이라는 것을 반영했는지 (배열 개수만큼 카드를 그리고, 카드마다 독립된 후기 폼을 붙였는지)
- [ ] 메인재료 태그가 이미 2개인 상태에서 세 번째를 태그하면 400이 오는데, 이 에러 문구가 제대로 뜨는지

## 12. 시간 없어서 기본값으로 결정한 것

- 피드백 참고 개수: 최근 3개 (전체 피드백 대상, 사용자 구분 없음 — 0번 전제 참고)
- 재료 병합 방식: 병합 안 함 (같은 이름이어도 별도 항목으로 저장, AI가 텍스트로 합산 이해)

## 13. 병합 전 최종 확인 (프론트/백엔드 각자 merge 하기 직전에)

각자 코딩 지식 없이 작업하기 때문에, merge 후에 둘이 같이 디버깅하기 어렵다. 그러니 **merge 전에 아래 흐름을 혼자서 한 번씩 실제로 눌러보고** 전부 정상 동작하는 걸 확인한 뒤에 PR을 올릴 것.

**백엔드 담당자 (Swagger `/docs` 또는 curl로 확인)**

1. `GET /health` → `{"status": "ok"}`
2. `GET /api/fridge/items` → 빈 배열 `[]`
3. `POST /api/fridge/items` (`{"name": "양파", "amount": "1개"}`) → 200, id/added_at 포함
4. `GET /api/fridge/items` → 방금 추가한 재료가 배열에 있는지, `added_at`이 `T` 포함 형식인지
5. `POST /api/recommend` (`{"servings": 2}`) → 200, `recipe_name`/`servings`/`feasible`/`note`/`steps` 전부 있는지, `steps`에 줄바꿈(`\n`)이 실제로 들어있는지
6. `POST /api/recipes/{recipe_id}/feedback` (`{"rating": "좋았음"}`, comment 생략) → 200 (comment 생략해도 에러 안 나는지 꼭 확인)
7. `POST /api/recipes/{recipe_id}/feedback` (`{"rating": "이상한값"}`) → 422, detail이 **문자열**인지 확인 (배열로 나오면 Literal 타입 문제이니 5번 섹션 경고 다시 확인)
8. `DELETE /api/fridge/items/{없는 id}` → 404
9. 냉장고를 비운 상태에서 `POST /api/recommend` → 400 (AI 호출 없이 바로 에러 나는지, Gemini 비용이 안 나갔는지 로그로 확인)
10. 재료 2개를 `PATCH /api/fridge/items/{id}/main` (`{"is_main": true}`)로 태그 → 각각 200, `is_main: true`
11. 세 번째 재료를 같은 방식으로 태그 시도 → 400 `{"detail": "메인재료는 최대 2개까지 선택할 수 있어요"}`
12. 메인재료 2개가 태그된 상태에서 `POST /api/recommend` → 200, `recipes` 배열 길이가 2인지, 각 레시피가 태그한 재료를 실제로 메인으로 사용했는지

**프론트엔드 담당자 (실제 배포된 BACKEND_URL로 붙여서 확인)**

1. 냉장고가 비어있을 때 "비어있어요" 문구가 뜨는지 (에러 문구가 아닌지)
2. 재료 추가 → 목록에 바로 반영되는지
3. 재료 이름/양을 공백만 넣고 등록 시도 → 에러 문구가 제대로 뜨는지 (`[object Object]` 아닌지)
4. 레시피 추천 버튼을 빠르게 두 번 눌러도 중복 요청이 안 나가는지 (버튼 비활성화 확인)
5. 추천 결과의 `steps`가 여러 줄로 나뉘어 보이는지 (한 줄로 붙어있지 않은지)
6. `feasible: false` 케이스에서도 `steps`가 계속 보이는지, `note`도 같이 보이는지
7. 후기 남기기 후 다시 추천 요청 시 에러 없이 정상 동작하는지
8. 백엔드 서버를 잠깐 꺼둔 상태에서 아무 버튼이나 눌러 네트워크 에러 문구가 뜨는지 (흰 화면/무한 로딩이 아닌지)
9. 재료 2개를 "메인재료로 표시"하고 추천 요청 → 레시피 카드가 2개 뜨는지, 카드마다 후기 폼이 따로 동작하는지
10. 메인재료를 3개째 태그 시도 → 400 에러 문구가 뜨는지 (버튼이 무반응이거나 콘솔 에러만 나는 게 아닌지)
