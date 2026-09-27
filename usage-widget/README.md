# AI 사용량 위젯 (Windows)

Claude와 ChatGPT 구독 플랜의 남은 사용량과 리셋 시각을 화면 구석에 작은 창으로 띄워요.

## 준비

1. Python 3.9 이상 설치 (python.org 설치본에는 tkinter가 포함돼 있어요)
2. **Claude**: [Claude Code](https://claude.com/claude-code)에 Pro/Max 계정으로 로그인 (`claude` 실행 → `/login`)
3. **ChatGPT**: [Codex CLI](https://github.com/openai/codex)에 ChatGPT 계정으로 로그인 (`codex login`)

추가 패키지 설치는 필요 없어요.

## 실행

- `start_widget.bat` 더블클릭 (콘솔 창 없이 실행)
- 콘솔에서 값만 확인: `python usage_widget.py --once`
- 시작 시 자동 실행: `Win+R` → `shell:startup` → 열린 폴더에 `start_widget.bat` 바로가기 추가

## 조작

- 왼쪽 드래그: 이동 (위치는 `~/.usage_widget.json`에 저장)
- 오른쪽 클릭: 새로고침 / 항상 위에 표시 켜기·끄기 / 종료
- 5분마다 서버 조회, 30초마다 남은 시간 표시 갱신

## 표시 항목

| 서비스 | 항목 | 데이터 출처 |
|---|---|---|
| Claude | 5시간, 주간(+ 모델별 주간 한도가 있으면 표시) | `~/.claude/.credentials.json` 토큰 → `api.anthropic.com/api/oauth/usage` |
| ChatGPT | 5시간, 주간 (Codex 한도) | `~/.codex/auth.json` 토큰 → `chatgpt.com/backend-api/wham/usage`, 실패 시 `~/.codex/sessions` 로그 |

## 한계

- 두 엔드포인트 모두 **비공식**이에요. 서비스 쪽에서 바뀌면 동작하지 않을 수 있어요.
- ChatGPT 쪽은 **Codex 사용 한도**예요. ChatGPT 웹 채팅의 모델별 메시지 한도(예: 주간 Thinking 횟수)는 조회할 방법이 없어 표시하지 않아요.
- 로그 대체 경로로 표시될 때는 마지막으로 Codex를 쓴 시점의 값이에요(헤더에 `로그 날짜` 표시).
- Claude 토큰이 만료되면 위젯이 refresh token으로 갱신하고 `~/.claude/.credentials.json`에 다시 저장해요. 이때 Claude Code가 켜져 있으면 가끔 재로그인(`/login`)을 요구할 수 있어요.
- 토큰은 로컬 파일에서 읽어 각 서비스 공식 도메인으로만 보내요.
