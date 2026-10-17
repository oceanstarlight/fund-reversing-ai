# 구현 구조 (1단계 뼈대)

기획서는 `README.md`. 이 문서는 지금 코드가 기획서의 어디를 어떻게 구현했는지 정리한다.

## 흐름

```
입력(기업명·LEI·사업자번호·종목코드·접두어 식별자)
  └ Tracer.resolve ── 이름이 정확·유일하게 맞지 않으면 후보 목록을 돌려주고 멈춤 (사람 선택)
      └ BFS 추적 (법인마다)
          ├ link: 식별자 연결 (DART 사업자번호 ↔ GLEIF registeredAs, GLEIF RA000585 ↔ Companies House 번호, 정확한 이름 매칭)
          ├ 관할별 소유 소스: KR → DART 최대주주 현황 · GB → Companies House PSC · US → SEC 13D/13G
          ├ GLEIF: 직접·최종 연결 모회사(회계상), 미보고 사유
          │    소유 소스가 답하면 회계상 모회사는 표시만, 답이 없을 때만 따라 올라감
          ├ 보유자 이름 → 등록 법인 연결 (resolve_holder, 확신도 3단계)
          └ 종착 판정: 개인 · 재단 · 공공기관 · 운용사/펀드 · 분산 소유 · 공개 데이터의 끝 · 미지원 관할 · 제한
  └ find_cycles (Tarjan SCC) → 순환출자·상호출자 플래그
  └ finalize_review → 사람 검토 목록 (우선순위 정렬)
출력: OwnershipGraph → Graphviz DOT / Markdown / JSON / Streamlit
```

## 파일

| 파일 | 역할 |
| --- | --- |
| `tracer/models.py` | Entity · Edge · Evidence · ReviewItem · OwnershipGraph. 확신도(확인됨/추정/확인 불가), 종착 유형 |
| `tracer/orchestrator.py` | 입력 정규화, 라우팅, 재귀 추적, 순환 감지, 검토 목록 |
| `tracer/sources/dart.py` | 고유번호 목록, 기업개황(사업자번호), 최대주주 현황(hyslrSttus) |
| `tracer/sources/gleif.py` | LEI 조회·검색, 직접/최종 모회사, 미보고 사유 → 종착 매핑 |
| `tracer/sources/sec.py` | 티커 목록, submissions, 구조화 13D/13G XML 파싱 (구형식 텍스트는 검토 목록) |
| `tracer/sources/companies_house.py` | 회사 검색, PSC, PSC 진술(statement) — 로컬 일괄 데이터 색인에서 읽음 |
| `tracer/sources/ch_index.py` + `scripts/build_ch_index.py` | Companies House 무료 일괄 데이터(PSC 스냅샷 일 단위, 회사 기본정보 월 단위)를 SQLite로 적재. 계정·키 불필요 |
| `tracer/names.py` | 이름 정규화(법인 형태 제거), rapidfuzz 매칭, 보유자 유형 규칙 |
| `tracer/http.py` | 디스크 캐시(같은 추적 재실행 시 API 비용 0), 호스트별 호출 간격, 키 제거된 근거 URL |
| `tracer/extract.py` + `tracer/llm.py` | 유일한 LLM 사용처: LPA·PPM에서 지분 조항 추출, 원문 인용 검증 통과분만 "추정"으로 |
| `tracer/events/root_cause.py` | 보조 모듈: 이벤트 일자 전후 DART 공시를 규칙으로 분류·순위화, 소유 변동이면 재추적 |
| `tracer/report.py` | DOT · Markdown · JSON |
| `app/streamlit_app.py` | UI (지원 범위 고정 표시, 소스 상태, 그래프, 근거 표, 검토 목록) |
| `scripts/trace.py` | CLI |

## 신뢰성 규칙 (코드로 강제)

- 지분율은 소스 원문 값만 쓴다. `share_text`는 원문 그대로, `share_pct`는 원문이 숫자 하나일 때만.
- Companies House는 구간(25~50%)으로만 공시하므로 구간 그대로 둔다.
- GLEIF 모회사는 `ACCOUNTING_PARENT`로 따로 표시. 교차 검증(FULLY_CORROBORATED)이 아니면 "추정".
- GLEIF가 "개인이 지배(NATURAL_PERSONS)"라고만 하면 이름이 없으므로 개인 종착이 아니라 "추가 확인 필요"로 끝낸다.
- 이름 매칭: 유사도 97 이상 + 후보 유일 → 확인됨, 88 이상 → 추정 + 검토, 그 미만 → 연결 안 함(검토).
- 소스 실패·키 없음은 예외가 아니라 `Unknown`으로 남고 화면에 표시된다. `Unknown.message`에는 숫자를 쓰지 않는다.

## 비용

- 추적은 전부 결정적 코드라 LLM 비용 0. LLM은 문서 추출을 명시적으로 돌릴 때만.
- 모든 소스 무료: GLEIF(키 없음), SEC(키 없음, 연락처 User-Agent), OpenDART(무료 키, 일 2만 건), Companies House(일괄 데이터, 키 없음).
- Companies House는 API 가입에 영국 인증 절차가 필요해 일괄 데이터로 대체했다. 대가는 최신성(PSC 하루, 회사명 한 달)과 로컬 디스크 사용.
- HTTP 응답은 24시간 디스크 캐시(DART 고유번호 목록은 7일).

## kr-fund-ai-agent에서 가져온 것

- `.env` 로더와 설정 방식 (`sentinel/config.py`)
- LLM 공급자 라우팅·구조화 출력·응답 캐시·재시도 (`sentinel/agents/llm.py` → `tracer/llm.py`, 문서 추출용으로 축소)
- "값 + 근거(evidence) + 한계(unknowns)" 도구 반환 원칙과 숫자 없는 unknown 메시지 규칙
- 오프라인 테스트 원칙 (가짜 HTTP, 키·네트워크 불필요)

## 아직 안 한 것 (다음 단계)

- 데모 대상 기업 선정 후 실데이터 검증 (사례 A·B·C)
- SEC 구형식(SC 13D/G 텍스트) 파싱, 13D 지배 지분 판정 세분화
- DART 타법인출자현황·대량보유보고서로 교차 확인, 정정 공시 반영 로직
- 이벤트 모듈의 뉴스 소스, 주가 이상 자동 감지
- 문서 업로드(LPA·PPM) UI 연결
- 동명이인 처리(현재 개인은 이름 기준으로 병합)
