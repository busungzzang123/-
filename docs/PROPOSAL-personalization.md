# 제안서: 유저 + Preference 기반 레시피 개인화

- 상태: **PROPOSED** (팀 검토 대기 — ADR-001 재검토 필요)
- 작성 배경: 현재 SPEC은 "공용 냉장고 + 피드백 로그를 프롬프트에 붙이는" 최소 구조다.
  개인화(사용자별 취향/난이도 학습)를 하려면 유저 개념과 파생 상태(`user_preference`)가 필요하다.
- 이 문서가 승인되면: `docs/ADR.md`에 ADR-008로 편입하고, 아래 4~7장을 `SPEC.md` v2 섹션으로 옮긴다.

---

## 1. 무엇을 바꾸나 (요약)

| 항목 | 현재 (SPEC v1) | 제안 (v2) |
|---|---|---|
| 사용자 | 없음. 전체가 냉장고 1개 공유 | 유저별 냉장고 1개. 비밀번호 없는 토큰 식별 |
| 피드백 활용 | 최근 3개를 텍스트로 프롬프트에 첨부 | 피드백 → `user_preference` 가중치 갱신, 그 상태를 프롬프트에 반영 |
| 취향 모델 | 없음 | 8개 장르축 가중치 벡터 (중립에서 시작) |
| 난이도 | 없음 | 유저 `skill_level` (ELO 스타일 갱신) |
| 레시피 메타 | recipe_name/servings/feasible/note/steps | + genre_dist / difficulty / spiciness / richness |

**유지되는 규칙 (v1과 동일):**

- 성공 응답은 항상 `200 OK` (201/204 미사용, DELETE도 body 있는 200)
- JSON 키 전부 snake_case
- 에러 응답 이원화: 자체 에러 `{"detail": "문자열"}`, FastAPI 자동 검증 `{"detail": [...]}` 배열
- 타임스탬프는 앱에서 `datetime.utcnow().isoformat()` (`T` 구분자) 생성해 INSERT
- Gemini: `responseMimeType: "application/json"` + `responseSchema` 강제, `timeout=30`, try/except로 키·URL 노출 차단
- `feasible`은 응답 시 Python bool로 변환

---

## 2. 사용자 식별 (로그인 없이)

로그인 폼/비밀번호는 여전히 만들지 않는다. 대신:

1. 프론트가 최초 로드 시 localStorage에 `user_token`이 없으면 `POST /api/users` 호출
2. 서버가 `user_id`(공개용)와 `user_token`(비밀, UUID)을 발급
3. 프론트는 `user_token`을 localStorage에 저장하고, **이후 모든 API 요청에 `X-User-Token` 헤더로 전송**
4. 토큰이 없거나 유효하지 않으면 → `401 {"detail": "유저 토큰이 필요합니다"}` (자체 에러, 문자열 detail)

> 트레이드오프: localStorage를 지우면 그 유저의 취향/냉장고에 다시 접근할 수 없다(계정 복구 개념 없음). MVP에서는 허용. 같은 브라우저를 여러 명이 쓰면 한 유저로 취급된다.

`POST /api/users`는 유저 생성과 동시에 그 유저의 `fridge` 1개와 `user_preference` 1행(중립값)을 함께 만든다.

---

## 3. 엔티티 관계

```
users (1) ──── (1) fridges ──── (N) fridge_items
   │
   ├──── (1) user_preferences
   │
   ├──── (N) recipes ──── (N) feedback_events
   │                          │
   └──────────────────────────┘   (feedback_events.user_id = 그 피드백을 남긴 유저)
```

- `users : fridges` = 1 : 1 (유저당 냉장고 하나)
- `fridges : fridge_items` = 1 : N
- `users : user_preferences` = 1 : 1 (유저당 취향 상태 하나)
- `users : recipes` = 1 : N (그 유저에게 추천된 레시피 이력)
- `recipes : feedback_events` = 1 : N (같은 레시피에 여러 번 후기 가능)
- `feedback_events`는 **원본 로그**다. `user_preferences`는 이 로그로부터 계산되는 **파생 상태**다. 둘 다 저장한다 — 가중치 공식을 바꾸면 로그로 재계산할 수 있어야 하고, 상태가 오염됐을 때 복구 경로가 필요하다.

---

## 4. 데이터 모델 (SQLite 테이블)

### 4-1. users

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `id` | TEXT PK | UUID. 공개 식별자 (`user_id`로 응답) |
| `token` | TEXT NOT NULL UNIQUE | UUID. 비밀. 응답에 딱 한 번(생성 시)만 내려감 |
| `created_at` | TEXT NOT NULL | ISO 8601 `T` |

### 4-2. fridges

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | |
| `user_id` | TEXT NOT NULL UNIQUE | users.id 참조 |
| `created_at` | TEXT NOT NULL | |

### 4-3. fridge_items

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | |
| `fridge_id` | INTEGER NOT NULL | fridges.id 참조 |
| `name` | TEXT NOT NULL | trim 후 저장 |
| `amount` | TEXT NOT NULL | 자유 텍스트 ("1개", "반개", "200g"). 숫자 변환 안 함 |
| `added_at` | TEXT NOT NULL | ISO 8601 `T`. 조회 시 `ORDER BY added_at ASC` |

### 4-4. user_preferences

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `user_id` | TEXT PK | users.id 참조 |
| `genre_weights` | TEXT NOT NULL | JSON 문자열. 8개 키 → float. 초기값 전부 `0.0` |
| `skill_level` | REAL NOT NULL | 초기값 `2.0`. 범위 `[1.0, 5.0]` |
| `feedback_count` | INTEGER NOT NULL | 초기값 `0`. 반영된 피드백 수 |
| `updated_at` | TEXT NOT NULL | |

`genre_weights` JSON 키 (고정 8개, snake_case 영문 키 / UI 표시용 한글 라벨):

| 키 | 한글 라벨 | 예시 |
|---|---|---|
| `soup` | 국물요리 | 찌개, 국, 전골 |
| `one_plate` | 한접시요리 | 볶음밥, 덮밥, 파스타, 비빔밥 |
| `stir_fry_grill` | 볶음·구이 | 제육볶음, 생선구이, 두부부침 |
| `braised_steamed` | 조림·찜 | 갈비찜, 감자조림, 계란찜 |
| `noodle` | 면요리 | 잔치국수, 비빔면, 파스타 |
| `side_dish` | 반찬·무침 | 나물, 겉절이, 장아찌 |
| `fried_jeon` | 튀김·전 | 부침개, 튀김, 전 |
| `light_raw` | 가벼운·생식 | 샐러드, 냉채, 오픈샌드위치 |

각 가중치 범위는 `[-1.0, 1.0]`. 양수 = 선호, 음수 = 비선호, 0 = 중립.

### 4-5. recipes

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | |
| `user_id` | TEXT NOT NULL | users.id 참조 (누구에게 추천됐나) |
| `recipe_name` | TEXT NOT NULL | |
| `servings` | INTEGER NOT NULL | AI가 실제로 추천한 인분수 |
| `feasible` | INTEGER NOT NULL | 0/1 저장, 응답 시 bool 변환 |
| `note` | TEXT NOT NULL | |
| `steps` | TEXT NOT NULL | `"1. ...\n2. ..."` 번호 매겨진 여러 줄 문자열 (배열 아님) |
| `genre_dist` | TEXT NOT NULL | JSON. 8개 장르 키 → float, **합 ≈ 1.0**. AI가 생성 |
| `difficulty` | REAL NOT NULL | `1.0`~`5.0`. AI가 생성 |
| `spiciness` | INTEGER NOT NULL | `0`~`3`. AI가 생성 |
| `richness` | INTEGER NOT NULL | `0`~`3` (기름진 정도). AI가 생성 |
| `created_at` | TEXT NOT NULL | |

### 4-6. feedback_events

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | |
| `user_id` | TEXT NOT NULL | users.id 참조 |
| `recipe_id` | INTEGER NOT NULL | recipes.id 참조 |
| `verdict` | TEXT NOT NULL | `'좋았음'` 또는 `'별로였음'`만 허용 |
| `reason` | TEXT | 선택. `'맛'`/`'난이도'`/`'양'`/`'기타'` 중 하나 또는 NULL |
| `comment` | TEXT | 선택. 없으면 NULL |
| `created_at` | TEXT NOT NULL | |

---

## 5. Preference 갱신 로직 (백엔드 구현 명세)

피드백이 저장될 때(`POST /api/recipes/{recipe_id}/feedback`) 아래 순서로 처리한다:

1. `feedback_events`에 원본 INSERT (항상)
2. 해당 레시피의 `genre_dist`, `difficulty`를 읽는다
3. 아래 규칙으로 `user_preferences` 갱신
4. `feedback_count += 1`, `updated_at` 갱신
5. 1~4를 **한 트랜잭션**으로 커밋

### 5-1. 장르 가중치 갱신

상수: `LR = 0.2`

- 적용 조건: `verdict == '좋았음'` **또는** (`verdict == '별로였음'` **그리고** `reason == '맛'`)
  - `verdict == '별로였음'`인데 `reason`이 `'난이도'`/`'양'`/`'기타'`/NULL이면 **장르 가중치는 건드리지 않는다** (맛 불만이 아니므로)
- `sign = +1` (좋았음) / `-1` (별로였음)
- 각 장르 키 `g`에 대해:
  ```
  w[g] = clamp(w[g] + LR * sign * genre_dist[g], -1.0, 1.0)
  ```

### 5-2. 난이도(skill_level) 갱신

상수: `K = 0.4`, `SPREAD = 2.0`

- 적용 조건:
  - `verdict == '좋았음'` → `W = 1` (그 난이도를 소화함)
  - `verdict == '별로였음'` **그리고** `reason == '난이도'` → `W = 0` (너무 어려웠음)
  - 그 외 → **skill_level 갱신 안 함**
- 계산:
  ```
  E = 1 / (1 + 10 ** ((difficulty - skill_level) / SPREAD))
  skill_level = clamp(skill_level + K * (W - E), 1.0, 5.0)
  ```

> numpy 불필요. `genre_weights`는 파이썬 dict 연산, `skill_level`은 스칼라. `requirements.txt`는 v1 그대로 유지 가능.

---

## 6. 레시피 추천 로직 변경 (`POST /api/recommend`)

1. `X-User-Token` → user 조회 (없으면 401)
2. 그 유저의 `fridge_items` 조회. 비어 있으면 `400 {"detail": "냉장고에 재료가 없습니다"}` (AI 호출 안 함)
3. `user_preferences` 조회
4. 프롬프트 조립:
   - 냉장고 재료 전체 (name + amount)
   - **선호 장르**: `genre_weights`에서 값 > `0.15`인 키의 한글 라벨 (없으면 생략)
   - **비선호 장르**: 값 < `-0.15`인 키의 한글 라벨 (없으면 생략)
   - **목표 난이도**: `target = min(skill_level + 0.3, skill_level 기준 +1.0, 5.0)` → "난이도 약 {target} 수준으로 추천"
   - **콜드스타트**: `feedback_count < 3`이면 "아직 취향 정보가 부족하니 장르가 겹치지 않는 다양한 요리를 시도하라" 문구 추가
   - 최근 `feedback_events` 3개 (선택, v1과 동일 형식) — `SELECT ... JOIN recipes ... ORDER BY created_at DESC LIMIT 3` (해당 유저 것만)
   - v1 고정 지시문 3줄 유지 (재료 부족해도 하나는 제안 / 어려우면 servings 낮추고 feasible=false / 스키마 준수)
5. Gemini 호출 — `responseSchema`에 아래 필드 추가:
   ```json
   {
     "type": "OBJECT",
     "properties": {
       "recipe_name": { "type": "STRING" },
       "servings":    { "type": "INTEGER" },
       "feasible":    { "type": "BOOLEAN" },
       "note":        { "type": "STRING" },
       "steps":       { "type": "STRING" },
       "genre_dist": {
         "type": "OBJECT",
         "properties": {
           "soup": {"type":"NUMBER"}, "one_plate": {"type":"NUMBER"},
           "stir_fry_grill": {"type":"NUMBER"}, "braised_steamed": {"type":"NUMBER"},
           "noodle": {"type":"NUMBER"}, "side_dish": {"type":"NUMBER"},
           "fried_jeon": {"type":"NUMBER"}, "light_raw": {"type":"NUMBER"}
         },
         "required": ["soup","one_plate","stir_fry_grill","braised_steamed","noodle","side_dish","fried_jeon","light_raw"]
       },
       "difficulty": { "type": "NUMBER" },
       "spiciness":  { "type": "INTEGER" },
       "richness":   { "type": "INTEGER" }
     },
     "required": ["recipe_name","servings","feasible","note","steps","genre_dist","difficulty","spiciness","richness"]
   }
   ```
6. 파싱 성공 후에만 `recipes` INSERT (v1 규칙 유지). 실패 시 아무것도 저장하지 않고 502
   - AI 호출 실패(타임아웃/4xx/5xx/ConnectionError): `502 {"detail": "AI 추천 실패 (status=...)"}`
   - 파싱 불가(KeyError/IndexError/JSONDecodeError): `502 {"detail": "AI 응답 형식이 예상과 다릅니다"}`
   - `genre_dist` 합이 0이거나 키가 빠졌으면 → 백엔드에서 균등 분포(각 0.125)로 보정하고 진행 (502 내지 않음)
7. `200`으로 아래 응답

---

## 7. 웹 계약 (API 명세)

모든 엔드포인트는 `GET /health`, `POST /api/users`를 제외하고 **`X-User-Token` 헤더 필수**.
누락/무효 시 `401 {"detail": "유저 토큰이 필요합니다"}`.

### 7-1. `POST /api/users` — 유저 생성 (토큰 없이 호출)

- 요청 body: 없음
- 응답 200:
  ```json
  { "user_id": "d1f...", "user_token": "9ac..." }
  ```
- `user_token`은 이 응답에서만 내려온다. 프론트는 즉시 localStorage에 저장.

### 7-2. `GET /api/users/me` — 내 정보 + 취향 스냅샷

- 응답 200:
  ```json
  {
    "user_id": "d1f...",
    "created_at": "2026-09-08T12:00:00",
    "preference": {
      "genre_weights": {
        "soup": 0.4, "one_plate": 0.0, "stir_fry_grill": -0.2, "braised_steamed": 0.0,
        "noodle": 0.1, "side_dish": 0.0, "fried_jeon": -0.3, "light_raw": 0.0
      },
      "skill_level": 2.6,
      "feedback_count": 5
    }
  }
  ```
- 프론트가 "당신의 취향" 패널이나 난이도 표시에 쓸 수 있다. 필수 사용은 아님.

### 7-3. `POST /api/fridge/items` — 재료 추가

- 요청: `{"name": "양파", "amount": "1개"}`
- 응답 200: `{"id": 1, "name": "양파", "amount": "1개", "added_at": "2026-09-08T12:00:00"}`
- 검증: `name` 또는 `amount`가 빈 문자열/공백만 → `400 {"detail": "재료 이름과 양을 모두 입력해주세요"}`
- 키 자체가 없으면 FastAPI 자동 422 (배열 detail)

### 7-4. `GET /api/fridge/items` — 내 냉장고 조회

- 응답 200: `[{"id": 1, "name": "양파", "amount": "1개", "added_at": "..."}, ...]`
- 정렬: `added_at ASC`
- 재료 없으면 빈 배열 `[]` (에러 아님)

### 7-5. `DELETE /api/fridge/items/{item_id}` — 재료 삭제

- 응답 200: `{"success": true}`
- 내 냉장고에 없는 `item_id`(존재하지 않거나 남의 것)면 → `404 {"detail": "재료를 찾을 수 없습니다"}` (남의 것 존재 여부를 노출하지 않기 위해 동일 처리)
- `item_id`가 숫자 아니면 FastAPI 자동 422

### 7-6. `POST /api/recommend` — 레시피 추천

- 요청: `{"servings": 3}`
- 응답 200:
  ```json
  {
    "recipe_id": 5,
    "recipe_name": "양파된장국",
    "servings": 3,
    "feasible": true,
    "note": "재료가 충분합니다",
    "steps": "1. 물 3컵을 끓인다\n2. 된장 2스푼을 푼다\n3. 양파를 넣고 5분 끓인다",
    "genre_dist": {
      "soup": 0.8, "one_plate": 0.0, "stir_fry_grill": 0.0, "braised_steamed": 0.1,
      "noodle": 0.0, "side_dish": 0.1, "fried_jeon": 0.0, "light_raw": 0.0
    },
    "difficulty": 1.5,
    "spiciness": 0,
    "richness": 1
  }
  ```
- 검증:
  - `servings` 없거나 정수 아님 → FastAPI 자동 422
  - `servings <= 0` → `400 {"detail": "1인분 이상으로 설정해주세요"}`
  - 냉장고 비어있음 → `400 {"detail": "냉장고에 재료가 없습니다"}`
  - AI 실패 → `502 {"detail": "AI 추천 실패 (status=...)"}`
  - AI 응답 파싱 불가 → `502 {"detail": "AI 응답 형식이 예상과 다릅니다"}`
- `feasible: false`여도 `steps`/`note`/`genre_dist`/`difficulty`는 채워서 내려온다. `servings`는 AI가 조정한 실제 값.

### 7-7. `POST /api/recipes/{recipe_id}/feedback` — 후기 + 취향 갱신

- 요청: `{"rating": "별로였음", "reason": "난이도", "comment": "칼질이 너무 많았어요"}`
  - `rating`: 필수. `"좋았음"`/`"별로였음"`만
  - `reason`: 선택. `"맛"`/`"난이도"`/`"양"`/`"기타"` 또는 생략. `"별로였음"`일 때 프론트에서 받기를 권장(필수 아님)
  - `comment`: 선택. 생략 가능
- 응답 200:
  ```json
  {
    "success": true,
    "preference": {
      "genre_weights": { "soup": 0.4, "one_plate": 0.0, "...": 0.0 },
      "skill_level": 2.35,
      "feedback_count": 6
    }
  }
  ```
  - `preference`는 갱신 직후 값. 프론트가 즉시 UI에 반영할 수 있게 함 (추가 필드일 뿐 200 유지)
- 검증 (⚠️ 구현 방식 v1과 동일):
  - `rating`을 Pydantic `Literal`로 제약하지 **말 것**. `rating: str`로 받고 핸들러에서 직접 비교 → `raise HTTPException(422, "rating은 좋았음 또는 별로였음이어야 합니다")` (문자열 detail 유지)
  - `reason`도 동일하게 `reason: str | None = None`으로 받고, 값이 있는데 허용 목록 밖이면 `HTTPException(422, "reason은 맛/난이도/양/기타 중 하나여야 합니다")`
  - `comment: str | None = None` — 기본값 필수 (없으면 생략 요청이 자동 422)
- 엣지: `recipe_id` 없음 → `404 {"detail": "레시피를 찾을 수 없습니다"}`. 남의 `recipe_id`도 동일하게 404

### 7-8. `GET /health`

- 응답 200: `{"status": "ok"}`

---

## 8. 프론트엔드가 알아야 할 것 (프론트 담당용)

1. **부트스트랩**: 페이지 로드 시
   - `localStorage.getItem("user_token")` 확인
   - 없으면 `POST /api/users` → 받은 `user_token` 저장
   - 있으면 그대로 사용
2. **모든 요청에 헤더**: `fetch(BACKEND_URL + path, { headers: { "X-User-Token": token, "Content-Type": "application/json" }, ... })`
   - `POST /api/users`, `GET /health`만 예외
3. **401 처리**: 토큰이 무효(예: DB 초기화)면 401이 온다 → localStorage 비우고 다시 `POST /api/users`로 복구 후 재시도
4. **에러 형식** (v1과 동일): `detail`이 문자열이면 그대로, 배열/없음이면 "요청 형식이 올바르지 않습니다"
   ```javascript
   const errData = await res.json().catch(() => ({}));
   const message = typeof errData.detail === "string" ? errData.detail : "요청 형식이 올바르지 않습니다";
   ```
5. **장르 라벨 맵** (표시용):
   ```javascript
   const GENRE_LABELS = {
     soup: "국물요리", one_plate: "한접시요리", stir_fry_grill: "볶음·구이",
     braised_steamed: "조림·찜", noodle: "면요리", side_dish: "반찬·무침",
     fried_jeon: "튀김·전", light_raw: "가벼운·생식",
   };
   ```
6. **피드백 폼**: `rating`(좋았음/별로였음) + `reason` 라디오(맛/난이도/양/기타, "별로였음" 선택 시 노출 권장) + `comment`(선택 textarea)
7. **추천 결과 표시**: `steps`는 `white-space: pre-line` 또는 `\n` split. `feasible: false`여도 `steps` 계속 표시 + `note` 병기. `servings`는 응답값 사용(요청값 아님)
8. **취향 패널(선택)**: `GET /api/users/me` 또는 피드백 응답의 `preference`로 "선호 장르 상위 2개", "난이도 skill_level" 정도 표시. `skill_level` 1~5를 "입문~숙련" 라벨로 매핑 가능
9. **BACKEND_URL**: 로컬 `http://localhost:8000`, 머지 직전 Railway 실제 주소로 교체
10. **중복 요청 방지**: 추천 버튼은 요청 중 비활성화

---

## 9. v1.5 — 1시간 제약 구현 버전 (지금 만드는 것)

전체 v2(유저 도입)는 시간상 불가. **유저를 버리고 공유 preference 1행만** 두는 축소판을 먼저 구현한다.
ADR-001(공유 냉장고)을 건드리지 않으므로 `feature/backend`에서 추가만 하면 되고 팀 재논의가 필요 없다.
v2(1~8장, 10~13장)는 이후 목표로 유지한다.

### 9-1. v2 대비 버리는 것

| 버림 | 대체 |
|---|---|
| `users`, `fridges` 테이블, `X-User-Token`, `401`, `POST /api/users`, `GET /api/users/me` | 유저 개념 제거. 냉장고·취향 모두 전역 공유 |
| `recipes.spiciness`, `recipes.richness` | 갱신에 안 쓰임 — 컬럼 자체를 만들지 않음 |
| 별도 `GET /api/preferences` | 피드백 응답에 `preference` 포함해서 대체 |
| decay, 콜드스타트 탐색 로직 | 현재 가중치를 항상 그대로 프롬프트에 주입 |
| 마이그레이션 | 런칭 전 — 테이블 drop & recreate |
| `backend/preference.py` 분리 | `db.py` 안 함수 2개로 |

### 9-2. 테이블 (v1.5)

v1의 `fridge_items`는 그대로 (단 `fridge_id` 없이 전역). 아래만 신규/변경:

**user_preference** (항상 `id = 1` 단일 행)

| 컬럼 | 타입 | 초기값 |
|---|---|---|
| `id` | INTEGER PK | 1 (고정) |
| `genre_weights` | TEXT NOT NULL | 8개 키 전부 `0.0`인 JSON |
| `skill_level` | REAL NOT NULL | `2.0` |
| `feedback_count` | INTEGER NOT NULL | `0` |
| `updated_at` | TEXT NOT NULL | |

앱 기동 시 이 행이 없으면 만든다(seed).

**recipes** — v1 컬럼 + `genre_dist TEXT NOT NULL`(8키 JSON, 합≈1) + `difficulty REAL NOT NULL`(1~5)

**feedback_events** — v1 `recipe_feedback`에서 이름 변경. 컬럼: `id`, `recipe_id`, `verdict`(`'좋았음'`/`'별로였음'`), `reason`(`'맛'`/`'난이도'`/`'양'`/`'기타'` 또는 NULL), `comment`(NULL 가능), `created_at`

장르 8키는 4-4의 표 그대로 (`soup`, `one_plate`, `stir_fry_grill`, `braised_steamed`, `noodle`, `side_dish`, `fried_jeon`, `light_raw`).

### 9-3. 갱신 규칙 (`reason` 없어도 동작)

피드백 저장 시 한 트랜잭션으로: `feedback_events` INSERT → `user_preference`(id=1) 갱신 → `feedback_count += 1`.

```
LR = 0.2 ;  K = 0.4 ;  SPREAD = 2.0
sign = +1 (좋았음) / -1 (별로였음)

# 장르: 항상 적용
for g in 8keys:
    w[g] = clamp(w[g] + LR * sign * genre_dist[g], -1.0, 1.0)

# 난이도
E = 1 / (1 + 10 ** ((difficulty - skill_level) / SPREAD))
if verdict == '좋았음':                       skill_level += K * (1 - E)
elif verdict == '별로였음' and reason == '난이도':  skill_level += K * (0 - E)
else:                                         (skill_level 갱신 안 함)
skill_level = clamp(skill_level, 1.0, 5.0)
```

> v2(5장)와 차이: v1.5는 "별로였음+맛"일 때도 장르 가중치를 내린다(단순화). `reason`이 오면 난이도 갱신만 정교해진다.

### 9-4. 엔드포인트 (v1.5)

| 엔드포인트 | v1 대비 변화 |
|---|---|
| `GET /health` | 없음 → `{"status": "ok"}` |
| `POST /api/fridge/items` | 없음 |
| `GET /api/fridge/items` | 없음 (`ORDER BY added_at ASC`) |
| `DELETE /api/fridge/items/{item_id}` | 없음 → `{"success": true}` / 없으면 404 |
| `POST /api/recommend` | 프롬프트에 취향 3줄 주입. 응답에 `genre_dist`, `difficulty` 추가 |
| `POST /api/recipes/{recipe_id}/feedback` | body에 `reason`(선택) 추가. 응답 `{"success": true, "preference": {...}}` |

**`POST /api/recommend` 응답 200 (v1.5)**

```json
{
  "recipe_id": 5, "recipe_name": "양파된장국", "servings": 3,
  "feasible": true, "note": "재료가 충분합니다",
  "steps": "1. ...\n2. ...",
  "genre_dist": { "soup": 0.8, "one_plate": 0.0, "stir_fry_grill": 0.0, "braised_steamed": 0.1, "noodle": 0.0, "side_dish": 0.1, "fried_jeon": 0.0, "light_raw": 0.0 },
  "difficulty": 1.5
}
```

프롬프트 주입 3줄 (해당 조건일 때만):
- 선호 장르: `genre_weights` 값 > `0.15`인 키의 한글 라벨
- 비선호 장르: 값 < `-0.15`인 키의 한글 라벨
- 목표 난이도: `min(skill_level + 0.3, 5.0)` 수준

`genre_dist`가 비었거나 합이 0이면 백엔드가 균등분포(각 `0.125`)로 보정하고 진행 (502 아님).

**`POST /api/recipes/{recipe_id}/feedback` (v1.5)**

- 요청: `{"rating": "좋았음", "reason": "맛", "comment": "..."}` — `reason`, `comment` 모두 선택
- 검증: v1 규칙 유지. `rating`/`reason` 모두 Pydantic `Literal` 금지, `str`/`str | None = None`로 받고 수동 if + `HTTPException(422, "문자열")`
  - `reason` 값이 있는데 목록 밖 → `HTTPException(422, "reason은 맛/난이도/양/기타 중 하나여야 합니다")`
- 응답 200:
  ```json
  { "success": true,
    "preference": { "genre_weights": { "...": 0.0 }, "skill_level": 2.35, "feedback_count": 6 } }
  ```
- `recipe_id` 없음 → `404 {"detail": "레시피를 찾을 수 없습니다"}`

### 9-5. 프론트 영향

v1 UI 그대로 머지해도 안 깨진다. `reason` 미전송 = 정상, 응답의 새 필드(`genre_dist`/`difficulty`/`preference`)는 무시하면 됨.
여유 되면: 피드백 폼에 `reason` 라디오(맛/난이도/양/기타), 추천 카드에 난이도 표시.

### 9-6. 파일

`backend/{app.py, db.py, llm.py, requirements.txt}` 구조·`requirements.txt` 목록 모두 v1 그대로.
`recipe_feedback` → `feedback_events` 테이블명 변경과 컬럼 추가는 백엔드 전용이라 git 충돌 없음(팀 공지만).

---

## 10. 백엔드 구조 변경 (승인 필요 항목)

현재 `backend/{app.py, db.py, llm.py, requirements.txt}` 고정 규칙을 아래처럼 확장 제안:

```
backend/
  app.py            # 라우트 핸들러 + CORS + X-User-Token 의존성
  db.py             # SQLite CRUD (users, fridges, fridge_items, recipes, feedback_events, user_preferences)
  llm.py            # Gemini 호출 (genre_dist/difficulty 포함 스키마)
  preference.py     # ← 신규. 가중치 갱신 순수 함수 (update_genre_weights, update_skill_level)
  requirements.txt  # v1 그대로 (fastapi, uvicorn, requests, python-dotenv)
```

- `preference.py`는 순수 함수만 (DB·네트워크 없음) → 유닛테스트 쉽고, Spring 포팅 시 그대로 옮겨짐
- `app.py`는 여전히 라우트만. 갱신 로직은 `preference.py`, 저장은 `db.py`

---

## 11. 마이그레이션 (v2 도입 시)

MVP가 아직 런칭 전이면 **테이블 드롭 후 재생성**이 가장 단순하다.
이미 데이터가 있다면:

1. `users`에 레거시 유저 1행 생성 (`id = "legacy-shared"`)
2. `fridge_items`에 `fridge_id` 컬럼 추가 → 레거시 유저의 `fridge`로 백필
3. `recipes`에 `user_id`, `genre_dist`, `difficulty`, `spiciness`, `richness` 추가 → `user_id`는 레거시, 나머지는 균등분포/기본값
4. `recipe_feedback` → `feedback_events`로 이름 변경, `user_id = "legacy-shared"`, `reason = NULL` 백필
5. `user_preferences`에 레거시 유저 중립 1행 생성

---

## 12. 구현 참고: JPA 엔티티 매핑 (Spring 대안 검토용)

> 팀 스택은 현재 FastAPI 고정(ADR-004 관련). 아래는 별도 PoC/포팅을 검토할 때 참고용.

| 테이블 | JPA 엔티티 | 매핑 |
|---|---|---|
| `users` | `User` | `@Id String id`, `@Column(unique=true) String token` |
| `fridges` | `Fridge` | `@OneToOne User`, `@OneToMany List<FridgeItem>` |
| `fridge_items` | `FridgeItem` | `@ManyToOne Fridge` |
| `user_preferences` | `UserPreference` | `@Id`(=user_id, `@MapsId`), `genreWeights`는 `@Convert`로 JSON ↔ `Map<String,Double>`, `double skillLevel` |
| `recipes` | `Recipe` | `@ManyToOne User`, `genreDist` JSON 컨버터, `double difficulty` |
| `feedback_events` | `FeedbackEvent` | `@ManyToOne User`, `@ManyToOne Recipe`, `enum Verdict`, `enum Reason` (nullable) |

- 웹 계약(200 통일, snake_case, 이원화 에러)은 `@RestControllerAdvice` + Jackson `PropertyNamingStrategies.SNAKE_CASE`로 재현
- `rating`/`reason` 검증은 Bean Validation 대신 서비스 레이어 수동 검사 → `422 + 문자열 detail`
- `preference.py`의 순수 함수 2개 → `PreferenceUpdater` 도메인 서비스로 1:1 이식, 유닛테스트 그대로

---

## 13. 미해결 질문 (팀 논의)

1. `reason`을 필수로 할까? (필수면 프론트 폼이 강제되지만 마찰 증가)
2. 시간 경과에 따른 가중치 decay를 넣을까? (오래된 취향이 계속 남음)
3. `LR = 0.2`, `K = 0.4` 등 상수는 감으로 잡은 값 — 실사용 후 튜닝 필요
4. 장르 8축이 한식 편향 — 양식/중식/일식 비중이 커지면 재검토
5. 임베딩 기반(Gemini `text-embedding-004`)으로 전환 시점 — "좋아한 것과 비슷한" 퍼지 매칭이 필요해지면. 그때도 `user_preference` 추상화는 유지, 내부만 교체
6. ADR-007(SQLite 단일 워커) — 유저가 늘면 병목. Postgres 전환 트리거 정의 필요
