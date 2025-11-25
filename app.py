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

# ======================
# 데이터 디렉토리 설정
# (Railway에서는 DATA_DIR=/data 로 환경변수 세팅, 로컬은 현재 폴더 사용)
# ======================
DATA_DIR = os.getenv("DATA_DIR", ".")
os.makedirs(DATA_DIR, exist_ok=True)

COMMENTS_FILE = os.path.join(DATA_DIR, "comments.json")
BALANCE_HISTORY_FILE = os.path.join(DATA_DIR, "balance_history.json")
MAX_HISTORY_LEN = 500  # 히스토리 최대 개수


# ======================
# Binance 클라이언트 헬퍼
# (import 시점이 아니라, 요청 시점에 생성)
# ======================
def get_binance_client() -> Client:
    if not BINANCE_API_KEY or not BINANCE_API_SECRET:
        # 키가 없으면 서버는 살려두고, API에서 에러 반환
        raise RuntimeError("Binance API 키가 설정되어 있지 않습니다.")
    return Client(BINANCE_API_KEY, BINANCE_API_SECRET)


# ======================
# 댓글/히스토리 파일 유틸
# ======================
def load_comments() -> List[dict]:
    if not os.path.exists(COMMENTS_FILE):
        return []
    try:
        with open(COMMENTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return []


def save_comments(comments: List[dict]):
    with open(COMMENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(comments, f, ensure_ascii=False, indent=2)


def load_balance_history() -> List[dict]:
    if not os.path.exists(BALANCE_HISTORY_FILE):
        return []
    try:
        with open(BALANCE_HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return []


def save_balance_history(history: List[dict]):
    with open(BALANCE_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


# ======================
# Pydantic 모델
# ======================
class CommentIn(BaseModel):
    content: str


class CommentOut(BaseModel):
    id: int
    nick: str
    content: str
    created_at: str


# ======================
# FastAPI 설정
# ======================
app = FastAPI(title="Please, pray my wallet!")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ======================
# 메인 페이지
# ======================
@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


# ======================
# Binance 선물 USDT 잔고
# ======================
@app.get("/api/balance")
async def get_balance():
    """
    Binance 선물 계정의 USDT 잔고 조회
    """
    try:
        client = get_binance_client()
    except RuntimeError as e:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(e)},
        )

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
# 요약 정보 + 히스토리 기록
# ======================
@app.get("/api/summary")
async def get_summary():
    """
    - 선물 USDT 잔고 요약
    - 호출될 때마다 balance_history.json에 포인트 추가
    """
    try:
        client = get_binance_client()
    except RuntimeError as e:
        # 그래도 서버는 살아있고, 프론트에서 에러 메시지 표시 가능
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(e)},
        )

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

        # 히스토리 기록
        history = load_balance_history()
        now = datetime.utcnow().isoformat()
        history.append({
            "timestamp": now,
            "balance": futures_usdt
        })
        if len(history) > MAX_HISTORY_LEN:
            history = history[-MAX_HISTORY_LEN:]
        save_balance_history(history)

        summary = {
            "coin_count": 1,
            "futures_usdt_balance": futures_usdt,
            "pnl_rate": None
        }

        return {"status": "ok", "summary": summary}
    except Exception as e:
        print("요약 정보 에러:", e)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": "요약 정보를 불러오는 중 오류가 발생했습니다."}
        )


# ======================
# 히스토리 조회
# ======================
@app.get("/api/balance-history")
async def get_balance_history():
    history = load_balance_history()
    return {"status": "ok", "history": history}


# ======================
# 댓글 API
# ======================
@app.get("/api/comments", response_model=List[CommentOut])
async def get_comments():
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


@app.post("/api/comments", response_model=CommentOut)
async def post_comment(comment: CommentIn):
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


# ======================
# 헬스 체크
# ======================
@app.get("/health")
async def health_check():
    return {"status": "ok"}
