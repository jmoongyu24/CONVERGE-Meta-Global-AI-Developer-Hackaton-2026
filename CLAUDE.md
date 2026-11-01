# CLAUDE.md

Converge (가칭): 연구실 회의록(Notion)에서 연구 아이디어의 lineage·담당자·stance를 추출해 시각화하고, multi-agent 토론으로 연구 방향 수렴을 돕는 서비스. Meta Global AI Developer Hackathon 2026 출품작.

- 상세 계획·일정: [docs/PLAN.md](docs/PLAN.md)
- 마감: 11/5(목) 11:00 KST (내부 목표 09:00)

## 협업 규칙 (사용자 지정, 항상 준수)

1. 모든 작업은 사용자가 이해하고 따라올 수 있어야 한다. 한 번에 한 기능(F#)씩 진행하고, 기능이 끝날 때마다 확인을 받는다.
2. English 기술 용어는 원어 그대로 쓰고, 한국어는 자연스러운 문체로 쓴다. 억지 번역 금지.
3. 작업 보고는 장황하지 않게, **작업한 파일명별로 핵심만** 쓴다.
4. 코드 파일을 늘리지 않는다. 새 파일을 만들기 전에 기존 파일에 넣을 수 있는지 먼저 검토하고, 비슷한 역할의 함수·코드는 한 파일에 모은다.
5. 모델 학습이 필요하면 학습 전에 아래 항목을 먼저 보고하고 승인받는다.
   - 학습 이유 / 사용 모델 / 사용 데이터 / 그 모델·데이터를 고른 이유
   - 예상 GPU memory를 항목별 GB로: weights, gradients, optimizer states, activations, 기타 (CUDA context, buffer 등)
   - 예상 학습 시간 (로컬 GPU: RTX 4070 Ti SUPER 16GB)
6. commit·push는 사용자가 직접 한다. 작업이 끝나면 추천 commit message(와 branch 이름)를 제시한다.
7. 사용자의 실제 연구 기록(Notion 등)에는 접근하지 않는다. Notion API는 사용자가 지정한 test page에만 쓰고, `.env`는 열지 않는다.

## Commands

| 목적 | 명령 (repo root 기준) |
|---|---|
| backend 실행 (dev) | `cd backend && uv run uvicorn app.main:app --reload` → http://localhost:8000/docs |
| frontend 실행 (dev) | `cd frontend && npm run dev` → http://localhost:5173 (`/api`는 :8000으로 proxy) |
| 배포용 build | `cd frontend && npm run build` → FastAPI가 `frontend/dist`를 http://localhost:8000 에서 serve |
| test | `cd backend && uv run pytest` |
| demo 데이터 적재 | `cd backend && uv run python -m app.ingest demo` → `data/converge.db` |
| Notion import | `cd backend && uv run python -m app.ingest notion <page_id> --team <team.json>` |
| Python lint·format | `cd backend && uv run ruff format . && uv run ruff check --fix .` |
| TS lint·format | `cd frontend && npm run check` |

- Node는 WSL 안의 nvm 설치본을 쓴다. Windows 쪽 npm(`/mnt/c/...`)은 쓰지 않는다.
- 환경 변수는 repo root의 `.env`에 둔다 (템플릿: `.env.example`).

## Git 규칙

- **Branch**: `main`은 항상 동작하는 상태로 유지한다. 작업은 `<type>/<feature-id>-<short-desc>` branch에서 한다. (예: `feat/f2-extract-link`, `fix/f4-slider-lag`, `docs/plan`)
- **Commit message**: Conventional Commits

  ```
  <type>(<scope>): <subject>

  <body: 무엇을, 왜 (선택)>
  ```

  - type: `feat` 기능 · `fix` 버그 · `refactor` · `perf` · `test` · `docs` · `style` · `chore` · `build` · `ci`
  - scope: `ingest` · `pipeline` · `converge` · `api` · `web` · `eval` · `infra` (생략 가능)
  - subject: English, 명령형 현재시제, 소문자로 시작, 마침표 없이 72자 이내
  - 예: `feat(pipeline): verify extracted quotes against source lines`
  - 한 commit = 한 가지 논리적 변경
- **PR**: 기능(F#) 1개 = PR 1개. 제목은 commit 형식, 본문은 What / Why / How to test / Screenshot. squash merge 후 branch 삭제.
- **Tag**: `v0.1.0` (backend MVP) · `v0.2.0` (UI) · `v1.0.0` (제출본)

## 이름 규칙

| 대상 | 규칙 | 예 |
|---|---|---|
| Python module | `snake_case.py` | `pipeline.py` |
| Python test | `test_<module>.py` | `test_pipeline.py` |
| React component 파일 | `PascalCase.tsx` | `LineageView.tsx` |
| 그 외 TS 파일 | `camelCase.ts` | `api.ts` |
| 메타 문서 | `UPPERCASE.md` | `README.md`, `PLAN.md` |
| 회의록 데이터 | `YYYY-MM-DD-<slug>.md` | `2026-09-07-weekly-01.md` |
| 환경 변수 | `UPPER_SNAKE_CASE` | `MODEL_API_KEY` |
| Python 식별자 | 함수·변수 `snake_case`, class `PascalCase`, 상수 `UPPER_SNAKE_CASE` | |
| TS 식별자 | 함수·변수 `camelCase`, component·type `PascalCase` | |
| API path | 소문자, 복수 명사 | `/api/ideas/{id}` |

## 코드 규칙

- 코드·주석·commit message·README는 English, 계획 문서(`docs/`)는 한국어로 쓴다.
- 모든 LLM 호출은 `backend/app/llm.py`를 거친다 (cache, retry, 비용 로깅). prompt는 그것을 쓰는 module(`pipeline.py`, `converge.py`) 상단에 상수로 둔다.
- LLM 입출력과 API 데이터는 `models.py`의 Pydantic model로 정의한다.
- **No quote, no claim**: LLM이 만든 모든 주장은 원문 quote + source id를 가져야 하고, 검증에 실패하면 버린다.
- 실제 연구 노트에는 `-contributor` 모델을 쓰지 않는다 (입력이 Meta 학습에 사용됨). standard model만 쓴다.
- Secret은 `.env`(git 제외)에만 두고, 필요한 key 목록은 `.env.example`에 적는다.
- Python은 type hint 필수. 주석은 "왜"만 쓴다.
- commit 전에 lint·format·test를 통과시킨다.
