import os
import hmac
import hashlib
import json
import httpx
import secrets
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from fastapi import (
    FastAPI, Request, HTTPException, Query, Response, Depends, Cookie, Body
)
from fastapi.responses import PlainTextResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

# ==================== CONFIG ====================
APP_SECRET = os.getenv("APP_SECRET", "")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
CLIENT_ID = os.getenv("CLIENT_ID", "")
CLIENT_SECRET = APP_SECRET
USER_DATA_FILE = os.getenv("USER_DATA_FILE", "users.json")
BUSINESS_PROFILE_FILE = os.getenv("BUSINESS_PROFILE_FILE", "business_profile.json")
GRAPH = "https://graph.facebook.com/v20.0"

# Frontend origin (for local dev, adjust as needed)
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "")
REDIRECT_URI = FRONTEND_ORIGIN + "/connect"


app = FastAPI(title="Instagram Marketing AI")
print(FRONTEND_ORIGIN)
if FRONTEND_ORIGIN:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[FRONTEND_ORIGIN],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# ==================== PERSISTENCE (very simple) ====================
def load_users() -> Dict[str, Any]:
    p = Path(USER_DATA_FILE)
    if not p.exists():
        return {}
    try:
        text = p.read_text().strip()
        if not text:
            return {}
        return json.loads(text)
    except Exception as e:
        # Log and reset to empty so /health doesn't 500
        print(f"⚠️ users.json unreadable ({p}): {e}; treating as empty.")
        return {}

def save_users(users: Dict[str, Any]) -> None:
    with open(USER_DATA_FILE, "w") as f:
        json.dump(users, f, indent=2)

def save_user(ig_user_id: str, data: Dict[str, Any]) -> None:
    users = load_users()
    users[ig_user_id] = data
    save_users(users)
    print(f"✅ Saved user {ig_user_id} to database")

def get_user(ig_user_id: str) -> Optional[Dict[str, Any]]:
    users = load_users()
    return users.get(ig_user_id)

# Lightweight server-side session for OAuth (HTTP-only cookie)
SESSIONS: Dict[str, Dict[str, Any]] = {}

def require_session(sid: Optional[str] = Cookie(default=None, alias="sid")) -> Dict[str, Any]:
    if not sid or sid not in SESSIONS:
        raise HTTPException(status_code=401, detail="No valid session")
    return SESSIONS[sid]

# ==================== UTILS ====================
def verify_signature(payload: bytes, signature_header: str) -> bool:
    if not signature_header:
        return False
    try:
        method, signature = signature_header.split("=")
        if method != "sha256":
            return False
    except ValueError:
        return False
    expected = hmac.new(APP_SECRET.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)

async def graph_get(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(f"{GRAPH}/{path.lstrip('/')}", params=params)
        data = r.json()
        if r.status_code >= 400 or "error" in data:
            raise HTTPException(status_code=r.status_code, detail=data.get("error", data))
        return data

async def graph_post(path: str, params: Dict[str, Any], json_body: Dict[str, Any] = None) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(f"{GRAPH}/{path.lstrip('/')}", params=params, json=json_body)
        data = r.json()
        if r.status_code >= 400 or "error" in data:
            raise HTTPException(status_code=r.status_code, detail=data.get("error", data))
        return data

def load_business_profile() -> Dict[str, Any]:
    if Path(BUSINESS_PROFILE_FILE).exists():
        with open(BUSINESS_PROFILE_FILE, "r") as f:
            return json.load(f)
    # default profile
    return {
        "business_name": "Your Business",
        "tone": "friendly",
        "products": [],
        "offers": [],
        "faq": {}
    }

def craft_short_reply(user_text: str) -> str:
    bp = load_business_profile()
    tone = bp.get("tone", "friendly")
    products = bp.get("products", [])
    offers = bp.get("offers", [])
    # ultra-simple 1–2 line heuristic
    base = "Thanks for reaching out!" if tone == "friendly" else "Thank you for your message."
    hint = ""
    if any(k in user_text.lower() for k in ["price", "cost"]):
        hint = " Our current offers: " + ", ".join(offers[:2]) if offers else ""
    elif products:
        hint = f" We recommend {products[0].get('name','our bestseller')}."
    reply = f"{base}{hint}"
    # keep it short
    return reply[:300]

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

# ==================== WEBHOOKS ====================
@app.get("/webhooks/meta")
async def verify_webhook(
    request: Request,
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
):
    print("🔍 Webhook verification request:", hub_mode, hub_verify_token)
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        return PlainTextResponse(hub_challenge)
    raise HTTPException(status_code=403, detail="Verification token mismatch")

@app.post("/webhooks/meta")
async def receive_webhook(request: Request):
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not verify_signature(body, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    print("📨 Webhook event:\n", json.dumps(payload, indent=2))

    # Instagram object covers IG messaging + comments (via linked Page)
    if payload.get("object") in ("instagram", "page"):
        for entry in payload.get("entry", []):
            # Messaging (DMs, story replies arrive as messaging events)
            for msg_event in entry.get("messaging", []):
                await handle_message_event(msg_event)

            # Changes (comments on IG media via Page subscriptions)
            for change in entry.get("changes", []):
                field = change.get("field")
                if field == "comments":
                    await handle_comment_change(change)

    return {"status": "ok"}

# ========== Incoming Messaging ==========
async def handle_message_event(event: Dict[str, Any]):
    """
    Handles Instagram DMs (including Story replies).
    event example keys: 'sender', 'recipient', 'timestamp', 'message'
    """
    sender_id = event.get("sender", {}).get("id")
    recipient_page_id = event.get("recipient", {}).get("id")
    message = event.get("message", {}) or {}
    is_echo = message.get("is_echo")
    text = message.get("text") or ""
    story = message.get("story")  # if present, this is a story reply

    if not sender_id or not recipient_page_id:
        return

    if is_echo:
        # Ignore echoes
        return

    print(f"💬 DM from {sender_id} -> Page {recipient_page_id}: {text or '[non-text]'}")

    # Find which IG Business account this Page maps to (via our stored users)
    users = load_users()
    target_user = None
    for u in users.values():
        if u.get("page_id") == recipient_page_id:
            target_user = u
            break

    if not target_user:
        print("⚠️ No stored user for this Page; cannot reply.")
        return

    page_access_token = target_user.get("page_access_token")
    if not page_access_token:
        print("⚠️ No page access token; cannot reply.")
        return

    # Craft a business-aware short reply
    if story:
        reply_text = craft_short_reply("story reply: " + (text or ""))
    else:
        reply_text = craft_short_reply(text or "")

    await send_instagram_dm(page_access_token, sender_id, reply_text)

# ========== Comments (Auto-one-time Private Reply) ==========
async def handle_comment_change(change: Dict[str, Any]):
    """
    Auto-DM one time when someone comments on your post/reel (Private Reply).
    """
    value = change.get("value", {}) or {}
    comment_id = value.get("id")
    text = value.get("text", "")
    from_user = value.get("from", {}) or {}
    commenter_igid = from_user.get("id")  # IG scoped user id
    page_id = value.get("page_id") or value.get("page")  # sometimes included
    print(f"💭 New IG comment {comment_id} from {commenter_igid}: {text}")

    if not commenter_igid:
        print("⚠️ No commenter IG user id; cannot private reply")
        return

    # Find stored page to get its token
    users = load_users()
    target_user = None
    for u in users.values():
        if u.get("page_id") == page_id or page_id is None:
            target_user = u
            break

    if not target_user:
        print("⚠️ No stored user for this Page; cannot private-reply.")
        return

    page_access_token = target_user.get("page_access_token")
    if not page_access_token:
        print("⚠️ No page access token; cannot private-reply.")
        return

    # One-time friendly private reply
    reply_text = craft_short_reply("comment: " + text)

    try:
        await send_instagram_dm(page_access_token, commenter_igid, reply_text, comment_id=comment_id)
        print("✅ Sent private reply DM (comment-based).")
    except HTTPException as e:
        print(f"⚠️ Private reply with comment_id failed: {e.detail}. Falling back to normal DM.")
        await send_instagram_dm(page_access_token, commenter_igid, reply_text)

# ========== Sending Messages ==========
async def send_instagram_dm(page_access_token: str, recipient_id: str, text: str, comment_id: Optional[str] = None):
    """
    Sends a DM via Instagram Messaging (through the Page).
    Endpoint: POST /me/messages with the Page access token.
    """
    payload: Dict[str, Any] = {
        "recipient": {"id": recipient_id},
        "message": {"text": text},
    }
    if comment_id:
        payload["comment_id"] = comment_id

    await graph_post(
        "me/messages",
        params={"access_token": page_access_token},
        json_body=payload,
    )

# ==================== ROOT (JSON, no templates) ====================
@app.get("/")
async def root():
    scopes = [
        "pages_show_list",
        "pages_read_engagement",
        "pages_manage_metadata",
        "pages_messaging",
        "instagram_basic",
        "instagram_manage_comments",
        "instagram_manage_messages",
        "instagram_content_publish",
    ]
    oauth_url = (
        f"https://www.facebook.com/v20.0/dialog/oauth?"
        f"client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}"
        f"&scope={','.join(scopes)}&response_type=code"
    )
    return {
        "ok": True,
        "message": "Use the Next.js /connect page for UI. This endpoint is just a helper.",
        "oauth_url_example": oauth_url,
        "endpoints": ["/oauth/exchange", "/facebook/pages", "/facebook/pages/{page_id}/subscribe", "/publish"],
    }

# ==================== NEXT.JS-FRIENDLY OAUTH FLOW ====================
@app.post("/oauth/exchange")
async def oauth_exchange(
    payload: Dict[str, str] = Body(...),
):
    """
    Exchange authorization code for a User Access Token (server-side),
    upgrade to long-lived, and store it inside a server session.
    Request JSON: { code, redirect_uri }
    """
    code = payload.get("code")
    redirect_uri = payload.get("redirect_uri") or REDIRECT_URI
    if not code:
        raise HTTPException(status_code=400, detail="Missing 'code'")

    # Step 1: short-lived token
    token_data = await graph_get(
        "oauth/access_token",
        {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "redirect_uri": redirect_uri,
            "code": code,
        },
    )
    user_access_token = token_data["access_token"]

    # Step 2: exchange for long-lived (best effort for POC)
    try:
        long_data = await graph_get(
            "oauth/access_token",
            {
                "grant_type": "fb_exchange_token",
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "fb_exchange_token": user_access_token,
            },
        )
        user_access_token = long_data.get("access_token", user_access_token)
    except HTTPException:
        pass

    # Create server-side session
    sid = secrets.token_urlsafe(24)
    SESSIONS[sid] = {
        "user_access_token": user_access_token,
        "created_at": utc_now_iso(),
    }

    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        key="sid",
        value=sid,
        httponly=True,
        secure=True,
        samesite="none",
        max_age=60 * 60 * 24 * 7,
        path="/",
    )
    return resp

@app.get("/facebook/pages")
async def list_pages(session=Depends(require_session)):
    """
    Lists Pages for the logged-in user (from session user token).
    Returns minimal fields plus whether IG is linked.
    """
    user_access_token = session["user_access_token"]
    data = await graph_get(
        "me/accounts",
        {"access_token": user_access_token, "fields": "id,name,category,instagram_business_account"},
    )
    print(data)
    pages = []
    for p in data.get("data", []):
        ig = p.get("instagram_business_account")
        pages.append(
            {
                "id": p["id"],
                "name": p.get("name"),
                "category": p.get("category"),
                "has_ig": bool(ig),
                "ig_id": (ig or {}).get("id") if ig else None,
            }
        )
    return {"ok": True, "pages": pages}

@app.post("/facebook/pages/{page_id}/subscribe")
async def subscribe_page(page_id: str, session=Depends(require_session)):
    """
    Subscribes the Page to webhook events and persists mapping if IG is linked.
    """
    user_access_token = session["user_access_token"]

    # Get Page access token
    page_edge = await graph_get(
        f"{page_id}",
        {"fields": "access_token,name,instagram_business_account", "access_token": user_access_token},
    )
    page_access_token = page_edge.get("access_token")
    page_name = page_edge.get("name")
    ig_edge = page_edge.get("instagram_business_account") or {}
    ig_user_id = ig_edge.get("id")

    if not page_access_token:
        raise HTTPException(status_code=400, detail="Cannot obtain Page access token")

    # Subscribe app to the Page
    try:
        await graph_post(
            f"{page_id}/subscribed_apps",
            params={
                "access_token": page_access_token,
                "subscribed_fields": "messages,message_echoes,message_reactions,messaging_postbacks,message_reads",
            },
        )
    except HTTPException as e:
        print("Subscription warning:", e.detail)

    # Persist mapping if IG is linked
    if ig_user_id:
        user_record = {
            "page_id": page_id,
            "page_name": page_name,
            "page_access_token": page_access_token,
            "ig_user_id": ig_user_id,
            "ig_username": None,
            "connected_at": utc_now_iso(),
        }
        try:
            ig_profile = await graph_get(
                f"{ig_user_id}", {"fields": "username", "access_token": page_access_token}
            )
            user_record["ig_username"] = ig_profile.get("username")
        except HTTPException:
            pass
        save_user(ig_user_id, user_record)

    return {"ok": True, "page_id": page_id, "ig_id": ig_user_id}

# ==================== PUBLISH ENDPOINT (Post/Reel) ====================
@app.post("/publish")
async def publish_media_api(
    payload: Dict[str, Any] = Body(...),
):
    """
    Basic IG publish flow (container -> publish).
    Request JSON:
      {
        "ig_user_id": "1784...",
        "media_type": "IMAGE" | "VIDEO",
        "media_url": "https://...",
        "caption": "Your caption",
        "cover_url": "https://...(for VIDEO optional)"
      }
    """
    ig_user_id = payload.get("ig_user_id")
    media_type = payload.get("media_type", "IMAGE").upper()
    media_url = payload.get("media_url")
    caption = payload.get("caption", "")
    cover_url = payload.get("cover_url")

    if not ig_user_id or not media_url:
        raise HTTPException(status_code=400, detail="ig_user_id and media_url are required")

    user = get_user(ig_user_id)
    if not user:
        raise HTTPException(status_code=404, detail="IG user not connected")
    page_access_token = user.get("page_access_token")
    if not page_access_token:
        raise HTTPException(status_code=400, detail="Missing page access token")

    params = {"access_token": page_access_token}
    body: Dict[str, Any] = {"caption": caption}

    if media_type == "IMAGE":
        body["image_url"] = media_url
    elif media_type == "VIDEO":
        body["media_type"] = "VIDEO"
        body["video_url"] = media_url
        if cover_url:
            body["cover_url"] = cover_url
    else:
        raise HTTPException(status_code=400, detail="Unsupported media_type")

    # 1) create media container
    container = await graph_post(f"{ig_user_id}/media", params=params, json_body=body)
    container_id = container.get("id")
    if not container_id:
        raise HTTPException(status_code=500, detail="Failed to create media container")

    # 2) publish
    publish = await graph_post(
        f"{ig_user_id}/media_publish",
        params=params,
        json_body={"creation_id": container_id},
    )
    return {"ok": True, "id": publish.get("id"), "container_id": container_id}

# ==================== DEBUG / ADMIN ====================
@app.get("/api/users")
async def list_users_api():
    users = load_users()
    return {"total_users": len(users), "users": users}

@app.get("/api/user/{ig_user_id}")
async def get_user_info_api(ig_user_id: str):
    user = get_user(ig_user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "instagram-marketing-ai",
        "connected_users": len(load_users()),
    }

@app.get("/info")
async def info():
    return {
        "server": "Instagram Marketing AI",
        "webhook_url": "/webhooks/meta",
        "onboarding_url": "/",
        "config": {
            "client_id_set": bool(CLIENT_ID),
            "app_secret_set": bool(APP_SECRET),
            "verify_token_set": bool(VERIFY_TOKEN),
            "redirect_uri": REDIRECT_URI,
        },
        "connected_users": len(load_users()),
    }


@app.get("/debug/token")
async def debug_token(session=Depends(require_session)):
    user_token = session["user_access_token"]
    app_access = f"{CLIENT_ID}|{CLIENT_SECRET}"
    data = await graph_get(
        "debug_token",
        {"input_token": user_token, "access_token": app_access},
    )
    return data  # look at data["data"]["scopes"], "is_valid"

@app.get("/debug/me_accounts")
async def debug_me_accounts(session=Depends(require_session)):
    user_token = session["user_access_token"]
    return await graph_get(
        "me/accounts",
        {"access_token": user_token, "fields": "id,name,perms,instagram_business_account"},
    )

@app.get("/debug/me")
async def debug_me(session=Depends(require_session)):
    return await graph_get("me", {"access_token": session["user_access_token"], "fields": "id,name"})


@app.get("/debug/page_access")
async def debug_page_access(session=Depends(require_session)):
    return await graph_get(
        "871581699375552",
        {"fields": "name,instagram_business_account,access_token",
         "access_token": session["user_access_token"]}
    )
