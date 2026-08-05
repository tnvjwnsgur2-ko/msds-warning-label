# MSDS 경고표지·관리요령 웹앱

최대 10개의 MSDS PDF를 처리해 PDF별 W-1~W-6 경고표지와 M-1~M-5 관리요령을 생성하는 FastAPI 웹앱입니다.
자동 추출 결과는 화면에서 수정할 수 있고, 수정된 최종 내용과 그림문자가 PDF 저장과 인쇄에 사용됩니다.

## 주요 기능

- PDF별 W-1~W-6 실행 및 실제 GHS 그림문자 미리보기
- PDF별 M-1~M-5 실행
- 관리요령 작업명을 PDF마다 별도로 입력하고 빈 값 저장·인쇄 차단
- M-4는 보호구 명칭만 표시
- W-6에 원문 공급자 전화번호 포함
- 한 PDF가 실패해도 나머지 PDF 계속 처리
- 업로드 파일은 임시 디렉터리에서 처리 후 삭제

## 로컬 실행

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

브라우저에서 `http://127.0.0.1:8000`을 엽니다.

## 테스트

```bash
.venv/bin/python -m pytest -q
```

120개 공식 MSDS의 학습·검증 결과는 `TRAINING_VALIDATION_REPORT.md`에 정리되어 있습니다.

## 배포 구조

- `netlify/`: 정적 프론트엔드
- `app/`: Render에서 실행하는 FastAPI 백엔드
- `netlify.toml`: `/api/*`를 Render 백엔드로 프록시
- `render.yaml`, `Dockerfile`: Render Blueprint/Docker 배포 설정

배포 절차는 `NETLIFY_DEPLOY.md`를 참고하세요.

## 운영 기본값

- 최대 PDF 수: 10개
- 파일당 최대 크기: 25MB
- PDF 서명 검사
- POST 요청 제한: IP당 60초에 30회
- CORS 및 기본 보안 헤더 적용
- Docker non-root 사용자 실행

환경변수:

```bash
MAX_UPLOAD_BYTES=26214400
MAX_UPLOAD_FILES=10
RATE_LIMIT_REQUESTS=30
RATE_LIMIT_WINDOW_SECONDS=60
ALLOWED_ORIGINS=https://your-site.netlify.app
```
