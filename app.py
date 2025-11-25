import os
import json
from datetime import datetime
from typing import List

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from binance.client import Client
from dotenv import load_dotenv

# ======================
# 환경변수 로드 (.env)
# ======================
load_dotenv()

BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")

if not BINANCE_API_KEY or not BINANCE_API_SECRET:
    print("⚠️  BINANCE_API_KEY / BINANCE_API_SECRET가 설정되지 않았습니다. .env를 확인하세요.")

# Binance 클라이언트 (읽기 전용 키 사용 권장)
client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)

# 댓글 저장 파일
COMMENTS_FILE = "comments.json"
BALANCE_HISTORY_FILE = "balance_history.json"
MAX_HISTORY_LEN = 500  # 로그를 너무 많이 쌓지 않기 위한 최대 개수


def load_balance_history() -> list[dict]:
    """
    balance_history.json 에 저장된 잔고 히스토리 불러오기
    형식: [{"timestamp": "...", "balance": 123.45}, ...]
    """
    if not os.path.exists(BALANCE_HISTORY_FILE):
        return []
    try:
        with open(BALANCE_HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return []


def save_balance_history(history: list[dict]):
    with open(BALANCE_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

# ======================
# FastAPI 기본 설정
# ======================
app = FastAPI(title="Please, pray my wallet!")

# 정적 파일 / 템플릿 설정
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ======================
# 댓글 관련 모델/함수
# ======================
class CommentIn(BaseModel):
    content: str  # 사용자가 입력하는 댓글 내용


class CommentOut(BaseModel):
    id: int
    nick: str
    content: str
    created_at: str  # ISO 문자열


def load_comments() -> List[dict]:
    if not os.path.exists(COMMENTS_FILE):
        return []
    try:
        with open(COMMENTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        # 파일이 깨졌을 경우 초기화
        return []


def save_comments(comments: List[dict]):
    with open(COMMENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(comments, f, ensure_ascii=False, indent=2)


# ======================
# 라우트: 메인 페이지
# ======================
@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    """
    메인 대시보드 페이지
    """
    return templates.TemplateResponse("index.html", {"request": request})


# ======================
# 라우트: Binance 계정 잔고
# ======================
@app.get("/api/balance")
async def get_balance():
    """
    Binance 선물 계정 USDT 잔고 조회
    - USDT (Futures)만 반환
    - free = availableBalance
    - locked = balance - availableBalance
    """
    try:
        futures_balances = client.futures_account_balance()
        usdt_info = None
        for b in futures_balances:
            if b.get("asset") == "USDT":
                usdt_info = b
                break

        if not usdt_info:
            return {"status": "ok", "balances": []}

        total = float(usdt_info.get("balance", 0))
        free = float(usdt_info.get("availableBalance", 0))
        locked = total - free

        result = [{
            "asset": "USDT (Futures)",
            "free": free,
            "locked": locked,
            "total": total
        }]

        return {"status": "ok", "balances": result}
    except Exception as e:
        print("선물 잔고 조회 에러:", e)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": "선물 잔고 조회 중 오류가 발생했습니다."}
        )

# ======================
# 라우트: 요약 정보 (간이 버전)
# ======================
@app.get("/api/summary")
async def get_summary():
    """
    요약 정보
    - Binance 선물 USDT 지갑 잔고
    - 호출 시마다 balance_history.json 에 로그 1건 추가
    """
    try:
        futures_balances = client.futures_account_balance()
        usdt_info = None
        for b in futures_balances:
            if b.get("asset") == "USDT":
                usdt_info = b
                break

        if not usdt_info:
            futures_usdt = 0.0
        else:
            futures_usdt = float(usdt_info.get("balance", 0))

        # ==== 여기서 서버 측 히스토리 기록 ====
        history = load_balance_history()
        now = datetime.utcnow().isoformat()
        history.append({
            "timestamp": now,
            "balance": futures_usdt
        })

        # 오래된 데이터는 잘라내기 (최근 MAX_HISTORY_LEN개만 유지)
        if len(history) > MAX_HISTORY_LEN:
            history = history[-MAX_HISTORY_LEN:]

        save_balance_history(history)
        # =====================================

        summary = {
            "coin_count": 1,
            "futures_usdt_balance": futures_usdt,
            "pnl_rate": None  # 나중에 수익률 계산 넣을 자리
        }

        return {"status": "ok", "summary": summary}
    except Exception as e:
        print("요약 정보 에러:", e)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": "요약 정보를 불러오는 중 오류가 발생했습니다."}
        )
# ======================
# 라우트: 댓글 목록 조회
# ======================
@app.get("/api/comments", response_model=List[CommentOut])
async def get_comments():
    """
    댓글 목록 조회
    - 저장된 순서대로 익명1, 익명2 ... 닉네임 부여
    """
    comments = load_comments()
    result: List[CommentOut] = []

    for idx, c in enumerate(comments, start=1):
        result.append(CommentOut(
            id=idx,
            nick=f"익명{idx}",
            content=c.get("content", ""),
            created_at=c.get("created_at", "")
        ))

    return result


# ======================
# 라우트: 댓글 작성
# ======================
@app.post("/api/comments", response_model=CommentOut)
async def post_comment(comment: CommentIn):
    """
    댓글 작성
    - content만 받음
    - created_at은 서버에서 UTC ISO로 기록
    """
    content = comment.content.strip()
    if not content:
        return JSONResponse(
            status_code=400,
            content={"detail": "내용이 비어있습니다."}
        )

    comments = load_comments()
    now = datetime.utcnow().isoformat()

    comments.append({
        "content": content,
        "created_at": now
    })
    save_comments(comments)

    new_id = len(comments)
    return CommentOut(
        id=new_id,
        nick=f"익명{new_id}",
        content=content,
        created_at=now
    )

@app.get("/api/balance-history")
async def get_balance_history():
    """
    선물 USDT 잔고 히스토리 조회
    - balance_history.json 에 저장된 최근 기록 반환
    """
    history = load_balance_history()
    # 프론트에서 바로 쓸 수 있게 반환
    return {
        "status": "ok",
        "history": history
    }
# ======================
# 헬스 체크용 (선택)
# ======================
@app.get("/health")
async def health_check():
    return {"status": "ok"}
