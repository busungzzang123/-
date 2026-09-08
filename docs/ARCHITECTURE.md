# 아키텍처

## 디렉토리 구조
```
project-root/
├── backend/
│   ├── app.py             # FastAPI 라우트 핸들러 + CORS 설정 (로직은 얇게 유지)
│   ├── db.py               # SQLite 연결 및 CRUD 쿼리
│   ├── llm.py                # Gemini API 호출 (requests, timeout=30)
│   └── requirements.txt      # fastapi, uvicorn, requests, python-dotenv (이 목록만 사용)
├── frontend/
│   └── index.html              # HTML+CSS+JS 단일 파일. BACKEND_URL 상수로 API 주소 관리
├── index.html                   # frontend/index.html 복사본 (GitHub Pages 배포용, 마지막에 cp)
└── README.md
```

- 백엔드 개발자는 `backend/` 폴더만, 프론트엔드 개발자는 `frontend/index.html`만 수정한다. 파일 경계가 겹치지 않아 두 브랜치가 merge되어도 충돌이 거의 없다.
- 폴더 구조는 고정이며 임의로 바꾸지 않는다.

## 패턴
- 백엔드: `app.py`(라우트) → `db.py`(SQLite 접근) / `llm.py`(Gemini 호출)로 관심사를 분리한다. 별도 ORM 없이 SQLite에 직접 쿼리한다.
- 프론트: 프레임워크·빌드 도구 없는 순수 HTML/CSS/JS. 전역 `const BACKEND_URL`로 API 주소를 한 곳에서 관리하고 `fetch`로 REST 호출한다.
- 모든 API 성공 응답은 200 OK로 통일(201/204 미사용), JSON 키는 snake_case로 통일한다.
- 에러 응답은 두 형태만 존재한다: 자체 에러(`HTTPException`) → `{"detail": "문자열"}`, FastAPI 자동 검증(422) → `{"detail": [...]}` 배열. 프론트는 반드시 둘 다 분기 처리한다.

## 데이터 흐름
```
사용자 입력 (frontend/index.html)
  → fetch(BACKEND_URL + "/api/...")
  → FastAPI 라우트 (backend/app.py)
      → backend/db.py   : SQLite CRUD (fridge_items / recipes / recipe_feedback)
      → backend/llm.py  : Gemini API 호출 (레시피 추천 요청에서만 실행)
                           냉장고 재료 전체 + 최근 피드백 3개(SQL로 조회)를 프롬프트에 포함
  → JSON 응답 (200 고정, snake_case)
  → 프론트 DOM 업데이트 (로딩 중 / 빈 목록 / 에러 각각 다른 문구)
```

## 상태 관리
- 백엔드: 세션/유저 상태 없음. SQLite 3개 테이블(`fridge_items`, `recipes`, `recipe_feedback`)이 유일한 상태 저장소이며 모든 사용자가 동일한 데이터를 공유한다.
- 프론트: 별도 상태 관리 라이브러리 없이 DOM 직접 조작. 레시피 추천 요청 중에는 버튼을 비활성화해 중복 요청(연타)을 막는다.
