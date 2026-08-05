# Netlify + Render 배포

이 앱은 정적 UI와 Python PDF 처리 API로 구성되므로 두 서비스로 배포합니다.

## Render 백엔드

저장소의 `render.yaml` 또는 `Dockerfile`로 `msds-warning-label-api` 서비스를 배포합니다.

- Health check: `/health`
- 기본 주소: `https://msds-warning-label-api.onrender.com`
- 업로드 PDF는 임시 처리 후 삭제
- 생성 PDF는 다운로드 직전의 임시 런타임 파일이며 서버에 영구 보관하지 않음

Render 서비스 주소가 달라지면 `netlify/config.js`, `netlify.toml`의 API 주소를 수정합니다.

## Netlify 프론트엔드

GitHub 저장소를 Netlify에 연결하면 루트 `netlify.toml`이 다음 설정을 적용합니다.

- Publish directory: `netlify`
- Build command: 없음
- 브라우저의 생성·저장 요청: `netlify/config.js`의 Render API를 직접 호출
- `/api/*` 프록시: 이전에 열린 페이지를 위한 호환 경로

Netlify 사이트 주소는 Render의 `ALLOWED_ORIGINS`에 추가해야 합니다. 긴 PDF 처리 요청은 Netlify
프록시의 응답 제한을 피하기 위해 Render로 직접 보내고, 그림문자도 CSP에서 Render 주소를 허용합니다.

## 배포 확인

1. Netlify 페이지 표시
2. PDF 최대 10개 선택 및 두 생성 버튼 동작
3. 그림문자 표시
4. PDF별 작업명 입력과 빈 값 저장·인쇄 차단
5. 공급자 전화번호 표시
6. 최종 편집 내용의 PDF 다운로드와 인쇄
