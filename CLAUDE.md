# 프로젝트: 냉장고털기

## 기술 스택
- 백엔드: Python FastAPI + SQLite, Gemini API 호출은 `requests`로 직접 (SDK 미사용)
- 프론트: 순수 HTML/CSS/JS 단일 파일 (프레임워크·빌드 도구 없음), `fetch`로 API 호출
- 배포: 백엔드 Railway / 프론트 GitHub Pages

## 아키텍처 규칙
- CRITICAL: 백엔드 개발자는 `backend/` 폴더만, 프론트엔드 개발자는 `frontend/index.html`만 수정한다. 이 경계를 넘으면 git 충돌이 발생한다.
- CRITICAL: 로그인/회원 기능을 만들지 않는다. 모든 사용자가 하나의 공유 냉장고를 사용한다.
- CRITICAL: 모든 JSON 키는 snake_case 사용 (`recipe_id`, `added_at` 등). camelCase 섞지 않는다.
- CRITICAL: 성공 응답은 항상 200 OK로 통일한다 (201, 204 사용 금지 — DELETE도 body 있는 200으로 응답).
- 에러 응답은 두 형식이 공존한다: 자체 에러(`HTTPException`)는 `{"detail": "문자열"}`, FastAPI 자동 검증 에러(422)는 `{"detail": [...]}` 배열. 프론트는 반드시 둘 다 처리한다 (`typeof detail === "string"` 체크).
- 레포 폴더 구조는 고정이다 (`backend/{app.py,db.py,llm.py,requirements.txt}`, `frontend/index.html`, 루트 `index.html`, `README.md`). 임의로 바꾸지 않는다.
- `backend/app.py`는 라우트 핸들러만 두고, SQLite 접근은 `backend/db.py`, Gemini 호출은 `backend/llm.py`에 분리한다.
- Gemini 호출에는 반드시 `timeout=30`을 지정하고 `try/except`로 감싸 원본 예외(및 API 키·요청 URL)가 클라이언트에 노출되지 않게 한다.
- 상세 API 명세·데이터 모델·엣지케이스는 `/SPEC.md`, 아키텍처 배경은 `docs/ARCHITECTURE.md`·`docs/ADR.md` 참고.

## 개발 프로세스
- 새 기능 구현 시 SPEC.md에 적힌 검증 규칙·엣지케이스를 빠짐없이 구현하고, SPEC.md 10·11번 체크리스트로 자체 점검한다.
- 커밋 메시지는 conventional commits 형식을 따를 것 (feat:, fix:, docs:, refactor:)
- 백엔드는 `feature/backend`, 프론트는 `feature/frontend` 브랜치에서 작업 후 PR로 main에 merge한다.

## 명령어
```
# 백엔드
cd backend
pip install -r requirements.txt
uvicorn app:app --reload --port 8000

# 프론트 (로컬 확인용, 정적 서빙이면 무엇이든 가능)
python -m http.server 5500 --directory frontend
```
