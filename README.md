# MSDS Warning Label Web App

MSDS PDF를 업로드하면 경고표지에 필요한 정보를 추출하고, 사용자가 수정한 뒤 미리보기/인쇄/PDF 저장을 할 수 있는 FastAPI 웹앱입니다.

## 포함된 자산

GHS 그림문자는 앱 패키지 안에 포함되어 있습니다.

```text
app/static/ghs_pictograms/
```

따라서 서버에 배포할 때 별도의 `C:\...\msds\GHS_그림문자` 폴더가 없어도 동작합니다.

## 로컬 실행

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

브라우저에서 엽니다.

```text
http://127.0.0.1:8000
```

## 공개 서버 실행

공개 서버에서는 외부 접속을 받기 위해 `0.0.0.0`으로 실행합니다.

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Docker 실행

```bash
docker build -t msds-warning-label .
docker run -p 8000:8000 msds-warning-label
```

Docker 이미지에는 Linux용 한글 폰트(`fonts-nanum`)가 설치되어 PDF 저장 시 한글이 깨지지 않도록 구성되어 있습니다.

## 배포 개념

사용자는 공개 URL에 접속해서 다음 흐름으로 사용합니다.

1. MSDS PDF 업로드
2. 자동 추출 결과 확인
3. 제품명, 신호어, 그림문자, 문구, 공급자 정보 수정
4. 경고표지 미리보기 확인
5. PDF 저장 또는 인쇄

업로드된 PDF는 임시 파일로 처리한 뒤 삭제됩니다.
## 운영 보안 기본값

앱에는 공개 운영을 위한 최소 보호장치가 들어 있습니다.

- 업로드 PDF 파일 크기 제한: 기본 20MB
- 한 번에 업로드 가능한 파일 수 제한: 기본 5개
- PDF 헤더 검사: `%PDF-`로 시작하지 않는 파일 거부
- PDF 페이지 수 제한: 기본 50쪽
- 요청 횟수 제한: 기본 60초당 30회
- 업로드 파일은 임시 파일로 처리 후 삭제
- CORS 허용 출처 제한
- 기본 보안 헤더 추가
- Docker 컨테이너 non-root 사용자 실행

환경변수로 조정할 수 있습니다.

```bash
MAX_UPLOAD_BYTES=20971520
MAX_UPLOAD_FILES=5
MAX_PDF_PAGES=50
RATE_LIMIT_REQUESTS=30
RATE_LIMIT_WINDOW_SECONDS=60
ALLOWED_ORIGINS=https://your-domain.com
```

공개 배포 시에는 반드시 HTTPS가 적용된 URL에서 운영하세요.
