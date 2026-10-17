# 테스트와 배포

## 테스트

| 종류 | 명령 | 네트워크·키 | 언제 |
| --- | --- | --- | --- |
| 단위·통합 + 앱 렌더링 | `python -m pytest -q` | 불필요 (가짜 HTTP·가짜 색인, Streamlit AppTest) | 푸시·PR마다 GitHub Actions `tests` 워크플로가 자동 실행 |
| 실제 소스 점검 | `python scripts/smoke.py --trace` | 필요 (DART 키, SEC User-Agent) | 수동. GitHub Actions → tests → Run workflow → "실제 API 점검" 체크 (레포 secrets 사용) |
| 앱 안에서 점검 | 사이드바 **연결 점검** 버튼 | 앱에 설정된 키 | Streamlit Cloud 배포 후 키가 읽히는지 확인할 때 |

`smoke.py`는 GLEIF·SEC·DART 중 하나라도 실패하면 종료 코드 1을 낸다. Companies House(보류 중)와 LLM은 실패해도 통과.

## Streamlit Community Cloud 배포

1. share.streamlit.io에서 New app → 레포 `oceanstarlight/fund-reversing-ai`, 브랜치 `main`, 파일 `app/streamlit_app.py`.
2. Advanced settings → Python 3.12, Secrets에 `.streamlit/secrets.toml.example` 형식으로 키 입력:
   ```toml
   DART_API_KEY = "..."
   SEC_USER_AGENT = "이름 이메일@example.com"
   GEMINI_API_KEY = "..."
   TRACER_LLM_PROVIDER = "auto"
   ```
   앱이 시작할 때 `st.secrets`의 최상위 문자열 값을 환경 변수로 옮기므로 코드 수정 없이 읽힌다.
3. 배포 후 사이드바 **연결 점검**으로 GLEIF·SEC·DART가 ✅인지 확인하고, 상단 데모 버튼(A 카카오, B 한국쉘석유, B S-OIL, C 고려아연)으로 추적을 확인한다.

## 영국 데이터 (보류)

Companies House 색인(약 3.2GiB)은 Streamlit Cloud의 메모리·임시 디스크 한도에 맞지 않아 보류 중이다. 색인이 없으면 영국 구간은 "확인 불가"로 표시되고 GLEIF 회계상 모회사까지만 추적한다. 로컬에서는 `python scripts/build_ch_index.py`로 쓸 수 있다.
