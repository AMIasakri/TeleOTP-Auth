import hashlib
import secrets
import httpx
import asyncio
import logging
import sys
from typing import Optional
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from jose import jwt

from core_db import SessionLocal
from models import User, OTPSession,RefreshToken
from config import settings

# =========================
# تنظیمات Logging برای ویندوز (رفع مشکل Emoji)
# =========================
class SafeStreamHandler(logging.StreamHandler):
    """Handler سفارشی برای مدیریت ایمن Emoji در ویندوز"""
    def emit(self, record):
        try:
            # حذف ایموجی‌ها برای ویندوز
            if sys.platform == "win32":
                if hasattr(record, 'msg'):
                    # جایگزینی ایموجی‌ها با متن ساده
                    record.msg = record.msg.replace('🚀', '[START]')
                    record.msg = record.msg.replace('🛑', '[STOP]')
                    record.msg = record.msg.replace('✅', '[OK]')
                    record.msg = record.msg.replace('❌', '[ERROR]')
                    record.msg = record.msg.replace('🔐', '[LOCK]')
                    record.msg = record.msg.replace('📤', '[SEND]')
                    record.msg = record.msg.replace('⚠️', '[WARN]')
                    record.msg = record.msg.replace('🔒', '[SECURE]')
                    record.msg = record.msg.replace('🧪', '[TEST]')
            super().emit(record)
        except Exception:
            # اگر باز هم خطا داشت، بدون ایموجی لاگ کن
            try:
                super().emit(record)
            except:
                pass

# تنظیم لاگینگ
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("telegram_otp.log", encoding='utf-8'),
        SafeStreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# =========================
# HTTP Client Singleton برای Telegram (بدون HTTP/2)
# =========================
class TelegramHTTPClient:
    """Singleton Async HTTP Client با Connection Pool مدیریت شده"""
    _instance: Optional[httpx.AsyncClient] = None
    
    @classmethod
    async def get_client(cls) -> httpx.AsyncClient:
        if cls._instance is None or cls._instance.is_closed:
            # حذف http2=True برای رفع خطای نصب h2
            cls._instance = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=5.0,    # زمان اتصال
                    read=10.0,      # زمان دریافت پاسخ
                    write=5.0,      # زمان ارسال
                    pool=2.0        # زمان انتظار در pool
                ),
                limits=httpx.Limits(
                    max_keepalive_connections=20,
                    max_connections=100,
                    keepalive_expiry=30.0
                ),
                follow_redirects=True
                # http2=True را حذف کردیم
            )
            logger.info("[OK] Telegram HTTP Client initialized")
        return cls._instance
    
    @classmethod
    async def close_client(cls):
        if cls._instance and not cls._instance.is_closed:
            await cls._instance.aclose()
            cls._instance = None
            logger.info("[LOCK] Telegram HTTP Client closed")

# =========================
# مدیریت چرخه حیات برنامه
# =========================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """مدیریت Startup و Shutdown"""
    # Startup
    logger.info("[START] FastAPI application starting...")
    try:
        await TelegramHTTPClient.get_client()  # Pre-warm the client
        logger.info("[OK] Telegram client initialized successfully")
    except Exception as e:
        logger.error(f"[ERROR] Failed to initialize Telegram client: {e}")
    yield
    # Shutdown
    logger.info("[STOP] FastAPI application shutting down...")
    await TelegramHTTPClient.close_client()

app = FastAPI(lifespan=lifespan, title="OTP Telegram Auth")

# =========================
# مدل‌های درخواست
# =========================
class LoginRequest(BaseModel):
    email: str
    password: str

class VerifyRequest(BaseModel):
    email: str
    login_session: str
    code: str

# =========================
# توابع کمکی JWT
# =========================
def create_access_token(user_id: int) -> str:
    payload = {
        "sub": str(user_id),
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=15)
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")

def create_refresh_token() -> str:
    return secrets.token_hex(32)

# =========================
# تابع ارسال تلگرام (نسخه صحیح Async بدون Emoji)
# =========================
async def send_telegram_async(chat_id: str, text: str) -> dict:
    """
    ارسال پیام به Telegram با مدیریت کامل خطا
    Returns: dict با وضعیت و جزئیات
    """
    if not chat_id or not isinstance(chat_id, str):
        error_msg = f"Invalid chat_id: {chat_id}"
        logger.error(f"[ERROR] {error_msg}")
        return {"success": False, "error": error_msg}
    
    if not text:
        error_msg = "Empty message text"
        logger.error(f"[ERROR] {error_msg}")
        return {"success": False, "error": error_msg}
    
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
    
    # لاگ دقیق برای دیباگ
    logger.info(f"[SEND] Sending Telegram message to chat_id: {chat_id}")
    logger.debug(f"Message preview: {text[:50]}...")
    
    try:
        # گرفتن کلاینت اشتراکی
        client = await TelegramHTTPClient.get_client()
        
        # ارسال درخواست Async واقعی
        response = await client.post(
            url,
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML" if "<" in text else None,  # فقط اگه HTML داره
                "disable_web_page_preview": True
            }
        )
        
        # لاگ وضعیت
        logger.info(f"Telegram response status: {response.status_code}")
        
        # بررسی پاسخ
        if response.status_code == 200:
            response_data = response.json()
            
            if response_data.get("ok"):
                logger.info(f"[OK] Telegram message sent successfully to {chat_id}")
                return {
                    "success": True,
                    "message_id": response_data.get("result", {}).get("message_id"),
                    "chat_id": chat_id
                }
            else:
                error_desc = response_data.get("description", "Unknown error")
                logger.error(f"[ERROR] Telegram API error: {error_desc}")
                return {"success": False, "error": error_desc}
        else:
            logger.error(f"[ERROR] Telegram HTTP error: {response.status_code} - {response.text}")
            return {"success": False, "error": f"HTTP {response.status_code}"}
            
    except httpx.TimeoutException as e:
        logger.error(f"[ERROR] Telegram timeout for chat_id {chat_id}: {e}")
        return {"success": False, "error": "Timeout"}
        
    except httpx.ConnectError as e:
        logger.error(f"[ERROR] Cannot connect to Telegram API: {e}")
        return {"success": False, "error": "Connection failed"}
        
    except httpx.HTTPStatusError as e:
        logger.error(f"[ERROR] Telegram HTTP status error: {e.response.status_code}")
        return {"success": False, "error": f"HTTP {e.response.status_code}"}
        
    except Exception as e:
        logger.error(f"[ERROR] Unexpected Telegram error: {type(e).__name__}: {e}", exc_info=True)
        return {"success": False, "error": str(e)}

# =========================
# تابع ارسال با Retry Mechanism
# =========================
async def send_telegram_with_retry(
    chat_id: str, 
    text: str, 
    max_retries: int = 3,
    retry_delay: float = 1.0
) -> dict:
    """
    ارسال پیام به Telegram با قابلیت تلاش مجدد
    """
    last_error = None
    
    for attempt in range(max_retries):
        logger.info(f"Telegram send attempt {attempt + 1}/{max_retries} for chat_id {chat_id}")
        
        result = await send_telegram_async(chat_id, text)
        
        if result["success"]:
            if attempt > 0:
                logger.info(f"[OK] Telegram sent successfully on attempt {attempt + 1}")
            return result
        
        last_error = result.get("error", "Unknown error")
        
        if attempt < max_retries - 1:
            wait_time = retry_delay * (2 ** attempt)  # 1s, 2s, 4s
            logger.warning(f"[WARN] Retry {attempt + 1}/{max_retries} failed. Waiting {wait_time}s...")
            await asyncio.sleep(wait_time)
    
    logger.error(f"[ERROR] All {max_retries} attempts failed for chat_id {chat_id}. Last error: {last_error}")
    return {"success": False, "error": last_error}

# =========================
# ENDPOINT لاگین (با BackgroundTasks)
# =========================
@app.post("/login")
async def login(data: LoginRequest, background_tasks: BackgroundTasks):
    """
    لاگین کاربر و ارسال OTP از طریق تلگرام
    استفاده از BackgroundTasks برای عدم بلوکه شدن پاسخ
    """
    db = SessionLocal()
    
    try:
        logger.info(f"[LOCK] Login attempt for email: {data.email}")
        
        # بررسی وجود کاربر
        user = db.query(User).filter(User.email == data.email).first()
        
        if not user:
            logger.warning(f"User not found: {data.email}")
            raise HTTPException(status_code=404, detail="User not found")
        
        # بررسی رمز عبور (توصیه: از هش استفاده کنید)
        if user.password_hash != data.password:
            logger.warning(f"Wrong password for user: {data.email}")
            raise HTTPException(status_code=401, detail="Wrong password")
        
        # تولید OTP
        login_session = secrets.token_hex(16)
        otp_code = str(secrets.randbelow(900000) + 100000)
        
        logger.info(f"OTP generated for {data.email}: {otp_code}")
        
        # هش کردن OTP برای ذخیره در دیتابیس
        otp_hash = hashlib.sha256(
            f"{otp_code}{settings.SECRET_KEY}".encode()
        ).hexdigest()
        
        # ذخیره در دیتابیس
        otp = OTPSession(
            user_id=user.id,
            login_session=login_session,
            otp_hash=otp_hash,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=2),
            attempts=0,
            is_used=False
        )
        
        db.add(otp)
        db.commit()
        
        logger.info(f"OTP stored in DB for user {user.id}, session: {login_session[:16]}...")
        
        # بررسی chat_id
        if not user.telegram_chat_id:
            logger.error(f"User {user.email} has no telegram_chat_id configured")
            raise HTTPException(
                status_code=400, 
                detail="Telegram not configured for this user. Please set up Telegram first."
            )
        
        # ارسال در Background برای عدم Block شدن
        background_tasks.add_task(
            send_telegram_with_retry,
            user.telegram_chat_id,
            f"[LOCK] Your OTP Code: {otp_code}\n\nValid for 2 minutes\nDo not share this code"
        )
        
        logger.info(f"Telegram task added to background for user {user.email}")
        
        # بازگشت پاسخ موفق (منتظر تلگرام نمی‌ماند)
        return {
            "status": "sent",
            "login_session": login_session,
            "message": "OTP code has been sent to your Telegram"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"[ERROR] Login error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        db.close()

# =========================
# ENDPOINT تأیید OTP
# =========================
@app.post("/verify-otp")
async def verify(data: VerifyRequest):
    """تأیید OTP و تولید توکن"""
    db = SessionLocal()
    
    try:
        logger.info(f"OTP verification attempt for email: {data.email}")
        
        user = db.query(User).filter(User.email == data.email).first()
        
        if not user:
            logger.warning(f"User not found during verification: {data.email}")
            raise HTTPException(status_code=404, detail="User not found")
        
        # پیدا کردن OTP session
        otp = db.query(OTPSession).filter(
            OTPSession.user_id == user.id,
            OTPSession.login_session == data.login_session,
            OTPSession.is_used == False
        ).first()
        
        if not otp:
            logger.warning(f"OTP not found for user {user.id}, session: {data.login_session[:16]}...")
            raise HTTPException(status_code=400, detail="OTP session not found")
        
        # بررسی انقضا
        if otp.expires_at < datetime.now(timezone.utc):
            logger.warning(f"Expired OTP for user {user.id}")
            raise HTTPException(status_code=400, detail="OTP expired")
        
        # بررسی تعداد تلاش‌ها
        if otp.attempts >= 3:
            logger.warning(f"Too many failed attempts for user {user.id}")
            raise HTTPException(status_code=401, detail="Too many failed attempts")
        
        # بررسی صحت OTP
        candidate = hashlib.sha256(
            f"{data.code}{settings.SECRET_KEY}".encode()
        ).hexdigest()
        
        if otp.otp_hash != candidate:
            otp.attempts += 1
            db.commit()
            logger.warning(f"Wrong OTP for user {user.id}, attempt {otp.attempts}/3")
            raise HTTPException(status_code=401, detail="Wrong OTP")
        
        # موفقیت
        otp.is_used = True
        db.commit()
        
        logger.info(f"[OK] OTP verified successfully for user {user.id}")
        
        
        otp.is_used=True
        db.commit()
        access_token = create_access_token(user.id)
        refresh_token_value = create_refresh_token()
        refresh_token = RefreshToken(
             user_id=user.id,
              token=refresh_token_value,
               expires_at=datetime.now(timezone.utc) + timedelta(days=7),
               revoked=False
)
        db.add(refresh_token)
        db.commit()
        logger.info(f"[OK] OTP verified successfully for user {user.id}")
        return {
            "status": "verified",
               "access_token": access_token,
               "refresh_token": refresh_token_value
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"[ERROR] Verification error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        db.close()

# =========================
# ENDPOINT سلامت سیستم
# =========================
@app.get("/health")
async def health_check():
    """بررسی سلامت سیستم و اتصال به تلگرام"""
    try:
        client = await TelegramHTTPClient.get_client()
        # تست ساده اتصال
        await client.get("https://api.telegram.org")
        
        return {
            "status": "healthy",
            "telegram_client": "connected",
            "bot_token_configured": bool(settings.TELEGRAM_BOT_TOKEN)
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {
            "status": "unhealthy",
            "error": str(e)
        }