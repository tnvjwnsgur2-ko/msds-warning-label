# Netlify 배포 가이드

이 프로젝트는 두 부분으로 나눠서 공개합니다.

## 1. Netlify에 올리는 것

Netlify에는 정적 프론트엔드만 올립니다.

```text
netlify/
  index.html
  config.js
  pictograms.json
  ghs-pictograms/
```

이 화면은 사용자가 접속하는 웹페이지입니다.

## 2. 별도 서버에 올리는 것

PDF 업로드, PDF 파싱, 경고표지 PDF 저장은 Python FastAPI 백엔드가 처리합니다.

Netlify 단독으로는 이 Python 백엔드를 그대로 실행하기 어렵기 때문에, 백엔드는 Render, Railway, Fly.io, VPS 같은 곳에 Docker로 배포하는 것을 권장합니다.

백엔드 배포 대상 파일은 프로젝트 루트입니다.

```text
app/
Dockerfile
requirements.txt
```

## 3. 백엔드 배포 후 설정

백엔드 공개 주소가 생기면 예를 들어 다음과 같습니다.

```text
https://msds-api.onrender.com
```

그 다음 `netlify/config.js`를 열어서 다음처럼 바꿉니다.

```js
window.MSDS_API_BASE_URL = "https://msds-api.onrender.com";
```

## 4. 백엔드 CORS 설정

백엔드 서버의 환경변수에 Netlify 주소를 넣어야 합니다.

```bash
ALLOWED_ORIGINS=https://your-site-name.netlify.app
```

커스텀 도메인을 쓰면 그 도메인도 넣습니다.

```bash
ALLOWED_ORIGINS=https://your-site-name.netlify.app,https://msds.example.com
```

## 5. Netlify 업로드 방법

### 쉬운 방법

1. Netlify 로그인
2. Add new site
3. Deploy manually 또는 Drag and drop 선택
4. `netlify` 폴더를 업로드
5. 사이트 주소 확인

### GitHub 연결 방법

1. 프로젝트 전체를 GitHub에 업로드
2. Netlify에서 GitHub 저장소 연결
3. Publish directory를 `netlify`로 설정
4. Build command는 비워둠

프로젝트 루트에 `netlify.toml`이 있으므로 GitHub 연결 시 Netlify가 publish 폴더를 자동으로 읽을 수 있습니다.

## 6. 꼭 확인할 것

- Netlify 페이지가 열리는지
- PDF 업로드 시 백엔드로 요청이 가는지
- 백엔드 CORS 에러가 없는지
- 그림문자가 보이는지
- PDF 저장 시 한글이 깨지지 않는지
- HTTPS 주소에서 동작하는지
