# Netlify + Render 배포

이 앱은 정적 UI와 Python PDF 처리 API로 구성되므로 두 서비스로 배포합니다.

## Render 백엔드

저장소의 `render.yaml` 또는 `Dockerfile`로 `msds-warning-label-api` 서비스를 배포합니다.

- Health check: `/health`
- 기본 주소: `https://msds-warning-label-api.onrender.com`
- 업로드 PDF는 임시 처리 후 삭제
- 생성 PDF는 다운로드 직전의 임시 런타임 파일이며 서버에 영구 보관하지 않음

Render 서비스 주소가 달라지면 `netlify.toml`의 프록시 목적지를 수정합니다.

## Netlify 프론트엔드

GitHub 저장소를 Netlify에 연결하면 루트 `netlify.toml`이 다음 설정을 적용합니다.

- Publish directory: `netlify`
- Build command: 없음
- `/api/*`: Render API로 프록시

Netlify 사이트 주소를 Render의 `ALLOWED_ORIGINS`에 추가할 수 있습니다. 현재 프론트엔드는
Netlify 프록시를 사용하므로 브라우저 요청은 동일 출처로 동작합니다.

## 배포 확인

1. Netlify 페이지 표시
2. PDF 최대 10개 선택 및 두 생성 버튼 동작
3. 그림문자 표시
4. PDF별 작업명 입력과 빈 값 저장·인쇄 차단
5. 공급자 전화번호 표시
6. 최종 편집 내용의 PDF 다운로드와 인쇄
