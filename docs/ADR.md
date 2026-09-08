# Architecture Decision Records

## 철학
MVP 속도 최우선. 프론트/백엔드 담당자가 서로 다른 파일만 건드리도록 경계를 고정해 git 충돌을 최소화한다. 외부 의존성은 SPEC에 명시된 최소 목록으로 제한한다.

---

### ADR-001: 로그인 없는 단일 공유 냉장고
**결정**: 사용자 인증/구분 없이 모든 사용자가 하나의 냉장고 데이터(fridge_items, recipes, recipe_feedback)를 공유한다.
**이유**: "팀 전체가 보는 공용 냉장고"라는 유스케이스에 맞고, 인증 구현 비용을 없애 MVP 범위를 최소화한다.
**트레이드오프**: 다중 사용자 환경에서 데이터 충돌/삭제 오남용 가능성이 있고 개인화된 추천을 할 수 없다.

### ADR-002: 프레임워크 없는 프론트엔드 (순수 HTML/CSS/JS 단일 파일)
**결정**: React/Vue 등 프레임워크 없이 `frontend/index.html` 하나로 구현하고, `fetch`로 API 통신한다. 배포 시 루트 `index.html`로 그대로 복사한다.
**이유**: 빌드 도구 없이 GitHub Pages에 바로 배포 가능하고, 프론트/백엔드 담당자의 작업 파일이 완전히 분리되어 git 충돌이 나지 않는다.
**트레이드오프**: 컴포넌트 재사용성과 상태관리 편의성이 낮고, 기능이 늘어나면 단일 파일 유지보수가 어려워진다.

### ADR-003: 성공 응답 200 통일 + JSON 키 snake_case 통일
**결정**: 201/204를 쓰지 않고 모든 성공 응답을 200 OK로 통일한다 (DELETE도 body 있는 200). JSON 키는 전부 snake_case로 통일한다.
**이유**: 서로 다른 개발자가 프론트/백엔드를 나눠 만들 때 응답 처리 로직을 단순화하고, status code/케이스 표기 불일치로 인한 버그를 방지한다.
**트레이드오프**: REST 관례(생성 시 201, 삭제 시 204)를 따르지 않아 외부에서 보면 비표준 API로 보일 수 있다.

### ADR-004: Gemini API를 SDK 없이 requests로 직접 호출
**결정**: `google-generativeai` 등 공식 SDK 대신 `requests`로 Gemini REST API를 직접 호출한다. `requirements.txt`는 fastapi, uvicorn, requests, python-dotenv로 고정한다.
**이유**: 의존성을 최소화해 배포(Railway) 환경 설정과 관리 비용을 줄인다.
**트레이드오프**: SDK가 제공하는 재시도/타입 안정성 등 편의 기능이 없어 timeout·에러 파싱을 직접 구현해야 한다 (timeout=30, try/except 필수).

### ADR-005: 에러 응답 형식 이원화 (HTTPException vs FastAPI 자동 422)
**결정**: 백엔드가 직접 던지는 에러는 `{"detail": "문자열"}`, FastAPI가 자동으로 만드는 검증 에러(422)는 `{"detail": [{"loc":...,"msg":...,"type":...}]}` 배열 그대로 둔다. FastAPI 기본 동작을 억지로 통일시키지 않는다.
**이유**: FastAPI의 기본 422 처리 방식을 오버라이드하는 비용보다, 프론트에서 `detail`의 타입을 분기 처리하는 편이 백엔드 구현이 단순하고 실수가 적다.
**트레이드오프**: 프론트엔드가 항상 `typeof detail === "string"` 체크를 해야 하며, 빼먹으면 `[object Object]`가 화면에 노출되는 버그가 생긴다.
