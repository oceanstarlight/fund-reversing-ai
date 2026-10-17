반말로 말 할 것.

# 작업 원칙 (최우선)

1. **애매하거나 모르는 부분은 반드시 질문한다.** 작업 중 판단이 애매하거나 모르는 부분이 생기면 추측으로 진행하지 말고 사용자에게 먼저 질문한다.
2. **최소 비용으로 최고 성능을 내도록 설계한다.**
   - 결정적 코드(스크립트, 규칙, 매칭)로 해결되는 일은 코드로 처리하고, LLM 호출은 꼭 필요한 곳에만 쓴다.
   - 무료·오픈소스 도구와 무료 API 등급을 우선 검토한다.
   - 설계안을 낼 때는 비용과 성능의 트레이드오프를 함께 밝힌다.

# 프로젝트 메모 (지배구조 역추적 에이전트)

- 기획서: `README.md`. 구현 구조: `docs/architecture.md`.
- 테스트: `python -m pytest -q` (네트워크·API 키 불필요, `tests/fakes.py`의 가짜 HTTP 사용)
- CLI: `python scripts/trace.py "기업명|LEI|사업자번호|종목코드"`
- 앱: `streamlit run app/streamlit_app.py`
- 실제 소스 점검: `python scripts/smoke.py --trace`. 배포·테스트 방법은 `docs/deploy.md`
- 영국 데이터(보류 중): `python scripts/build_ch_index.py` (Companies House 일괄 데이터 → `data/ch/companies_house.sqlite`, API 키 안 씀)
- 추적·매칭·확신도 판정은 전부 결정적 코드(`tracer/orchestrator.py`, `tracer/sources/*`)가 한다.
  LLM은 문서(LPA·PPM)에서 지분 조항을 뽑는 `tracer/extract.py`에만 쓰고, 원문 인용이 확인된 값만 남기며 결과는 항상 "추정"+사람 검토로 보낸다.
- 지분율은 소스 원문 값만 쓴다. 범위로 공시된 값(Companies House 25~50% 등)은 범위 그대로 표시한다.
- 소스 결과는 값과 함께 근거(Evidence)와 한계(Unknown)를 반환한다. Unknown.message에는 숫자를 쓰지 않는다(LLM 입력용).