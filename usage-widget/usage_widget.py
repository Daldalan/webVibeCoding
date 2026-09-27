"""Claude / ChatGPT(Codex) 구독 사용량 데스크톱 위젯 (Windows용, 표준 라이브러리만 사용).

- Claude: Claude Code 로그인 토큰(~/.claude/.credentials.json)으로 사용량 엔드포인트 조회
- ChatGPT: Codex CLI 로그인 토큰(~/.codex/auth.json)으로 사용량 엔드포인트 조회,
  실패하면 Codex 세션 로그(~/.codex/sessions)에 남은 마지막 한도 정보를 사용

두 엔드포인트 모두 비공식이라 예고 없이 바뀔 수 있다.
"""

import glob
import json
import os
import re
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone

REFRESH_SECONDS = 300  # 서버 조회 주기
TICK_SECONDS = 30  # 남은 시간 표시 갱신 주기
HTTP_TIMEOUT = 15

HOME = os.path.expanduser("~")
CLAUDE_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(HOME, ".claude")
CODEX_DIR = os.environ.get("CODEX_HOME") or os.path.join(HOME, ".codex")
STATE_FILE = os.path.join(HOME, ".usage_widget.json")


class FetchError(Exception):
    pass


# ---------------------------------------------------------------- 공통 유틸


def http_get_json(url, headers):
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise FetchError("인증 만료 (CLI 재로그인 필요)")
        raise FetchError(f"HTTP {e.code}")
    except (urllib.error.URLError, TimeoutError) as e:
        raise FetchError(f"네트워크 오류: {getattr(e, 'reason', e)}")


def parse_iso(value):
    if not value:
        return None
    s = value.replace("Z", "+00:00")
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)  # 소수점 이하 7자리 이상 잘라내기
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def from_epoch(value):
    return datetime.fromtimestamp(float(value), tz=timezone.utc) if value else None


def window_label(minutes):
    if not minutes:
        return "한도"
    if minutes % 10080 == 0:
        return "주간" if minutes == 10080 else f"{minutes // 10080}주"
    if minutes % 1440 == 0:
        return f"{minutes // 1440}일"
    if minutes % 60 == 0:
        return f"{minutes // 60}시간"
    return f"{minutes}분"


def make_window(label, used_percent, resets_at):
    """used_percent는 0~100. 리셋 시각이 이미 지났으면 사용량을 0으로 본다."""
    used = max(0.0, min(100.0, float(used_percent or 0)))
    now = datetime.now(timezone.utc)
    if resets_at and resets_at <= now:
        used, resets_at = 0.0, None
    return {"label": label, "used": used, "resets_at": resets_at}


# ---------------------------------------------------------------- Claude


def fetch_claude():
    path = os.path.join(CLAUDE_DIR, ".credentials.json")
    try:
        with open(path, encoding="utf-8") as f:
            oauth = json.load(f)["claudeAiOauth"]
    except FileNotFoundError:
        raise FetchError("Claude Code 로그인 정보 없음")
    except (KeyError, json.JSONDecodeError):
        raise FetchError("credentials.json 형식 오류")

    expires_at = oauth.get("expiresAt")
    if expires_at and expires_at / 1000 < datetime.now(timezone.utc).timestamp():
        raise FetchError("토큰 만료 (Claude Code 한 번 실행)")

    data = http_get_json(
        "https://api.anthropic.com/api/oauth/usage",
        {
            "Authorization": f"Bearer {oauth['accessToken']}",
            "anthropic-beta": "oauth-2025-04-20",
            "Content-Type": "application/json",
            "User-Agent": "usage-widget/1.0",
        },
    )

    windows = []
    for key, label in (
        ("five_hour", "5시간"),
        ("seven_day", "주간"),
        ("seven_day_opus", "주간 Opus"),
        ("seven_day_sonnet", "주간 Sonnet"),
    ):
        w = data.get(key)
        if not w or w.get("utilization") is None:
            continue
        windows.append(make_window(label, w["utilization"], parse_iso(w.get("resets_at"))))
    if not windows:
        raise FetchError("응답에 사용량 정보 없음")
    return {"windows": windows, "source": "API"}


# ---------------------------------------------------------------- ChatGPT (Codex)


def fetch_chatgpt():
    api_error = None
    try:
        return fetch_chatgpt_api()
    except FetchError as e:
        api_error = e
    result = fetch_chatgpt_logs()
    if result:
        return result
    raise api_error


def fetch_chatgpt_api():
    path = os.path.join(CODEX_DIR, "auth.json")
    try:
        with open(path, encoding="utf-8") as f:
            tokens = json.load(f)["tokens"]
        access_token = tokens["access_token"]
    except FileNotFoundError:
        raise FetchError("Codex CLI 로그인 정보 없음")
    except (KeyError, TypeError, json.JSONDecodeError):
        raise FetchError("auth.json에 ChatGPT 로그인 토큰 없음")

    headers = {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": "codex_cli_rs",
        "Accept": "application/json",
    }
    if tokens.get("account_id"):
        headers["ChatGPT-Account-Id"] = tokens["account_id"]
    data = http_get_json("https://chatgpt.com/backend-api/wham/usage", headers)

    rate = data.get("rate_limit") or {}
    windows = []
    for key in ("primary_window", "secondary_window"):
        w = rate.get(key)
        if not w:
            continue
        minutes = (w.get("limit_window_seconds") or 0) // 60
        resets_at = from_epoch(w.get("reset_at"))
        if not resets_at and w.get("reset_after_seconds") is not None:
            resets_at = from_epoch(datetime.now(timezone.utc).timestamp() + w["reset_after_seconds"])
        windows.append(make_window(window_label(minutes), w.get("used_percent"), resets_at))
    if not windows:
        raise FetchError("응답에 사용량 정보 없음")
    plan = data.get("plan_type")
    return {"windows": windows, "source": f"API · {plan}" if plan else "API"}


def fetch_chatgpt_logs():
    """가장 최근 Codex 세션 로그의 마지막 rate_limits 이벤트를 읽는다."""
    files = glob.glob(os.path.join(CODEX_DIR, "sessions", "**", "rollout-*.jsonl"), recursive=True)
    for path in sorted(files, key=os.path.getmtime, reverse=True)[:5]:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            continue
        for line in reversed(lines):
            if '"rate_limits"' not in line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = entry.get("payload") or entry.get("msg") or {}
            limits = payload.get("rate_limits")
            if not limits:
                continue
            logged_at = parse_iso(entry.get("timestamp")) or datetime.fromtimestamp(
                os.path.getmtime(path), tz=timezone.utc
            )
            windows = []
            for key in ("primary", "secondary"):
                w = limits.get(key)
                if not w:
                    continue
                resets_at = from_epoch(w.get("resets_at"))
                if not resets_at and w.get("resets_in_seconds") is not None:
                    resets_at = from_epoch(logged_at.timestamp() + w["resets_in_seconds"])
                windows.append(
                    make_window(window_label(w.get("window_minutes")), w.get("used_percent"), resets_at)
                )
            if windows:
                return {"windows": windows, "source": f"로그 {logged_at.astimezone():%m/%d %H:%M}"}
    return None


# ---------------------------------------------------------------- 표시 포맷


def format_reset(resets_at):
    if not resets_at:
        return "리셋 정보 없음"
    now = datetime.now(timezone.utc)
    secs = max(0, int((resets_at - now).total_seconds()))
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    if days:
        left = f"{days}일 {hours}시간"
    elif hours:
        left = f"{hours}시간 {mins}분"
    else:
        left = f"{mins}분"
    local = resets_at.astimezone()
    weekday = "월화수목금토일"[local.weekday()]
    when = f"{local:%H:%M}" if local.date() == datetime.now().date() else f"{local:%m/%d}({weekday}) {local:%H:%M}"
    return f"{when} 리셋 · {left} 남음"


def bar_color(remaining):
    if remaining >= 50:
        return "#4ade80"
    if remaining >= 20:
        return "#fbbf24"
    return "#f87171"


# ---------------------------------------------------------------- UI


def run_ui():
    import tkinter as tk

    BG, CARD, FG, MUTED, TRACK = "#18181b", "#27272a", "#f4f4f5", "#a1a1aa", "#3f3f46"
    WIDTH, BAR_W = 280, 256
    SERVICES = (("Claude", "#d97757", fetch_claude), ("ChatGPT", "#10a37f", fetch_chatgpt))

    state = {}
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError):
        pass

    root = tk.Tk()
    root.title("Usage Widget")
    root.overrideredirect(True)
    root.configure(bg=BG)
    root.attributes("-topmost", state.get("topmost", True))
    root.attributes("-alpha", 0.94)
    root.geometry(f"+{state.get('x', 40)}+{state.get('y', 40)}")

    results = {name: None for name, _, _ in SERVICES}
    body = tk.Frame(root, bg=BG, padx=10, pady=8)
    body.pack(fill="both", expand=True)

    def save_state():
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(
                    {"x": root.winfo_x(), "y": root.winfo_y(), "topmost": bool(root.attributes("-topmost"))}, f
                )
        except OSError:
            pass

    def render():
        for child in body.winfo_children():
            child.destroy()
        for name, color, _ in SERVICES:
            card = tk.Frame(body, bg=CARD, padx=10, pady=8)
            card.pack(fill="x", pady=4)
            head = tk.Frame(card, bg=CARD)
            head.pack(fill="x")
            tk.Label(head, text="●", fg=color, bg=CARD, font=("Segoe UI", 9)).pack(side="left")
            tk.Label(head, text=name, fg=FG, bg=CARD, font=("Segoe UI Semibold", 10)).pack(side="left", padx=4)

            res = results[name]
            if res is None:
                tk.Label(card, text="불러오는 중…", fg=MUTED, bg=CARD, font=("Segoe UI", 9)).pack(anchor="w")
                continue
            if "error" in res:
                tk.Label(
                    card, text=res["error"], fg="#f87171", bg=CARD, font=("Segoe UI", 9), wraplength=BAR_W, justify="left"
                ).pack(anchor="w", pady=(4, 0))
                continue
            tk.Label(head, text=res["source"], fg=MUTED, bg=CARD, font=("Segoe UI", 8)).pack(side="right")

            for w in res["windows"]:
                w = make_window(w["label"], w["used"], w["resets_at"])  # 리셋 시각 경과 반영
                remaining = 100 - w["used"]
                row = tk.Frame(card, bg=CARD)
                row.pack(fill="x", pady=(6, 0))
                tk.Label(row, text=w["label"], fg=FG, bg=CARD, font=("Segoe UI", 9)).pack(side="left")
                tk.Label(
                    row, text=f"{remaining:.0f}% 남음", fg=bar_color(remaining), bg=CARD, font=("Segoe UI Semibold", 9)
                ).pack(side="right")
                bar = tk.Canvas(card, width=BAR_W, height=6, bg=TRACK, highlightthickness=0)
                bar.pack(anchor="w", pady=2)
                bar.create_rectangle(0, 0, BAR_W * remaining / 100, 6, fill=bar_color(remaining), width=0)
                tk.Label(card, text=format_reset(w["resets_at"]), fg=MUTED, bg=CARD, font=("Segoe UI", 8)).pack(
                    anchor="w"
                )
        tk.Label(
            body,
            text=f"갱신 {datetime.now():%H:%M} · 우클릭 메뉴",
            fg=MUTED,
            bg=BG,
            font=("Segoe UI", 7),
        ).pack(anchor="e")
        root.update_idletasks()
        root.geometry(f"{WIDTH}x{root.winfo_reqheight()}")

    def refresh():
        def worker():
            new = {}
            for name, _, fetch in SERVICES:
                try:
                    new[name] = fetch()
                except FetchError as e:
                    new[name] = {"error": str(e)}
                except Exception as e:  # 응답 형식 변경 등 예상 밖 오류도 위젯은 살려둔다
                    new[name] = {"error": f"오류: {e}"}
            root.after(0, lambda: (results.update(new), render()))

        threading.Thread(target=worker, daemon=True).start()

    def schedule_refresh():
        refresh()
        root.after(REFRESH_SECONDS * 1000, schedule_refresh)

    def schedule_tick():
        render()
        root.after(TICK_SECONDS * 1000, schedule_tick)

    # 드래그 이동
    drag = {}

    def on_press(e):
        drag["x"], drag["y"] = e.x_root - root.winfo_x(), e.y_root - root.winfo_y()

    def on_drag(e):
        root.geometry(f"+{e.x_root - drag['x']}+{e.y_root - drag['y']}")

    root.bind("<ButtonPress-1>", on_press)
    root.bind("<B1-Motion>", on_drag)
    root.bind("<ButtonRelease-1>", lambda e: save_state())

    # 우클릭 메뉴
    topmost_var = tk.BooleanVar(value=bool(root.attributes("-topmost")))

    def toggle_topmost():
        root.attributes("-topmost", topmost_var.get())
        save_state()

    def quit_app():
        save_state()
        root.destroy()

    menu = tk.Menu(root, tearoff=0)
    menu.add_command(label="지금 새로고침", command=refresh)
    menu.add_checkbutton(label="항상 위에 표시", variable=topmost_var, command=toggle_topmost)
    menu.add_separator()
    menu.add_command(label="종료", command=quit_app)
    root.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))

    render()
    schedule_refresh()
    root.after(TICK_SECONDS * 1000, schedule_tick)
    root.mainloop()


if __name__ == "__main__":
    import sys

    if "--once" in sys.argv:  # 콘솔 확인용: 위젯 없이 한 번 조회해서 출력
        for name, fetch in (("Claude", fetch_claude), ("ChatGPT", fetch_chatgpt)):
            try:
                res = fetch()
                print(f"[{name}] ({res['source']})")
                for w in res["windows"]:
                    print(f"  {w['label']}: {100 - w['used']:.0f}% 남음 — {format_reset(w['resets_at'])}")
            except FetchError as e:
                print(f"[{name}] {e}")
    else:
        run_ui()
