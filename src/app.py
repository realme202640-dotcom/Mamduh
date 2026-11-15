from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("MIRSAL_DB_PATH", str(BASE_DIR.parent / "mirsal.db")))
SESSION_DAYS = 30

app = FastAPI(title="Mirsal Messenger", description="Phone-verified messaging demo")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            phone TEXT PRIMARY KEY,
            country_code TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            phone TEXT NOT NULL REFERENCES users(phone) ON DELETE CASCADE,
            expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_phone TEXT NOT NULL REFERENCES users(phone) ON DELETE CASCADE,
            contact_phone TEXT NOT NULL,
            contact_name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(owner_phone, contact_phone)
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
            sender_phone TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS streams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            host_phone TEXT NOT NULL REFERENCES users(phone) ON DELETE CASCADE,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'live',
            viewers INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_phone TEXT NOT NULL REFERENCES users(phone) ON DELETE CASCADE,
            contact_phone TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            ended_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_chats_owner ON chats(owner_phone);
        CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, id);
        """)


@app.on_event("startup")
def startup() -> None:
    init_db()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_phone(phone: str) -> str:
    value = phone.strip()
    if value.startswith("+"):
        digits = "".join(ch for ch in value[1:] if ch.isdigit())
    else:
        digits = "".join(ch for ch in value if ch.isdigit())
    if not 8 <= len(digits) <= 15:
        raise HTTPException(status_code=400, detail="أدخل رقم هاتف صحيحاً مع مفتاح الدولة")
    return "+" + digits


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class RegisterRequest(BaseModel):
    phone: str = Field(min_length=8, max_length=20)
    country_code: str = Field(default="+966", max_length=5)


class VerifyRequest(BaseModel):
    phone: str = Field(min_length=8, max_length=20)
    code: str = Field(min_length=4, max_length=10)


class MessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class NewChatRequest(BaseModel):
    phone: str = Field(min_length=8, max_length=20)
    name: Optional[str] = Field(default=None, max_length=80)


class StreamRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class CallRequest(BaseModel):
    contact: str = Field(min_length=8, max_length=20)


def current_user(authorization: Optional[str] = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="سجّل الدخول للمتابعة")
    token = authorization.removeprefix("Bearer ").strip()
    with connect() as db:
        row = db.execute(
            "SELECT phone, expires_at FROM sessions WHERE token_hash = ?",
            (token_hash(token),),
        ).fetchone()
    if not row or datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="انتهت الجلسة، سجّل الدخول مجدداً")
    return str(row["phone"])


def require_twilio() -> tuple[str, str, str]:
    sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth = os.getenv("TWILIO_AUTH_TOKEN")
    service = os.getenv("TWILIO_VERIFY_SERVICE_SID")
    if not all((sid, auth, service)):
        raise HTTPException(
            status_code=503,
            detail="تسجيل الهاتف الحقيقي غير مفعّل. اضبط TWILIO_ACCOUNT_SID وTWILIO_AUTH_TOKEN وTWILIO_VERIFY_SERVICE_SID.",
        )
    return sid or "", auth or "", service or ""


@app.get("/")
def root():
    return RedirectResponse(url="/static/index.html")


@app.get("/api/health")
def health():
    return {"status": "ok", "database": "sqlite", "phone_verification": "Twilio Verify"}


@app.post("/api/register")
def register_phone(data: RegisterRequest):
    phone = normalize_phone(data.phone)
    sid, auth, service = require_twilio()
    try:
        response = httpx.post(
            f"https://verify.twilio.com/v2/Services/{service}/Verifications",
            data={"To": phone, "Channel": "sms"},
            auth=(sid, auth),
            timeout=15,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = "تعذر إرسال رمز التحقق. تحقق من الرقم وإعدادات مزود الرسائل."
        raise HTTPException(status_code=502, detail=detail) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="تعذر الاتصال بخدمة إرسال رمز التحقق") from exc

    with connect() as db:
        db.execute(
            "INSERT INTO users(phone, country_code, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(phone) DO UPDATE SET country_code = excluded.country_code",
            (phone, data.country_code, now_iso()),
        )
    return {"message": "أرسلنا رمز التحقق عبر رسالة نصية", "phone": phone}


@app.post("/api/verify")
def verify_phone(data: VerifyRequest):
    phone = normalize_phone(data.phone)
    sid, auth, service = require_twilio()
    try:
        response = httpx.post(
            f"https://verify.twilio.com/v2/Services/{service}/VerificationChecks",
            data={"To": phone, "Code": data.code},
            auth=(sid, auth),
            timeout=15,
        )
        response.raise_for_status()
        result = response.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=400, detail="رمز التحقق غير صحيح أو منتهي الصلاحية") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="تعذر التحقق من الرمز حالياً") from exc

    if result.get("status") != "approved":
        raise HTTPException(status_code=400, detail="رمز التحقق غير صحيح أو منتهي الصلاحية")

    with connect() as db:
        db.execute(
            "INSERT INTO users(phone, country_code, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(phone) DO NOTHING",
            (phone, "+" + "".join(ch for ch in data.phone.strip().split()[0] if ch.isdigit())[:3], now_iso()),
        )
        token = secrets.token_urlsafe(32)
        expires = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat()
        db.execute("INSERT INTO sessions(token_hash, phone, expires_at) VALUES (?, ?, ?)",
                   (token_hash(token), phone, expires))
    return {"message": "تم التحقق بنجاح", "phone": phone, "access_token": token}


@app.get("/api/me")
def me(phone: str = Depends(current_user)):
    return {"phone": phone}


@app.post("/api/logout")
def logout(phone: str = Depends(current_user), authorization: Optional[str] = Header(default=None)):
    token = (authorization or "").removeprefix("Bearer ").strip()
    with connect() as db:
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(token),))
    return {"message": "تم تسجيل الخروج"}


@app.get("/api/chats")
def get_chats(phone: str = Depends(current_user)):
    with connect() as db:
        rows = db.execute("""
            SELECT c.id, c.contact_phone, c.contact_name,
                   (SELECT text FROM messages m WHERE m.chat_id = c.id ORDER BY m.id DESC LIMIT 1) AS last_message,
                   (SELECT created_at FROM messages m WHERE m.chat_id = c.id ORDER BY m.id DESC LIMIT 1) AS last_time
            FROM chats c WHERE c.owner_phone = ? ORDER BY COALESCE(last_time, c.created_at) DESC
        """, (phone,)).fetchall()
    return [{"id": row["id"], "name": row["contact_name"], "phone": row["contact_phone"],
             "lastMessage": row["last_message"] or "ابدأ المحادثة", "status": "متاح"} for row in rows]


@app.post("/api/chats")
def create_chat(data: NewChatRequest, phone: str = Depends(current_user)):
    contact_phone = normalize_phone(data.phone)
    if contact_phone == phone:
        raise HTTPException(status_code=400, detail="لا يمكنك بدء محادثة مع رقمك")
    name = (data.name or contact_phone).strip()
    with connect() as db:
        db.execute("INSERT OR IGNORE INTO chats(owner_phone, contact_phone, contact_name, created_at) VALUES (?, ?, ?, ?)",
                   (phone, contact_phone, name, now_iso()))
        row = db.execute("SELECT id, contact_phone, contact_name FROM chats WHERE owner_phone = ? AND contact_phone = ?",
                         (phone, contact_phone)).fetchone()
    return {"id": row["id"], "phone": row["contact_phone"], "name": row["contact_name"], "lastMessage": "ابدأ المحادثة"}


@app.get("/api/chats/{chat_id}/messages")
def get_chat_messages(chat_id: int, phone: str = Depends(current_user)):
    with connect() as db:
        chat = db.execute("SELECT id FROM chats WHERE id = ? AND owner_phone = ?", (chat_id, phone)).fetchone()
        if not chat:
            raise HTTPException(status_code=404, detail="المحادثة غير موجودة")
        rows = db.execute("SELECT id, sender_phone, text, created_at FROM messages WHERE chat_id = ? ORDER BY id", (chat_id,)).fetchall()
    return [{"id": row["id"], "sender": "Me" if row["sender_phone"] == phone else row["sender_phone"],
             "text": row["text"], "time": row["created_at"][11:16]} for row in rows]


@app.post("/api/chats/{chat_id}/messages")
def send_message(chat_id: int, data: MessageRequest, phone: str = Depends(current_user)):
    text = data.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="اكتب رسالة أولاً")
    with connect() as db:
        chat = db.execute("SELECT id FROM chats WHERE id = ? AND owner_phone = ?", (chat_id, phone)).fetchone()
        if not chat:
            raise HTTPException(status_code=404, detail="المحادثة غير موجودة")
        cursor = db.execute("INSERT INTO messages(chat_id, sender_phone, text, created_at) VALUES (?, ?, ?, ?)",
                            (chat_id, phone, text, now_iso()))
        message_id = cursor.lastrowid
    return {"id": message_id, "sender": "Me", "text": text, "time": datetime.now().strftime("%H:%M")}


@app.get("/api/live")
def get_streams(phone: str = Depends(current_user)):
    with connect() as db:
        rows = db.execute("SELECT id, host_phone, title, status, viewers FROM streams ORDER BY id DESC LIMIT 50").fetchall()
    return [{"id": r["id"], "host": r["host_phone"], "title": r["title"], "status": r["status"], "viewers": r["viewers"]} for r in rows]


@app.post("/api/live")
def create_stream(data: StreamRequest, phone: str = Depends(current_user)):
    with connect() as db:
        cursor = db.execute("INSERT INTO streams(host_phone, title, status, viewers, created_at) VALUES (?, ?, 'live', 0, ?)",
                            (phone, data.title.strip(), now_iso()))
    return {"id": cursor.lastrowid, "title": data.title.strip(), "host": phone, "viewers": 0, "status": "live"}


@app.post("/api/live/{stream_id}/toggle")
def toggle_stream(stream_id: int, phone: str = Depends(current_user)):
    with connect() as db:
        row = db.execute("SELECT status FROM streams WHERE id = ? AND host_phone = ?", (stream_id, phone)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="البث غير موجود")
        status = "offline" if row["status"] == "live" else "live"
        db.execute("UPDATE streams SET status = ? WHERE id = ?", (status, stream_id))
    return {"id": stream_id, "status": status}


@app.get("/api/calls")
def get_calls(phone: str = Depends(current_user)):
    with connect() as db:
        rows = db.execute("SELECT id, contact_phone, status, created_at, ended_at FROM calls WHERE owner_phone = ? ORDER BY id DESC LIMIT 50", (phone,)).fetchall()
    return [{"id": r["id"], "contact": r["contact_phone"], "status": r["status"],
             "duration": "جارية" if r["status"] == "active" else (r["ended_at"] or r["created_at"])[11:16]} for r in rows]


@app.post("/api/calls")
def create_call(data: CallRequest, phone: str = Depends(current_user)):
    contact = normalize_phone(data.contact)
    with connect() as db:
        cursor = db.execute("INSERT INTO calls(owner_phone, contact_phone, status, created_at) VALUES (?, ?, 'active', ?)",
                            (phone, contact, now_iso()))
    return {"id": cursor.lastrowid, "contact": contact, "status": "active"}


@app.post("/api/calls/{call_id}/end")
def end_call(call_id: int, phone: str = Depends(current_user)):
    with connect() as db:
        result = db.execute("UPDATE calls SET status = 'ended', ended_at = ? WHERE id = ? AND owner_phone = ? AND status = 'active'",
                            (now_iso(), call_id, phone))
        if not result.rowcount:
            raise HTTPException(status_code=404, detail="المكالمة غير موجودة أو منتهية")
    return {"id": call_id, "status": "ended"}
