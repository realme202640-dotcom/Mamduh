from __future__ import annotations

from typing import Dict, List

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(
    title="Mirsal Messenger",
    description="WhatsApp-like messaging app with phone registration, live broadcasts, and calls",
)

app.mount("/static", StaticFiles(directory="src/static"), name="static")


class RegisterRequest(BaseModel):
    phone: str
    country_code: str = "+966"


class VerifyRequest(BaseModel):
    phone: str
    code: str


class MessageRequest(BaseModel):
    sender: str
    text: str


class StreamRequest(BaseModel):
    title: str
    host: str = "Mirsal Live"


class CallRequest(BaseModel):
    contact: str
    status: str = "incoming"


def normalize_phone(phone: str) -> str:
    cleaned = "".join(ch for ch in phone if ch.isdigit())
    if not cleaned:
        raise HTTPException(status_code=400, detail="Phone number is required")
    return cleaned


def ensure_user(phone: str, country_code: str = "+966") -> Dict[str, str | bool]:
    user = users.get(phone)
    if user is None:
        user = {
            "phone": phone,
            "country_code": country_code,
            "verified": False,
            "otp": "123456",
        }
        users[phone] = user
    return user


users: Dict[str, dict] = {}

conversations: List[dict] = [
    {
        "id": 1,
        "name": "Ahmed",
        "status": "online",
        "lastMessage": "سأرسل لك الملف الآن",
        "messages": [
            {"id": 1, "sender": "Ahmed", "text": "مرحباً، هل أنت متاح اليوم؟", "time": "09:12"},
            {"id": 2, "sender": "Me", "text": "نعم، يمكنني المشاركة قبل الظهر.", "time": "09:14"},
            {"id": 3, "sender": "Ahmed", "text": "سأرسل لك الملف الآن", "time": "09:15"},
        ],
    },
    {
        "id": 2,
        "name": "Nada",
        "status": "typing",
        "lastMessage": "ممتاز، سنبدأ البث بعد 10 دقائق",
        "messages": [
            {"id": 1, "sender": "Nada", "text": "أهلاً، هل تم تجهيز العرض؟", "time": "08:40"},
            {"id": 2, "sender": "Me", "text": "نعم، كل شيء جاهز.", "time": "08:42"},
            {"id": 3, "sender": "Nada", "text": "ممتاز، سنبدأ البث بعد 10 دقائق", "time": "08:44"},
        ],
    },
    {
        "id": 3,
        "name": "Team Support",
        "status": "online",
        "lastMessage": "تم تحديث التطبيق بنجاح",
        "messages": [
            {"id": 1, "sender": "Team Support", "text": "تم تحديث التطبيق بنجاح", "time": "Yesterday"},
        ],
    },
]

live_streams: List[dict] = [
    {"id": 1, "title": "جلسة توضيح المنتج", "host": "Mirsal Live", "viewers": 1842, "status": "live"},
    {"id": 2, "title": "بث مباشر للمسؤولين", "host": "Operations", "viewers": 327, "status": "scheduled"},
    {"id": 3, "title": "مؤتمر القيادة", "host": "Leadership", "viewers": 952, "status": "live"},
]

calls: List[dict] = [
    {"id": 1, "contact": "Ahmed", "status": "incoming", "duration": "00:02:18"},
    {"id": 2, "contact": "Nada", "status": "outgoing", "duration": "00:04:57"},
    {"id": 3, "contact": "Support", "status": "missed", "duration": "00:00:00"},
]


@app.get("/")
def root():
    return RedirectResponse(url="/static/index.html")


@app.get("/api/health")
def health():
    return {"status": "ok", "app": "Mirsal Messenger"}


@app.post("/api/register")
def register_phone(data: RegisterRequest):
    phone = normalize_phone(data.phone)
    user = ensure_user(phone, data.country_code)
    user["otp"] = "123456"
    user["verified"] = False
    return {"message": "Verification code sent successfully", "phone": phone, "otp": "123456"}


@app.post("/api/verify")
def verify_phone(data: VerifyRequest):
    phone = normalize_phone(data.phone)
    user = users.get(phone)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found. Please register first.")
    if user.get("otp") != data.code:
        raise HTTPException(status_code=400, detail="Invalid verification code")

    user["verified"] = True
    return {
        "message": "Phone number verified successfully",
        "phone": phone,
        "country_code": user.get("country_code", "+966"),
        "verified": True,
    }


@app.get("/api/chats")
def get_chats():
    return conversations


@app.get("/api/chats/{chat_id}/messages")
def get_chat_messages(chat_id: int):
    chat = next((item for item in conversations if item["id"] == chat_id), None)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat["messages"]


@app.post("/api/chats/{chat_id}/messages")
def send_chat_message(chat_id: int, data: MessageRequest):
    chat = next((item for item in conversations if item["id"] == chat_id), None)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")

    message = {
        "id": len(chat["messages"]) + 1,
        "sender": data.sender,
        "text": data.text,
        "time": "now",
    }
    chat["messages"].append(message)
    chat["lastMessage"] = data.text
    return {"message": "Message sent", "data": message}


@app.get("/api/live")
def get_live_streams():
    return live_streams


@app.post("/api/live")
def create_live_stream(data: StreamRequest):
    new_stream = {
        "id": len(live_streams) + 1,
        "title": data.title,
        "host": data.host,
        "viewers": 0,
        "status": "live",
    }
    live_streams.insert(0, new_stream)
    return {"message": "Live stream started", "data": new_stream}


@app.post("/api/live/{stream_id}/toggle")
def toggle_live_stream(stream_id: int):
    stream = next((item for item in live_streams if item["id"] == stream_id), None)
    if stream is None:
        raise HTTPException(status_code=404, detail="Live stream not found")

    stream["status"] = "live" if stream["status"] != "live" else "offline"
    if stream["status"] == "live":
        stream["viewers"] = max(stream["viewers"], 100)
    return {"message": "Stream status updated", "data": stream}


@app.get("/api/calls")
def get_calls():
    return calls


@app.post("/api/calls")
def create_call(data: CallRequest):
    call = {
        "id": len(calls) + 1,
        "contact": data.contact,
        "status": data.status,
        "duration": "00:00:00",
    }
    calls.insert(0, call)
    return {"message": "Call created", "data": call}


@app.post("/api/calls/{call_id}/end")
def end_call(call_id: int):
    call = next((item for item in calls if item["id"] == call_id), None)
    if call is None:
        raise HTTPException(status_code=404, detail="Call not found")
    call["status"] = "ended"
    call["duration"] = "00:03:42"
    return {"message": "Call ended", "data": call}
