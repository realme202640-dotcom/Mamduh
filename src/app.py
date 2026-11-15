from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import (Column, ForeignKey, Integer, String, Text, UniqueConstraint,
                        create_engine, or_, select, text)
from sqlalchemy.orm import declarative_base, sessionmaker

BASE_DIR = Path(__file__).resolve().parent
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR.parent / 'mirsal.db'}")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://") and "+" not in DATABASE_URL.split(":")[0]:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
Base = declarative_base()
SESSION_DAYS = 30


class User(Base):
    __tablename__ = "users"
    phone = Column(String(20), primary_key=True)
    country_code = Column(String(8), nullable=False, default="+966")
    created_at = Column(String(40), nullable=False)
    display_name = Column(String(80), nullable=False, default="")
    about = Column(String(240), nullable=False, default="")
    avatar_url = Column(Text, nullable=False, default="")


class LoginSession(Base):
    __tablename__ = "sessions"
    token_hash = Column(String(64), primary_key=True)
    phone = Column(String(20), ForeignKey("users.phone", ondelete="CASCADE"), nullable=False)
    expires_at = Column(String(40), nullable=False)


class Chat(Base):
    __tablename__ = "chats"
    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_phone = Column(String(20), ForeignKey("users.phone", ondelete="CASCADE"), nullable=False)
    contact_phone = Column(String(20), nullable=False)
    contact_name = Column(String(80), nullable=False)
    created_at = Column(String(40), nullable=False)
    __table_args__ = (UniqueConstraint("owner_phone", "contact_phone", name="uq_chat_owner_contact"),)


class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(Integer, ForeignKey("chats.id", ondelete="CASCADE"), nullable=False)
    sender_phone = Column(String(20), nullable=False)
    text = Column(Text, nullable=False)
    created_at = Column(String(40), nullable=False)


class Stream(Base):
    __tablename__ = "streams"
    id = Column(Integer, primary_key=True, autoincrement=True)
    host_phone = Column(String(20), ForeignKey("users.phone", ondelete="CASCADE"), nullable=False)
    title = Column(String(120), nullable=False)
    status = Column(String(20), nullable=False, default="live")
    viewers = Column(Integer, nullable=False, default=0)
    created_at = Column(String(40), nullable=False)


class Call(Base):
    __tablename__ = "calls"
    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_phone = Column(String(20), ForeignKey("users.phone", ondelete="CASCADE"), nullable=False)
    contact_phone = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False)
    created_at = Column(String(40), nullable=False)
    ended_at = Column(String(40), nullable=True)


app = FastAPI(title="Mirsal Messenger", description="Phone-verified messaging app with WebRTC signaling")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    Base.metadata.create_all(engine)
    # Safe, additive profile migration for existing SQLite installations.
    if engine.dialect.name == "sqlite":
        with engine.begin() as conn:
            existing = {row["name"] for row in conn.execute(text("PRAGMA table_info(users)")).mappings()}
            for name, declaration in (("display_name", "VARCHAR(80) NOT NULL DEFAULT ''"),
                                      ("about", "VARCHAR(240) NOT NULL DEFAULT ''"),
                                      ("avatar_url", "TEXT NOT NULL DEFAULT ''")):
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE users ADD COLUMN {name} {declaration}"))


@app.on_event("startup")
def startup() -> None:
    init_db()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def normalize_phone(phone: str) -> str:
    value = phone.strip()
    digits = "".join(ch for ch in (value[1:] if value.startswith("+") else value) if ch.isdigit())
    if not 8 <= len(digits) <= 15:
        raise HTTPException(status_code=400, detail="أدخل رقم هاتف صحيحاً مع مفتاح الدولة")
    return "+" + digits


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class RegisterRequest(BaseModel):
    phone: str = Field(min_length=8, max_length=20)
    country_code: str = Field(default="+966", max_length=8)

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

class ProfileRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    about: str = Field(default="", max_length=240)
    avatar_url: str = Field(default="", max_length=1000)


def user_from_token(token: str, db) -> Optional[User]:
    row = db.execute(select(LoginSession).where(LoginSession.token_hash == token_hash(token))).scalar_one_or_none()
    if not row or datetime.fromisoformat(row.expires_at) <= datetime.now(timezone.utc):
        return None
    return db.get(User, row.phone)


def current_user(authorization: Optional[str] = Header(default=None), db=Depends(get_db)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="سجّل الدخول للمتابعة")
    user = user_from_token(authorization.removeprefix("Bearer ").strip(), db)
    if not user:
        raise HTTPException(status_code=401, detail="انتهت الجلسة، سجّل الدخول مجدداً")
    return user.phone


def require_twilio() -> tuple[str, str, str]:
    sid, auth, service = (os.getenv(k) for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_VERIFY_SERVICE_SID"))
    if not all((sid, auth, service)):
        raise HTTPException(status_code=503, detail="التحقق غير مفعّل؛ اضبط إعدادات Twilio Verify على الخادم.")
    return sid or "", auth or "", service or ""


@app.get("/")
def root():
    return RedirectResponse(url="/static/index.html")

@app.get("/api/health")
def health():
    return {"status": "ok", "database": engine.dialect.name, "signaling": "websocket"}

@app.post("/api/register")
def register_phone(data: RegisterRequest, db=Depends(get_db)):
    phone = normalize_phone(data.phone)
    sid, auth, service = require_twilio()
    try:
        response = httpx.post(f"https://verify.twilio.com/v2/Services/{service}/Verifications",
                              data={"To": phone, "Channel": "sms"}, auth=(sid, auth), timeout=15)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="تعذر إرسال رمز التحقق عبر SMS") from exc
    user = db.get(User, phone)
    if user:
        user.country_code = data.country_code
    else:
        db.add(User(phone=phone, country_code=data.country_code, created_at=now_iso()))
    db.commit()
    return {"message": "أرسلنا رمز التحقق عبر رسالة نصية", "phone": phone}

@app.post("/api/verify")
def verify_phone(data: VerifyRequest, db=Depends(get_db)):
    phone = normalize_phone(data.phone)
    sid, auth, service = require_twilio()
    try:
        response = httpx.post(f"https://verify.twilio.com/v2/Services/{service}/VerificationChecks",
                              data={"To": phone, "Code": data.code}, auth=(sid, auth), timeout=15)
        response.raise_for_status()
        if response.json().get("status") != "approved":
            raise HTTPException(status_code=400, detail="رمز التحقق غير صحيح أو منتهي الصلاحية")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=400, detail="رمز التحقق غير صحيح أو منتهي الصلاحية") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="تعذر التحقق من الرمز حالياً") from exc
    user = db.get(User, phone)
    if not user:
        user = User(phone=phone, country_code="+966", created_at=now_iso())
        db.add(user)
    token = secrets.token_urlsafe(32)
    db.add(LoginSession(token_hash=token_hash(token), phone=phone,
                        expires_at=(datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat()))
    db.commit()
    return {"message": "تم التحقق بنجاح", "phone": phone, "access_token": token}

@app.get("/api/me")
def me(phone: str = Depends(current_user), db=Depends(get_db)):
    user = db.get(User, phone)
    return {"phone": phone, "display_name": user.display_name or "", "about": user.about or "", "avatar_url": user.avatar_url or ""}

@app.put("/api/profile")
def update_profile(data: ProfileRequest, phone: str = Depends(current_user), db=Depends(get_db)):
    user = db.get(User, phone)
    user.display_name, user.about, user.avatar_url = data.display_name.strip(), data.about.strip(), data.avatar_url.strip()
    db.commit()
    return {"phone": phone, "display_name": user.display_name, "about": user.about, "avatar_url": user.avatar_url}

@app.post("/api/logout")
def logout(authorization: Optional[str] = Header(default=None), db=Depends(get_db)):
    if authorization and authorization.startswith("Bearer "):
        row = db.get(LoginSession, token_hash(authorization.removeprefix("Bearer ").strip()))
        if row:
            db.delete(row); db.commit()
    return {"message": "تم تسجيل الخروج"}

@app.get("/api/chats")
def get_chats(phone: str = Depends(current_user), db=Depends(get_db)):
    chats = db.execute(select(Chat).where(Chat.owner_phone == phone).order_by(Chat.id.desc())).scalars().all()
    result = []
    for chat in chats:
        last = db.execute(select(Message).where(Message.chat_id == chat.id).order_by(Message.id.desc()).limit(1)).scalar_one_or_none()
        result.append({"id": chat.id, "name": chat.contact_name, "phone": chat.contact_phone,
                       "lastMessage": last.text if last else "ابدأ المحادثة", "status": "متاح"})
    return result

@app.post("/api/chats")
def create_chat(data: NewChatRequest, phone: str = Depends(current_user), db=Depends(get_db)):
    contact = normalize_phone(data.phone)
    if contact == phone:
        raise HTTPException(status_code=400, detail="لا يمكنك بدء محادثة مع رقمك")
    chat = db.execute(select(Chat).where(Chat.owner_phone == phone, Chat.contact_phone == contact)).scalar_one_or_none()
    if not chat:
        chat = Chat(owner_phone=phone, contact_phone=contact, contact_name=(data.name or contact).strip(), created_at=now_iso())
        db.add(chat); db.commit(); db.refresh(chat)
    return {"id": chat.id, "phone": contact, "name": chat.contact_name, "lastMessage": "ابدأ المحادثة"}

@app.get("/api/chats/{chat_id}/messages")
def get_messages(chat_id: int, phone: str = Depends(current_user), db=Depends(get_db)):
    chat = db.execute(select(Chat).where(Chat.id == chat_id, Chat.owner_phone == phone)).scalar_one_or_none()
    if not chat: raise HTTPException(status_code=404, detail="المحادثة غير موجودة")
    messages = db.execute(select(Message).where(Message.chat_id == chat_id).order_by(Message.id)).scalars().all()
    return [{"id": m.id, "sender": "Me" if m.sender_phone == phone else m.sender_phone, "text": m.text,
             "time": datetime.fromisoformat(m.created_at).strftime("%H:%M")} for m in messages]

@app.post("/api/chats/{chat_id}/messages")
def send_message(chat_id: int, data: MessageRequest, phone: str = Depends(current_user), db=Depends(get_db)):
    chat = db.execute(select(Chat).where(Chat.id == chat_id, Chat.owner_phone == phone)).scalar_one_or_none()
    if not chat: raise HTTPException(status_code=404, detail="المحادثة غير موجودة")
    message = Message(chat_id=chat_id, sender_phone=phone, text=data.text.strip(), created_at=now_iso())
    db.add(message); db.commit(); db.refresh(message)
    return {"id": message.id, "sender": "Me", "text": message.text, "time": datetime.now().strftime("%H:%M")}

@app.get("/api/live")
def get_streams(phone: str = Depends(current_user), db=Depends(get_db)):
    rows = db.execute(select(Stream).order_by(Stream.id.desc()).limit(50)).scalars().all()
    return [{"id": r.id, "host": r.host_phone, "title": r.title, "status": r.status, "viewers": r.viewers} for r in rows]

@app.post("/api/live")
def create_stream(data: StreamRequest, phone: str = Depends(current_user), db=Depends(get_db)):
    stream = Stream(host_phone=phone, title=data.title.strip(), status="live", viewers=0, created_at=now_iso())
    db.add(stream); db.commit(); db.refresh(stream)
    return {"id": stream.id, "title": stream.title, "host": phone, "viewers": 0, "status": "live"}

@app.get("/api/rtc-config")
def rtc_config(phone: str = Depends(current_user)):
    ice = [{"urls": ["stun:stun.l.google.com:19302"]}]
    urls = os.getenv("TURN_URLS", "").split(",")
    if urls and urls[0].strip():
        turn = {"urls": [url.strip() for url in urls if url.strip()], "username": os.getenv("TURN_USERNAME", ""), "credential": os.getenv("TURN_CREDENTIAL", "")}
        ice.append(turn)
    return {"iceServers": ice}

@app.get("/api/calls")
def get_calls(phone: str = Depends(current_user), db=Depends(get_db)):
    rows = db.execute(select(Call).where(or_(Call.owner_phone == phone, Call.contact_phone == phone)).order_by(Call.id.desc()).limit(50)).scalars().all()
    return [{"id": c.id, "contact": c.contact_phone if c.owner_phone == phone else c.owner_phone,
             "direction": "outgoing" if c.owner_phone == phone else "incoming", "status": c.status,
             "duration": "جارية" if c.status in ("ringing", "active") else "منتهية"} for c in rows]

@app.post("/api/calls")
def create_call(data: CallRequest, phone: str = Depends(current_user), db=Depends(get_db)):
    contact = normalize_phone(data.contact)
    if contact == phone: raise HTTPException(status_code=400, detail="لا يمكنك الاتصال برقمك")
    call = Call(owner_phone=phone, contact_phone=contact, status="ringing", created_at=now_iso())
    db.add(call); db.commit(); db.refresh(call)
    return {"id": call.id, "contact": contact, "status": call.status}

@app.post("/api/calls/{call_id}/answer")
def answer_call(call_id: int, phone: str = Depends(current_user), db=Depends(get_db)):
    call = db.get(Call, call_id)
    if not call or phone not in (call.owner_phone, call.contact_phone): raise HTTPException(status_code=404, detail="المكالمة غير موجودة")
    if phone == call.contact_phone and call.status == "ringing": call.status = "active"; db.commit()
    return {"id": call.id, "status": call.status}

@app.post("/api/calls/{call_id}/end")
def end_call(call_id: int, phone: str = Depends(current_user), db=Depends(get_db)):
    call = db.get(Call, call_id)
    if not call or phone not in (call.owner_phone, call.contact_phone): raise HTTPException(status_code=404, detail="المكالمة غير موجودة")
    call.status, call.ended_at = "ended", now_iso(); db.commit()
    return {"id": call.id, "status": call.status}


class SignalRooms:
    def __init__(self): self.rooms: dict[int, dict[str, WebSocket]] = {}
    async def join(self, call_id: int, phone: str, ws: WebSocket):
        await ws.accept(); peers = self.rooms.setdefault(call_id, {}); peers[phone] = ws
        for other_phone, other in list(peers.items()):
            if other_phone != phone:
                await other.send_json({"type": "peer_joined", "phone": phone})
        await ws.send_json({"type": "ready", "phone": phone})
    def leave(self, call_id: int, phone: str):
        peers = self.rooms.get(call_id, {}); peers.pop(phone, None)
        if not peers: self.rooms.pop(call_id, None)
    async def relay(self, call_id: int, sender: str, payload: dict):
        for peer, ws in list(self.rooms.get(call_id, {}).items()):
            if peer != sender:
                await ws.send_json({**payload, "from": sender})

signal_rooms = SignalRooms()

@app.websocket("/ws/calls/{call_id}")
async def call_signaling(websocket: WebSocket, call_id: int, token: str = ""):
    db = SessionLocal()
    user = user_from_token(token, db) if token else None
    call = db.get(Call, call_id) if user else None
    if not user or not call or user.phone not in (call.owner_phone, call.contact_phone):
        db.close(); await websocket.close(code=1008); return
    phone = user.phone; db.close()
    await signal_rooms.join(call_id, phone, websocket)
    try:
        while True:
            payload = await websocket.receive_json()
            if payload.get("type") not in {"offer", "answer", "ice", "hangup"}: continue
            await signal_rooms.relay(call_id, phone, payload)
    except WebSocketDisconnect:
        pass
    finally:
        signal_rooms.leave(call_id, phone)
        try: await signal_rooms.relay(call_id, phone, {"type": "peer_left"})
        except Exception: pass
