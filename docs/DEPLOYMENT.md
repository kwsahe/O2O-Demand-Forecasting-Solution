# 배포 선택과 운영 구성

## 결론

현재 프로젝트는 **Flask + Docker로 먼저 배포**하는 것이 가장 합리적이다. 둘 중 하나를 반드시 고르면
**FastAPI**가 Streamlit보다 적합하지만, 배포만을 위해 즉시 마이그레이션할 실익은 작다.

| 선택지 | 장점 | 현재 프로젝트의 비용 | 판단 |
|---|---|---|---|
| Flask 유지 | 이미 완성된 Jinja·Vanilla JS·Chart.js·지도 UI를 그대로 사용 | API 문서 자동화는 별도 작업 | **지금 배포에 추천** |
| FastAPI | 타입 검증, OpenAPI 문서, 비동기 작업, 프론트 분리에 유리 | Flask 라우트·테스트·템플릿 연결을 다시 작성 | **차기 리팩터링 후보** |
| Streamlit | 분석 데모를 빠르게 공개 | 현재 UI와 상호작용을 대부분 재작성하고 백엔드 설계가 가려짐 | 내부 분석 프로토타입 외에는 비추천 |

FastAPI는 배포 플랫폼이 아니라 웹 프레임워크다. 실제 공개에는 Docker와 Render, Railway, Fly.io,
Cloud Run 또는 VPS 같은 실행 환경이 추가로 필요하다.

## 권장 구성

```text
Browser
  -> Flask/Gunicorn container
       -> committed result CSV/JSON
       -> public data APIs (manual/admin refresh only)
       -> optional Ollama service
```

포트폴리오 데모에서는 수집을 요청마다 실행하지 않고 검증된 결과 파일을 읽기 전용으로 제공한다.
`/api/collect`는 `ADMIN_TOKEN`으로 보호하고, 긴 수집은 별도 배치 작업으로 분리하는 것이 좋다.

Ollama는 일반적인 서버리스 호스팅에 기본 포함되지 않는다. 배포 환경에 Ollama가 없다면 Qwen 버튼은
연결 오류를 안내하고 결정론적 지역 분석은 계속 동작한다. 완전한 온라인 AI 데모가 필요하면 GPU VPS에
Ollama를 별도 서비스로 띄우거나 외부 추론 API를 선택적으로 연결해야 한다.

## Docker 실행

```powershell
docker build -t o2o-demand .
docker run --rm -p 8300:8300 --env-file .env o2o-demand
```

확인 주소:

- 대시보드: `http://127.0.0.1:8300`
- 상태 확인: `http://127.0.0.1:8300/health`
- 지역 분석: `http://127.0.0.1:8300/region`

## 배포 전 점검

1. `.env`를 이미지와 Git에 포함하지 않는다.
2. `FLASK_DEBUG=false`, 충분히 긴 `ADMIN_TOKEN`을 설정한다.
3. Naver Maps Web 서비스 URL에 실제 배포 도메인을 등록한다.
4. 결과 CSV·JSON의 라이선스와 공개 가능 범위를 확인한다.
5. 원천 대용량 파일을 이미지에 포함할지 객체 스토리지에서 받을지 결정한다.
6. `/health`를 플랫폼 헬스 체크 경로로 설정한다.
7. Ollama가 없을 때 AI 기능만 실패하고 나머지 페이지가 정상 동작하는지 확인한다.

## FastAPI 전환 시점

다음 조건이 생기면 별도 브랜치에서 FastAPI로 옮긴다.

- 프론트엔드를 React/Vue 등 별도 앱으로 분리할 때
- Pydantic 요청·응답 스키마와 자동 OpenAPI 문서가 채용 포지션에 직접 도움이 될 때
- 장기 수집 작업을 큐와 비동기 상태 API로 분리할 때
- 다수 사용자의 인증·권한·사용량 제한이 필요할 때

전환 순서는 데이터 서비스 계층 분리 → Flask/FastAPI 공용 함수화 → 읽기 API 이관 → 수집 작업 이관 →
템플릿 또는 별도 프론트 연결 순서가 안전하다.
