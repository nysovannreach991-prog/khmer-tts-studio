#!/usr/bin/env python3
"""
Production-Ready Gemini Web API Call Router & Gateway
FastAPI + Async Playwright OpenAI-Compatible API Gateway with Multi-Account Round-Robin,
Local Chrome Profile Auto-Discovery & One-Click Import, and Embedded Tailwind CSS Web Dashboard.
"""

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import uvicorn
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, model_validator
from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

# ---------------------------------------------------------------------------
# Logging Configuration
# ---------------------------------------------------------------------------
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("gemini-router")

# ---------------------------------------------------------------------------
# Constants & Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent.resolve()
LOCAL_PLAYWRIGHT_BROWSERS = BASE_DIR / "ms-playwright"
if LOCAL_PLAYWRIGHT_BROWSERS.exists() and "PLAYWRIGHT_BROWSERS_PATH" not in os.environ:
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(LOCAL_PLAYWRIGHT_BROWSERS)

PROFILES_DIR = (BASE_DIR / "profiles").resolve()
GEMINI_URL = "https://gemini.google.com/app"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/133.0.0.0 Safari/537.36"
)

CHROMIUM_ARGS = [
    "--no-first-run",
    "--no-default-browser-check",
    "--profile-directory=Default",
    "--window-size=1280,850",
    "--lang=en-US",
    "--disable-renderer-backgrounding",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
]

def get_system_chrome_user_data_dir() -> Optional[Path]:
    """Returns the default Google Chrome User Data path on Windows."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        p = Path(local_app_data) / "Google" / "Chrome" / "User Data"
        if p.exists():
            return p
    return None

def get_chrome_executable_path() -> str:
    """Finds Google Chrome executable from Windows Registry or standard program files locations."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe") as key:
            val, _ = winreg.QueryValueEx(key, "")
            if val and os.path.exists(val):
                return str(val)
    except Exception:
        pass

    candidates = [
        os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), r"Google\Chrome\Application\chrome.exe"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"), r"Google\Chrome\Application\chrome.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe"),
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return str(c)

    found = shutil.which("chrome") or shutil.which("chrome.exe") or shutil.which("google-chrome")
    return str(found) if found else r"C:\Program Files\Google\Chrome\Application\chrome.exe"

# ---------------------------------------------------------------------------
# Supported Gemini Model Registry
# ---------------------------------------------------------------------------
SUPPORTED_MODELS = [
    {
        "id": "gemini-3.5-flash-lite",
        "name": "Gemini 3.5 Flash Lite",
        "description": "Ultra-fast lightweight responses with minimal latency",
        "tier": "Flash Lite",
        "thinking": "None",
    },
    {
        "id": "gemini-3.5-flash-lite-low",
        "name": "Gemini 3.5 Flash Lite (Low)",
        "description": "Gemini 3.5 Flash Lite with minimal thinking overhead for fastest execution",
        "tier": "Flash Lite Low",
        "thinking": "Low",
    },
    {
        "id": "gemini-3.1-pro",
        "name": "Gemini 3.1 Pro",
        "description": "Professional-grade reasoning and complex analysis",
        "tier": "Pro",
        "thinking": "None",
    },
    {
        "id": "gemini-3.1-pro-low",
        "name": "Gemini 3.1 Pro (Low)",
        "description": "Gemini 3.1 Pro with low thinking overhead for fast professional-grade answers",
        "tier": "Pro Low",
        "thinking": "Low",
    },
    {
        "id": "gemini-3.8-flash",
        "name": "Gemini 3.8 Flash",
        "description": "Served by Gemini 4 Hydrogen (Gemini Web no longer offers 3.8 Flash)",
        "tier": "Flash",
        "thinking": "None",
    },
    {
        "id": "gemini-3.8-flash-low",
        "name": "Gemini 3.8 Flash (Low)",
        "description": "Served by Gemini 4 Hydrogen with Low thinking (Gemini Web no longer offers 3.8 Flash)",
        "tier": "Flash Low",
        "thinking": "Low",
    },
    {
        "id": "gemini-4-argon",
        "name": "Gemini 4 Argon",
        "description": "Flagship next-gen architecture with state-of-the-art capability",
        "tier": "Flagship",
        "thinking": "Maximum",
    },
    {
        "id": "gemini-4-hydrogen",
        "name": "Gemini 4 Hydrogen",
        "description": "Gemini 4 Hydrogen (selected by the 'Hydrogen' entry in the Gemini Web model picker)",
        "tier": "Gemini 4",
        "thinking": "None",
    },
    {
        "id": "gemini-web",
        "name": "Gemini Web (Auto)",
        "description": "Adaptive automatic Gemini model selection",
        "tier": "Auto",
        "thinking": "Adaptive",
    },
]

# Optional prompt prefixes by model ID (clean and non-intrusive to prevent triggering Gemini Web internal directive errors)
MODEL_PROMPT_PREFIXES = {
    "gemini-3.5-flash-lite": "",
    "gemini-3.5-flash-lite-low": "",
    "gemini-3.5-lite": "",
    "gemini-3.5-lite-low": "",
    "gemini-3.1-pro": "",
    "gemini-3.1-pro-low": "",
    "gemini-3.1-low": "",
    "gemini-3.8-flash": "",
    "gemini-3.8-flash-low": "",
    "gemini-3.8-low": "",
    "gemini-4-argon": "",
    "gemini-4-hydrogen": "",
}


# ---------------------------------------------------------------------------
# OpenAI API Request & Response Schemas
# ---------------------------------------------------------------------------
class ChatMessage(BaseModel):
    role: str
    content: Union[str, List[Any]]

class ChatCompletionRequest(BaseModel):
    # None / empty -> filled with the configured default model (see _apply_default_model below)
    model: Optional[str] = None
    messages: List[ChatMessage]
    stream: Optional[bool] = False
    temperature: Optional[float] = 0.7
    top_p: Optional[float] = 1.0
    max_tokens: Optional[int] = None
    audio_path: Optional[str] = None
    account_id: Optional[str] = None
    auto_delete: Optional[bool] = Field(None, description="If true, deletes conversation from Gemini Web after completion")
    timeout: Optional[int] = Field(None, description="Generation timeout in seconds")

    @model_validator(mode="after")
    def _apply_default_model(self):
        """Requests that do not name a model use the router's default model (run.bat menu / settings.json)."""
        if not (self.model or "").strip():
            self.model = getattr(account_manager, "preferred_fast_model", None) or "gemini-3.5-flash-lite"
        return self

class AddAccountPayload(BaseModel):
    account_id: str = Field(..., description="Unique alphanumeric identifier for the account")

class ImportChromePayload(BaseModel):
    chrome_profile_dir: str = Field(..., description="Chrome profile folder name (e.g. 'Default', 'Profile 5')")
    account_id: Optional[str] = Field(None, description="Optional target account ID. If omitted, derived from email or folder.")

class TestChatPayload(BaseModel):
    account_id: Optional[str] = None
    prompt: Optional[str] = "Hello Gemini! Please reply with a brief greeting."
    model: Optional[str] = "gemini-3.5-flash-lite"
    provider: Optional[str] = Field("auto", description="Execution target: 'auto', 'antigravity', or 'web'")
    auto_delete: Optional[bool] = Field(None, description="If true, deletes conversation from Gemini Web after completion")
    timeout: Optional[int] = Field(None, description="Generation timeout in seconds")

class TestTranscribePayload(BaseModel):
    account_id: Optional[str] = None
    audio_path: str = Field(..., description="Path to the audio file (.mp3, .wav, .m4a, etc.)")
    prompt: Optional[str] = None
    model: Optional[str] = "gemini-3.8-flash"
    auto_delete: Optional[bool] = Field(None, description="If true, deletes conversation from Gemini Web after completion")
    timeout: Optional[int] = Field(None, description="Generation timeout in seconds")

class TimeoutSettingsPayload(BaseModel):
    generation_timeout: Optional[int] = Field(None, description="Timeout for text generation in seconds (default: 180)")
    audio_timeout: Optional[int] = Field(None, description="Timeout for audio/file processing in seconds (default: 360)")
    page_timeout: Optional[int] = Field(None, description="Timeout for page navigation in seconds (default: 45)")

class SpeedOptimizationPayload(BaseModel):
    block_heavy_media: Optional[bool] = Field(None, description="Block images, web fonts, video and audio streams in headless tabs for 2x faster load")
    turbo_dom_polling: Optional[bool] = Field(None, description="High frequency DOM response polling (0.12s instead of 0.25s) for instant response pickup")
    preferred_fast_model: Optional[str] = Field(None, description="Preferred default fast model (e.g. gemini-3.5-flash-lite)")

class UpdatePlanPayload(BaseModel):
    plan: str = Field(..., description="Gemini Plan tier: 'Free', 'Pro', or 'Ultra'")

class AutoDeletePayload(BaseModel):
    auto_delete: bool = Field(..., description="Enable or disable auto-deletion of chat history after response")

# ---------------------------------------------------------------------------
# Request Telemetry & Audit Logs
# ---------------------------------------------------------------------------
class RequestLogEntry:
    def __init__(
        self,
        request_id: str,
        endpoint: str,
        account_id: str,
        model: str,
        prompt: str,
        status: str = "in_flight",
        status_code: int = 200,
        latency_seconds: float = 0.0,
        response_preview: str = "",
        error_message: str = "",
        timestamp: Optional[str] = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        provider: str = "web"
    ):
        self.request_id = request_id
        self.endpoint = endpoint
        self.account_id = account_id
        self.model = model
        self.prompt = prompt
        self.status = status
        self.status_code = status_code
        self.latency_seconds = latency_seconds
        self.response_preview = response_preview
        self.error_message = error_message
        self.prompt_tokens = prompt_tokens or (max(1, len(prompt) // 4) if prompt else 0)
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens or (self.prompt_tokens + self.completion_tokens)
        self.created_at_ts = time.time()
        self.timestamp = timestamp or datetime.now().strftime("%H:%M:%S")
        self.provider = provider or "web"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "endpoint": self.endpoint,
            "account_id": self.account_id,
            "provider": getattr(self, "provider", "web"),
            "model": self.model,
            "prompt": self.prompt[:300] + ("..." if len(self.prompt) > 300 else ""),
            "prompt_full": self.prompt,
            "status": self.status,
            "status_code": self.status_code,
            "latency_seconds": self.latency_seconds,
            "response_preview": self.response_preview[:300] + ("..." if len(self.response_preview) > 300 else ""),
            "response_full": self.response_preview,
            "error_message": self.error_message,
            "prompt_tokens": getattr(self, "prompt_tokens", 0),
            "completion_tokens": getattr(self, "completion_tokens", 0),
            "total_tokens": getattr(self, "total_tokens", 0),
            "created_at_ts": getattr(self, "created_at_ts", time.time()),
            "timestamp": self.timestamp,
        }

class RequestLogTracker:
    def __init__(self, max_entries: int = 500):
        self.max_entries = max_entries
        self.entries: deque = deque(maxlen=max_entries)
        self._lock = asyncio.Lock()
        self._load_from_db()

    def _load_from_db(self):
        """Preloads recent persistent request logs from SQLite database on startup."""
        try:
            from telemetry_db import telemetry_db
            db_logs = telemetry_db.get_logs(limit=self.max_entries)
            # Rehydrate in reverse order so latest is at the left of the deque
            for log_data in reversed(db_logs):
                entry = RequestLogEntry(
                    request_id=log_data.get("request_id", ""),
                    endpoint=log_data.get("endpoint", ""),
                    account_id=log_data.get("account_id", ""),
                    model=log_data.get("model", ""),
                    prompt=log_data.get("prompt") or log_data.get("prompt_preview") or "",
                    status=log_data.get("status", "success"),
                    status_code=log_data.get("status_code", 200),
                    latency_seconds=log_data.get("latency_seconds", 0.0),
                    response_preview=log_data.get("response_full") or log_data.get("response_preview") or "",
                    error_message=log_data.get("error_message", ""),
                    timestamp=log_data.get("timestamp"),
                    prompt_tokens=log_data.get("prompt_tokens", 0),
                    completion_tokens=log_data.get("completion_tokens", 0),
                    total_tokens=log_data.get("total_tokens", 0),
                    provider=log_data.get("provider", "web")
                )
                entry.created_at_ts = log_data.get("created_at_ts", time.time())
                self.entries.appendleft(entry)
        except Exception as e:
            logger.warning(f"Could not preload logs from SQLite DB: {e}")

    async def log_start(self, request_id: str, endpoint: str, account_id: str, model: str, prompt: str, provider: str = "web") -> RequestLogEntry:
        entry = RequestLogEntry(
            request_id=request_id,
            endpoint=endpoint,
            account_id=account_id,
            model=model,
            prompt=prompt,
            status="in_flight",
            provider=provider
        )
        async with self._lock:
            self.entries.appendleft(entry)

        # Persist start of request to SQLite
        try:
            from telemetry_db import telemetry_db
            telemetry_db.log_start(
                request_id=request_id,
                endpoint=endpoint,
                account_id=account_id,
                model=model,
                prompt=prompt,
                created_at_ts=entry.created_at_ts,
                provider=provider
            )
        except Exception as e:
            logger.warning(f"[TelemetryDB] log_start failed: {e}")

        return entry

    async def log_finish(
        self,
        entry: RequestLogEntry,
        status: str,
        status_code: int,
        latency_seconds: float,
        response_preview: str = "",
        error_message: str = "",
        response_full: str = ""
    ):
        p_text = entry.prompt or ""
        r_text = response_full or response_preview or ""
        p_tokens = getattr(entry, "prompt_tokens", 0) or (max(1, len(p_text) // 4) if p_text else 0)
        c_tokens = max(1, len(r_text) // 4) if r_text else 0

        async with self._lock:
            entry.status = status
            entry.status_code = status_code
            entry.latency_seconds = round(latency_seconds, 2)
            entry.response_preview = response_preview
            entry.error_message = error_message
            entry.prompt_tokens = p_tokens
            entry.completion_tokens = c_tokens
            entry.total_tokens = p_tokens + c_tokens

        # Persist finish to SQLite
        try:
            from telemetry_db import telemetry_db
            telemetry_db.log_finish(
                request_id=entry.request_id,
                status=status,
                status_code=status_code,
                latency_seconds=latency_seconds,
                response_preview=response_preview,
                response_full=response_full or response_preview,
                error_message=error_message,
                prompt=entry.prompt
            )
        except Exception as e:
            logger.warning(f"[TelemetryDB] log_finish failed: {e}")

    async def get_logs(self, limit: int = 100) -> List[Dict[str, Any]]:
        # If in-memory deque has entries, use them; also falls back to SQLite
        async with self._lock:
            if len(self.entries) > 0:
                return [e.to_dict() for e in list(self.entries)[:limit]]
        try:
            from telemetry_db import telemetry_db
            return telemetry_db.get_logs(limit=limit)
        except Exception:
            return []

    async def clear_logs(self):
        async with self._lock:
            self.entries.clear()
        try:
            from telemetry_db import telemetry_db
            telemetry_db.clear_logs()
        except Exception as e:
            logger.warning(f"[TelemetryDB] clear_logs failed: {e}")

    async def cancel_inflight_for_account(self, account_id: Optional[str] = None):
        """Marks in-flight request log entries as cancelled with status code 499."""
        async with self._lock:
            for entry in self.entries:
                if entry.status == "in_flight":
                    if account_id is None or entry.account_id == account_id:
                        entry.status = "cancelled"
                        entry.status_code = 499
                        entry.error_message = "Request stopped / cancelled by user"
        try:
            from telemetry_db import telemetry_db
            telemetry_db.cancel_inflight_for_account(account_id)
        except Exception as e:
            logger.warning(f"[TelemetryDB] cancel_inflight failed: {e}")

request_logger = RequestLogTracker(max_entries=500)

# ---------------------------------------------------------------------------
# Account State & Browser Management
# ---------------------------------------------------------------------------
class AccountSession:
    def __init__(self, account_id: str, profile_path: Path):
        self.account_id = account_id
        self.profile_path = profile_path
        self.context: Optional[BrowserContext] = None
        self.lock = asyncio.Lock()
        self.is_busy = False
        self.is_paused: bool = False
        self.cancelled: bool = False
        self.active_task: Optional[asyncio.Task] = None
        self.status = "initializing"  # initializing, ready, needs_login, logging_in, manual_control, error, paused
        self.manual_chrome_proc: Optional[subprocess.Popen] = None
        self.last_used_at: float = 0
        self.total_requests: int = 0
        self.last_error: Optional[str] = None
        self.source: str = "manual"  # manual or chrome_import
        self.plan: str = "Free"  # Free, Pro, Ultra
        self.auto_delete_history: bool = False  # Auto-clean chats from Gemini account
        self.current_usage_pct: Optional[int] = None
        self.current_usage_reset: Optional[str] = None
        self.weekly_usage_pct: Optional[int] = None
        self.weekly_usage_reset: Optional[str] = None
        self.usage_updated_at: Optional[float] = None
        self.is_calibrated: bool = False
        self.active_model_id: Optional[str] = None
        self.active_thinking_level: Optional[str] = None
        self.recent_latencies: deque = deque(maxlen=10)
        self.avg_latency: float = 0.0
        self.quota_exhausted: bool = False

        # Feature 1: Pre-Warmed Tab Pool
        self.warm_page: Optional[Page] = None
        self.warm_lock: asyncio.Lock = asyncio.Lock()
        self._is_warming: bool = False

        # Feature 2: Account Health Circuit Breaker & Auto-Cooldown
        self.cooldown_until: float = 0.0
        self.cooldown_reason: Optional[str] = None
        self.consecutive_failures: int = 0

        # Feature 3: Official Chrome Direct Automation via CDP (Fast 6s)
        self.is_cdp: bool = False
        self.cdp_url: str = ""
        self.cdp_browser = None
        self.email: str = ""
        self.color: str = "#4F46E5"

        self.load_meta()

    def is_cooling_down(self) -> bool:
        """Returns True if this account is currently placed in temporary cooldown by the Circuit Breaker."""
        return time.time() < getattr(self, "cooldown_until", 0.0)

    def get_cooldown_remaining(self) -> int:
        """Returns remaining cooldown time in seconds."""
        rem = getattr(self, "cooldown_until", 0.0) - time.time()
        return max(0, int(rem))

    def trigger_cooldown(self, duration_seconds: int = 600, reason: str = "Rate limited by Google Gemini"):
        """Activates Circuit Breaker cooldown for this account, temporarily routing requests away."""
        self.cooldown_until = time.time() + duration_seconds
        self.cooldown_reason = reason
        logger.warning(f"[{self.account_id}] [CircuitBreaker] Activated cooldown for {duration_seconds}s. Reason: {reason}")

    def reset_cooldown(self):
        """Clears Circuit Breaker cooldown and restores account to active rotation."""
        self.cooldown_until = 0.0
        self.cooldown_reason = None
        self.consecutive_failures = 0
        logger.info(f"[{self.account_id}] [CircuitBreaker] Cooldown cleared.")

    async def prepare_warm_page(self):
        """Pre-warms a clean Gemini tab in the background to eliminate navigation latency on the next request."""
        if not getattr(account_manager, "pre_warmed_pool_enabled", True):
            return
        if self.status not in ("ready", "initializing") or getattr(self, "is_paused", False) or not self.context:
            return
        if self.warm_page is not None:
            try:
                if not self.warm_page.is_closed():
                    return
            except Exception:
                self.warm_page = None

        if self._is_warming:
            return
        self._is_warming = True

        async with self.warm_lock:
            try:
                if not getattr(account_manager, "pre_warmed_pool_enabled", True):
                    return
                if self.warm_page is not None and not self.warm_page.is_closed():
                    return
                if not self.context:
                    return

                logger.info(f"[{self.account_id}] Pre-warming a Gemini page in background pool...")
                page = await self.context.new_page()
                try:
                    page.set_default_timeout(180000)
                    page.set_default_navigation_timeout(180000)
                except Exception:
                    pass
                await page.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    });
                """)
                nav_timeout = getattr(account_manager, "page_timeout", 45) * 1000
                await page.goto(GEMINI_URL, wait_until="domcontentloaded", timeout=nav_timeout)

                # Check if redirected to Google sign-in
                if "accounts.google.com" in page.url:
                    self.status = "needs_login"
                    await page.close()
                    return

                try:
                    await dismiss_gemini_overlays(page, self.account_id)
                except Exception:
                    pass

                # Select the default model on the standby tab now, so requests for that model
                # skip the 3-4s model-picker step entirely.
                try:
                    warm_model = getattr(account_manager, "preferred_fast_model", None)
                    if warm_model and warm_model != "gemini-web":
                        # Wait for the real model picker first. Running the switch on a half-rendered page
                        # makes its text fallback grab an unrelated button (e.g. a "...math problem"
                        # suggestion chip matches 'Pro') and click it.
                        picker = page.locator("button[data-test-id='bard-mode-menu-button'], bard-mode-switcher button, button[aria-label*='mode picker' i]").first
                        await picker.wait_for(state="visible", timeout=10000)
                        await switch_gemini_model_ui(page, warm_model, self.account_id)
                except Exception as sw_err:
                    logger.debug(f"[{self.account_id}] Warm tab model pre-select note: {sw_err}")

                self.warm_page = page
                logger.info(f"[{self.account_id}] Pre-warmed Gemini tab ready in pool.")
            except Exception as e:
                logger.debug(f"[{self.account_id}] prepare_warm_page note: {e}")
                if 'page' in locals() and page:
                    try:
                        await page.close()
                    except Exception:
                        pass
                self.warm_page = None
            finally:
                self._is_warming = False

    async def get_or_create_page(self, rewarm: bool = True) -> Page:
        """Retrieves the pre-warmed tab from the pool, or creates and navigates to a new tab on demand."""
        # Feature 3: Official Chrome Direct Automation via CDP (Fast 6s)
        if getattr(self, "is_cdp", False):
            try:
                # Auto-heal: If CDP port is unreachable, attempt auto-launching Official Chrome
                if not await account_manager.check_official_chrome_cdp(port=9222):
                    logger.warning(f"[{self.account_id}] CDP not reachable on 9222. Auto-launching Official Chrome...")
                    launch_res = await account_manager.launch_official_chrome_cdp(port=9222)
                    if not launch_res.get("success"):
                        logger.error(f"[{self.account_id}] Auto-launch failed: {launch_res.get('error')}")

                if not self.context or not getattr(self, "cdp_browser", None) or not self.cdp_browser.is_connected():
                    logger.info(f"[{self.account_id}] Connecting over CDP to {self.cdp_url}...")
                    self.cdp_browser = await account_manager.playwright.chromium.connect_over_cdp(self.cdp_url)
                    self.context = self.cdp_browser.contexts[0]

                # Reuse active Gemini tab in official Chrome
                for p in self.context.pages:
                    if "gemini.google.com" in p.url and not p.is_closed():
                        try:
                            await p.bring_to_front()
                        except Exception:
                            pass
                        logger.info(f"[{self.account_id}] Attached to active Gemini tab in Official Chrome!")
                        self.status = "ready"
                        return p

                # If no Gemini tab is open, use first page or open new tab
                p = self.context.pages[0] if self.context.pages and not self.context.pages[0].is_closed() else await self.context.new_page()
                if "gemini.google.com" not in p.url:
                    await p.goto(GEMINI_URL, wait_until="domcontentloaded", timeout=45000)
                try:
                    await p.bring_to_front()
                except Exception:
                    pass
                self.status = "ready"
                return p
            except Exception as cdp_err:
                logger.error(f"[{self.account_id}] Official Chrome CDP connection failed: {cdp_err}")
                self.status = "offline"
                raise

        pool_enabled = getattr(account_manager, "pre_warmed_pool_enabled", True)
        async with self.warm_lock:
            if not pool_enabled:
                if self.warm_page:
                    try:
                        if not self.warm_page.is_closed():
                            await self.warm_page.close()
                    except Exception:
                        pass
                    self.warm_page = None
            elif self.warm_page is not None:
                try:
                    if not self.warm_page.is_closed():
                        page = self.warm_page
                        self.warm_page = None
                        logger.info(f"[{self.account_id}] Reusing pre-warmed Gemini tab (zero navigation latency).")
                        if rewarm:
                            asyncio.create_task(self.prepare_warm_page())
                        return page
                except Exception:
                    self.warm_page = None

        logger.info(f"[{self.account_id}] Pool empty or cold; creating fresh page...")
        if not self.context:
            self.context = await account_manager.launch_context(self.profile_path, headless=True)

        try:
            page = await self.context.new_page()
            page.set_default_timeout(180000)
            page.set_default_navigation_timeout(180000)
        except Exception as ctx_err:
            logger.warning(f"[{self.account_id}] Context invalid ({ctx_err}). Auto-healing...")
            self.context = await account_manager.launch_context(self.profile_path, headless=True)
            page = await self.context.new_page()
            try:
                page.set_default_timeout(180000)
                page.set_default_navigation_timeout(180000)
            except Exception:
                pass

        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)
        nav_timeout = getattr(account_manager, "page_timeout", 45) * 1000
        await page.goto(GEMINI_URL, wait_until="domcontentloaded", timeout=nav_timeout)
        if pool_enabled and rewarm:
            asyncio.create_task(self.prepare_warm_page())
        return page

    async def close_warm_page(self):
        """Safely closes any pre-warmed tab held in the pool."""
        async with self.warm_lock:
            if self.warm_page:
                try:
                    if not self.warm_page.is_closed():
                        await self.warm_page.close()
                except Exception as e:
                    logger.debug(f"[{self.account_id}] Error closing warm page: {e}")
                self.warm_page = None

    async def close_browser(self):
        """Safely closes warm page and Chromium browser context to free RAM completely."""
        if getattr(self, "is_busy", False):
            return
        if getattr(self, "is_cdp", False):
            # For CDP, disconnect client without closing user's official Chrome window
            self.cdp_browser = None
            self.context = None
            return
        async with self.warm_lock:
            if self.warm_page:
                try:
                    if not self.warm_page.is_closed():
                        await self.warm_page.close()
                except Exception as e:
                    logger.debug(f"[{self.account_id}] Error closing warm page: {e}")
                self.warm_page = None
            if self.context:
                try:
                    await self.context.close()
                except Exception as e:
                    logger.debug(f"[{self.account_id}] Error closing context: {e}")
                self.context = None

    def record_latency(self, latency: float):
        """Records latency of a successful request and re-computes moving average."""
        if latency > 0:
            self.recent_latencies.append(latency)
            self.avg_latency = round(sum(self.recent_latencies) / len(self.recent_latencies), 2)

    def is_quota_protected(self, threshold: int = 95) -> bool:
        """Returns True if this account has reached or exceeded quota threshold (default 95%)."""
        if getattr(self, "quota_exhausted", False):
            return True
        w_pct = getattr(self, "weekly_usage_pct", None)
        c_pct = getattr(self, "current_usage_pct", None)
        if w_pct is not None and w_pct >= threshold:
            return True
        if c_pct is not None and c_pct >= threshold:
            return True
        return False

    def load_meta(self):
        """Loads persistent metadata like plan tier, auto_delete_history, is_paused, and usage stats from profile_meta.json."""
        meta_file = self.profile_path / "profile_meta.json"
        if meta_file.exists():
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self.plan = data.get("plan", self.plan or "Free")
                        self.auto_delete_history = bool(data.get("auto_delete_history", False))
                        self.is_paused = bool(data.get("is_paused", False))
                        self.is_calibrated = bool(data.get("is_calibrated", False))
                        self.current_usage_pct = data.get("current_usage_pct")
                        self.current_usage_reset = data.get("current_usage_reset")
                        self.weekly_usage_pct = data.get("weekly_usage_pct")
                        self.weekly_usage_reset = data.get("weekly_usage_reset")
                        self.usage_updated_at = data.get("usage_updated_at")
                        self.active_model_id = data.get("active_model_id")
                        self.active_thinking_level = data.get("active_thinking_level")
            except Exception as e:
                logger.debug(f"Could not load profile_meta.json for {self.account_id}: {e}")

    def save_meta(self):
        """Saves persistent metadata like plan tier, auto_delete_history, is_paused, and usage stats to profile_meta.json."""
        meta_file = self.profile_path / "profile_meta.json"
        try:
            self.profile_path.mkdir(parents=True, exist_ok=True)
            with open(meta_file, "w", encoding="utf-8") as f:
                json.dump({
                    "plan": self.plan,
                    "auto_delete_history": bool(self.auto_delete_history),
                    "is_paused": bool(getattr(self, "is_paused", False)),
                    "is_calibrated": bool(getattr(self, "is_calibrated", False)),
                    "current_usage_pct": getattr(self, "current_usage_pct", None),
                    "current_usage_reset": getattr(self, "current_usage_reset", None),
                    "weekly_usage_pct": getattr(self, "weekly_usage_pct", None),
                    "weekly_usage_reset": getattr(self, "weekly_usage_reset", None),
                    "usage_updated_at": getattr(self, "usage_updated_at", None),
                    "active_model_id": getattr(self, "active_model_id", None),
                    "active_thinking_level": getattr(self, "active_thinking_level", None),
                }, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save profile_meta.json for {self.account_id}: {e}")

    def to_dict(self) -> Dict[str, Any]:
        is_paused = bool(getattr(self, "is_paused", False))
        if self.status == "manual_control":
            effective_status = "manual_control"
        elif is_paused:
            effective_status = "paused"
        else:
            effective_status = self.status
        return {
            "account_id": self.account_id,
            "status": effective_status,
            "raw_status": self.status,
            "is_paused": is_paused,
            "in_manual_control": (self.status == "manual_control"),
            "has_portable_backup": (self.profile_path / "portable_session.json").exists(),
            "is_calibrated": bool(getattr(self, "is_calibrated", False)),
            "plan": getattr(self, "plan", "Free"),
            "auto_delete_history": bool(getattr(self, "auto_delete_history", False)),
            "current_usage_pct": getattr(self, "current_usage_pct", None),
            "current_usage_reset": getattr(self, "current_usage_reset", None),
            "weekly_usage_pct": getattr(self, "weekly_usage_pct", None),
            "weekly_usage_reset": getattr(self, "weekly_usage_reset", None),
            "usage_updated_at": getattr(self, "usage_updated_at", None),
            "is_busy": self.is_busy,
            "cancelled": bool(getattr(self, "cancelled", False)),
            "avg_latency": round(self.avg_latency, 2) if getattr(self, "avg_latency", 0) > 0 else None,
            "quota_exhausted": bool(getattr(self, "quota_exhausted", False)),
            "is_quota_protected": bool(self.is_quota_protected()),
            "is_cooling_down": self.is_cooling_down(),
            "cooldown_remaining_seconds": self.get_cooldown_remaining(),
            "cooldown_reason": getattr(self, "cooldown_reason", None),
            "has_warm_tab": bool(self.warm_page and not self.warm_page.is_closed()),
            "browser_running": bool(self.context is not None),
            "total_requests": self.total_requests,
            "last_used_at": self.last_used_at,
            "last_error": self.last_error,
            "profile_dir": str(self.profile_path),
            "source": self.source,
            "is_cdp": bool(getattr(self, "is_cdp", False)),
            "cdp_url": getattr(self, "cdp_url", ""),
        }

def get_profile_file_lockers(file_path) -> list[int]:
    """Uses Windows Restart Manager API to detect PIDs locking a specific file."""
    try:
        import ctypes
        from ctypes import wintypes
        rstrtmgr = ctypes.WinDLL('rstrtmgr')

        class RM_UNIQUE_PROCESS(ctypes.Structure):
            _fields_ = [('dwProcessId', wintypes.DWORD), ('ProcessStartTime', wintypes.FILETIME)]

        class RM_PROCESS_INFO(ctypes.Structure):
            _fields_ = [
                ('Process', RM_UNIQUE_PROCESS),
                ('strAppName', wintypes.WCHAR * 256),
                ('strServiceShortName', wintypes.WCHAR * 64),
                ('ApplicationType', wintypes.UINT),
                ('AppStatus', wintypes.ULONG),
                ('TSSessionId', wintypes.DWORD),
                ('bRestartable', wintypes.BOOL),
            ]

        session_handle = wintypes.DWORD()
        session_key = (wintypes.WCHAR * 33)()
        if rstrtmgr.RmStartSession(ctypes.byref(session_handle), 0, session_key) != 0:
            return []
        try:
            path_arr = (wintypes.LPCWSTR * 1)(str(Path(file_path).resolve()))
            if rstrtmgr.RmRegisterResources(session_handle, 1, path_arr, 0, None, 0, None) != 0:
                return []
            n_needed = wintypes.UINT(0)
            n_proc = wintypes.UINT(0)
            reasons = wintypes.DWORD()
            res = rstrtmgr.RmGetList(session_handle, ctypes.byref(n_needed), ctypes.byref(n_proc), None, ctypes.byref(reasons))
            if res not in (0, 234) or n_needed.value == 0:
                return []
            n_proc.value = n_needed.value
            proc_infos = (RM_PROCESS_INFO * n_proc.value)()
            if rstrtmgr.RmGetList(session_handle, ctypes.byref(n_needed), ctypes.byref(n_proc), proc_infos, ctypes.byref(reasons)) != 0:
                return []
            return [proc_infos[i].Process.dwProcessId for i in range(n_proc.value)]
        finally:
            rstrtmgr.RmEndSession(session_handle)
    except Exception:
        return []

def kill_profile_browser_processes(account_id: str):
    """
    Ruthlessly kills all chrome and headless-shell processes associated with account_id
    to release file locks before launching interactive Chrome.
    Uses Windows Restart Manager + taskkill process tree kill.
    """
    clean_id = account_id.strip() if account_id else ""
    if not clean_id or clean_id == "official_chrome":
        return

    # 1. Kill any process locking crucial profile files via Restart Manager
    p_path = PROFILES_DIR / clean_id
    targets = [
        p_path / "lockfile",
        p_path / "SingletonLock",
        p_path / "Default" / "Network" / "Cookies",
        p_path / "Default" / "Preferences",
        p_path / "Profile 1" / "Network" / "Cookies",
    ]
    for tf in targets:
        if tf.exists():
            for pid in get_profile_file_lockers(tf):
                if pid and pid != os.getpid():
                    try:
                        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
                    except Exception:
                        pass

    # 2. Kill matching browser processes by commandline (do NOT Stop-Process first so taskkill /T terminates children)
    import base64
    ps_kill = f"""
    $searcher = [System.Management.ManagementObjectSearcher]::new("SELECT ProcessId, CommandLine FROM Win32_Process WHERE Name LIKE '%chrome%' OR Name LIKE '%headless%'")
    foreach ($r in $searcher.Get()) {{
        $cmd = [string]$r["CommandLine"]
        if ($cmd -like "*{clean_id}*") {{
            if ($cmd -like "*--remote-debugging-port=9222*") {{
                continue
            }}
            $p = $r["ProcessId"]
            cmd.exe /c "taskkill /F /T /PID $p" | Out-Null
        }}
    }}
    """
    b64 = base64.b64encode(ps_kill.encode('utf-16le')).decode('ascii')
    try:
        subprocess.run(["powershell", "-NoProfile", "-EncodedCommand", b64], capture_output=True, timeout=8)
    except Exception as e:
        logger.debug(f"Error terminating processes for {clean_id}: {e}")

class AccountManager:
    def __init__(self):
        self.accounts: Dict[str, AccountSession] = {}
        self.playwright: Optional[Playwright] = None
        self.rr_lock = asyncio.Lock()
        self.rr_index = 0
        self.login_tasks: Dict[str, asyncio.Task] = {}
        self.manual_chrome_tasks: Dict[str, asyncio.Task] = {}
        self.routing_strategy: str = "balanced"  # balanced, lowest_latency, lowest_quota
        self.quota_guard_enabled: bool = True
        self.quota_threshold: int = 95
        self.generation_timeout: int = 180  # Default text generation timeout in seconds
        self.audio_timeout: int = 360       # Default audio transcription/upload timeout in seconds
        self.page_timeout: int = 45         # Default page load/navigation timeout in seconds
        self.pre_warmed_pool_enabled: bool = True  # Pre-warmed standby tab pool for zero navigation latency
        self.api_token: str = "sk-gemini-gateway-router"  # Gateway API Token / Bearer key
        self.require_api_token: bool = False  # Enforce Bearer token check on OpenAI /v1 endpoints
        self.captcha_solver_enabled: bool = True  # Auto-solve reCAPTCHA / unusual traffic
        self.captcha_provider: str = "free_audio"  # free_audio, capsolver, 2captcha
        self.captcha_api_key: str = ""             # API key for capsolver / 2captcha
        # Speed & Performance Optimization toggles
        self.block_heavy_media: bool = True        # Block heavy media (images, web fonts, media) in headless tabs
        self.turbo_dom_polling: bool = True        # Turbo DOM response check (0.12s instead of 0.25s)
        self.preferred_fast_model: str = "gemini-3.5-flash-lite"
        # Session Keep-Alive & Thread-Safety
        self.main_loop: Optional[asyncio.AbstractEventLoop] = None
        self.keepalive_task: Optional[asyncio.Task] = None
        self.session_keepalive_enabled: bool = True
        self.session_keepalive_interval_hours: int = 4
        self.show_chrome: bool = False
        self.load_settings()

        # Default model chosen at launch (run.bat menu) overrides settings.json for this run
        env_model = os.environ.get("GEMINI_DEFAULT_MODEL", "").strip()
        if env_model:
            if any(m["id"] == env_model for m in SUPPORTED_MODELS):
                self.preferred_fast_model = env_model
                logger.info(f"Default model set from GEMINI_DEFAULT_MODEL: {env_model}")
            else:
                logger.warning(f"GEMINI_DEFAULT_MODEL='{env_model}' is not a supported model ID. Keeping '{self.preferred_fast_model}'.")

    def load_settings(self):
        settings_file = PROFILES_DIR.parent / "settings.json"
        if settings_file.exists():
            try:
                data = json.loads(settings_file.read_text(encoding="utf-8"))
                self.routing_strategy = str(data.get("routing_strategy", self.routing_strategy))
                self.quota_guard_enabled = bool(data.get("quota_guard_enabled", self.quota_guard_enabled))
                self.quota_threshold = int(data.get("quota_threshold", self.quota_threshold))
                self.generation_timeout = int(data.get("generation_timeout", self.generation_timeout))
                self.audio_timeout = int(data.get("audio_timeout", self.audio_timeout))
                self.page_timeout = int(data.get("page_timeout", self.page_timeout))
                self.pre_warmed_pool_enabled = bool(data.get("pre_warmed_pool_enabled", self.pre_warmed_pool_enabled))
                self.api_token = str(data.get("api_token", self.api_token))
                self.require_api_token = bool(data.get("require_api_token", self.require_api_token))
                self.captcha_solver_enabled = bool(data.get("captcha_solver_enabled", self.captcha_solver_enabled))
                self.captcha_provider = str(data.get("captcha_provider", self.captcha_provider))
                self.captcha_api_key = str(data.get("captcha_api_key", self.captcha_api_key))
                self.block_heavy_media = bool(data.get("block_heavy_media", self.block_heavy_media))
                self.turbo_dom_polling = bool(data.get("turbo_dom_polling", self.turbo_dom_polling))
                self.preferred_fast_model = str(data.get("preferred_fast_model", self.preferred_fast_model))
                self.session_keepalive_enabled = bool(data.get("session_keepalive_enabled", self.session_keepalive_enabled))
                self.session_keepalive_interval_hours = int(data.get("session_keepalive_interval_hours", self.session_keepalive_interval_hours))
                self.show_chrome = bool(data.get("show_chrome", self.show_chrome))
                logger.info(f"Loaded gateway settings: GenTimeout={self.generation_timeout}s, AudioTimeout={self.audio_timeout}s, BlockMedia={self.block_heavy_media}, TurboPolling={self.turbo_dom_polling}, FastModel={self.preferred_fast_model}, ShowChrome={self.show_chrome}")
            except Exception as e:
                logger.warning(f"Failed to load settings.json: {e}")

    def save_settings(self):
        settings_file = PROFILES_DIR.parent / "settings.json"
        try:
            data = {
                "routing_strategy": self.routing_strategy,
                "quota_guard_enabled": self.quota_guard_enabled,
                "quota_threshold": self.quota_threshold,
                "generation_timeout": self.generation_timeout,
                "audio_timeout": self.audio_timeout,
                "page_timeout": self.page_timeout,
                "pre_warmed_pool_enabled": self.pre_warmed_pool_enabled,
                "api_token": self.api_token,
                "require_api_token": self.require_api_token,
                "captcha_solver_enabled": self.captcha_solver_enabled,
                "captcha_provider": self.captcha_provider,
                "captcha_api_key": self.captcha_api_key,
                "block_heavy_media": self.block_heavy_media,
                "turbo_dom_polling": self.turbo_dom_polling,
                "preferred_fast_model": self.preferred_fast_model,
                "session_keepalive_enabled": self.session_keepalive_enabled,
                "session_keepalive_interval_hours": self.session_keepalive_interval_hours,
                "show_chrome": self.show_chrome,
            }
            settings_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to save settings.json: {e}")

    async def set_pre_warmed_pool_enabled(self, enabled: bool):
        """Enables or disables pre-warmed standby tab pool. Immediately cleans up warm tabs and closes background Chrome if disabled."""
        self.pre_warmed_pool_enabled = bool(enabled)
        self.save_settings()
        logger.info(f"Pre-Warmed Tab Pool is now {'ENABLED' if self.pre_warmed_pool_enabled else 'DISABLED'}.")
        if self.pre_warmed_pool_enabled:
            for acc in self.accounts.values():
                if acc.status == "ready" and not getattr(acc, "is_paused", False) and not acc.is_cooling_down():
                    if not acc.context:
                        try:
                            acc.context = await self.launch_context(acc.profile_path, headless=True)
                        except Exception as e:
                            logger.warning(f"Failed to launch context for '{acc.account_id}': {e}")
                            continue
                    asyncio.create_task(acc.prepare_warm_page())
        else:
            for acc in self.accounts.values():
                if acc.account_id == "official_chrome" or getattr(acc, "is_cdp", False):
                    continue
                await acc.close_browser()

    async def close_all_browsers(self) -> int:
        """
        Immediately closes all Chrome browser contexts across all accounts to release RAM.
        Accounts remain in 'ready' state and dynamically re-launch on-demand when a request arrives.
        """
        closed_count = 0
        for acc_id, acc in self.accounts.items():
            if acc_id == "official_chrome" or getattr(acc, "is_cdp", False):
                continue
            if getattr(acc, "is_busy", False):
                continue
            if acc.context is not None or getattr(acc, "warm_page", None) is not None:
                await acc.close_browser()
                closed_count += 1
        logger.info(f"Closed {closed_count} Chrome browser context(s) to free system RAM.")
        return closed_count

    async def warmup_all_browsers(self) -> int:
        """Pre-launches Chrome contexts and prepares standby tabs for all ready accounts."""
        count = 0
        for acc_id, acc in self.accounts.items():
            if acc_id == "official_chrome" or getattr(acc, "is_cdp", False):
                continue
            if acc.status == "ready" and not getattr(acc, "is_paused", False) and not acc.is_cooling_down():
                if not acc.context:
                    try:
                        acc.context = await self.launch_context(acc.profile_path, headless=not getattr(self, "show_chrome", False))
                    except Exception as e:
                        logger.warning(f"Failed to launch context for '{acc_id}': {e}")
                        continue
                asyncio.create_task(acc.prepare_warm_page())
                count += 1
        logger.info(f"Initiated warm-up for {count} accounts.")
        return count

    async def initialize(self):
        PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        self.playwright = await async_playwright().start()
        logger.info(f"Playwright runtime started. Scanning profiles in {PROFILES_DIR}...")
        
        # Load all existing pool profiles (excluding official_chrome which is reserved for Port 9222 CDP)
        for item in PROFILES_DIR.iterdir():
            if item.is_dir() and item.name != "official_chrome":
                acc_id = item.name
                await self.load_headless_account(acc_id)

        active = sum(1 for a in self.accounts.values() if a.status == "ready")
        logger.info(f"Loaded {len(self.accounts)} profile(s), {active} ready for requests.")

        # Pre-warm tabs for all ready accounts if pool enabled
        if self.pre_warmed_pool_enabled:
            for acc in self.accounts.values():
                if acc.status == "ready":
                    asyncio.create_task(acc.prepare_warm_page())

        # Probe and auto-connect Official Chrome CDP (Port 9222) if already active
        try:
            await self.check_official_chrome_cdp()
        except Exception as cdp_init_err:
            logger.debug(f"Official Chrome CDP init probe: {cdp_init_err}")

        # Start Session Keep-Alive Heartbeat in background
        if self.session_keepalive_enabled and (self.keepalive_task is None or self.keepalive_task.done()):
            self.keepalive_task = asyncio.create_task(self._session_keepalive_loop())

    async def trigger_session_keepalive(self) -> Dict[str, Any]:
        """
        Runs an immediate session keep-alive check and refresh across all accounts.
        Auto-restores missing cookies and refreshes Google rolling tokens (__Secure-1PSIDTS / OSID).
        """
        logger.info("[KeepAlive] Triggering session keep-alive heartbeat across accounts...")
        results = {}
        for account_id, acc in list(self.accounts.items()):
            if acc.status not in ("ready", "needs_login") or getattr(acc, "is_paused", False):
                continue
            if getattr(acc, "is_busy", False):
                results[account_id] = {"status": "skipped", "reason": "busy"}
                continue
            if not acc.context:
                results[account_id] = {"status": "skipped", "reason": "no_context"}
                continue

            try:
                profile_path = PROFILES_DIR / account_id
                session_backup = profile_path / "portable_session.json"

                cookies = await acc.context.cookies(["https://gemini.google.com", "https://accounts.google.com"])
                auth_cnt = sum(1 for c in cookies if c.get("name") in ("SID", "SSID", "__Secure-1PSID", "SAPISID", "OSID"))

                if auth_cnt < 2 and session_backup.exists():
                    logger.info(f"[{account_id}] KeepAlive detected missing cookies ({auth_cnt}). Restoring from portable backup...")
                    res = await self.restore_portable_session(account_id)
                    if getattr(acc, "warm_page", None):
                        await acc.close_warm_page()
                    if not res.get("success"):
                        acc.status = "needs_login"
                        acc.last_error = f"Not signed in to Google. {NOT_SIGNED_IN_HINT}"
                        results[account_id] = {"status": "needs_login", "error": res.get("error")}
                        continue
                    if self.pre_warmed_pool_enabled:
                        asyncio.create_task(acc.prepare_warm_page())
                    results[account_id] = {"status": "restored_from_backup", "cookies": res.get("restored_cookies", 0)}
                else:
                    # Rolling refresh: visit gemini.google.com/app to update __Secure-1PSIDTS
                    async with acc.lock:
                        acc.is_busy = True
                        try:
                            page = await acc.context.new_page()
                            try:
                                await page.goto(GEMINI_URL, wait_until="domcontentloaded", timeout=25000)
                                await asyncio.sleep(2)
                                await self.backup_portable_session(account_id)
                                results[account_id] = {"status": "refreshed_and_saved", "auth_cookies": auth_cnt}
                            finally:
                                try:
                                    await page.close()
                                except Exception:
                                    pass
                        finally:
                            acc.is_busy = False
            except Exception as err:
                logger.warning(f"[{account_id}] KeepAlive check failed: {err}")
                results[account_id] = {"status": "error", "error": str(err)}

        return results

    async def _session_keepalive_loop(self):
        """
        Background daemon that runs session keep-alive every N hours.
        Prevents Google's 24-hour cookie expiration and auto-heals corrupted sessions.
        """
        logger.info("Session Keep-Alive Heartbeat service started.")
        # Initial sleep so startup finishes smoothly
        await asyncio.sleep(90)

        while True:
            try:
                interval_secs = max(3600, getattr(self, "session_keepalive_interval_hours", 4) * 3600)
                await asyncio.sleep(interval_secs)
                if getattr(self, "session_keepalive_enabled", True):
                    await self.trigger_session_keepalive()
                # Periodically probe Official Chrome CDP in background
                try:
                    await self.check_official_chrome_cdp()
                except Exception:
                    pass
            except asyncio.CancelledError:
                break
            except Exception as loop_err:
                logger.error(f"[KeepAlive] Background loop error: {loop_err}")
                await asyncio.sleep(120)

    async def check_official_chrome_cdp(self, port: int = 9222) -> bool:
        """
        Probes Official Google Chrome CDP on localhost:{port}.
        If running, inspects the active Gemini page to detect if user is signed in.
        If signed in, status = 'ready'; if not signed in, status = 'needs_login'.
        """
        url = f"http://127.0.0.1:{port}"
        loop = asyncio.get_running_loop()
        def _probe_socket():
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.5)
            try:
                s.connect(("127.0.0.1", port))
                s.close()
                return True
            except Exception:
                return False

        is_alive = await loop.run_in_executor(None, _probe_socket)

        account_id = "official_chrome"
        if is_alive:
            if account_id not in self.accounts:
                profile_path = PROFILES_DIR / account_id
                acc = AccountSession(account_id, profile_path)
                acc.is_cdp = True
                acc.cdp_url = url
                acc.email = "Official Chrome (Fast Lane 6s)"
                acc.plan = "Pro"
                acc.color = "#00E5FF"
                self.accounts[account_id] = acc

            acc = self.accounts[account_id]
            acc.is_cdp = True
            acc.cdp_url = url
            
            # Probe page state via CDP if playwright is active and account is not actively processing
            if self.playwright and not getattr(acc, "is_busy", False):
                cdp_b = None
                try:
                    async with asyncio.timeout(2.0):
                        cdp_b = await self.playwright.chromium.connect_over_cdp(url)
                        p_ctx = cdp_b.contexts[0]
                        accounts_pages = [p for p in p_ctx.pages if "accounts.google.com" in p.url]
                        gemini_pages = [p for p in p_ctx.pages if "gemini.google.com" in p.url]
                        
                        if accounts_pages and not gemini_pages:
                            acc.status = "needs_login"
                        elif gemini_pages:
                            pg = gemini_pages[0]
                            dom_info = await pg.evaluate("""() => {
                                const url = window.location.href;
                                const hasInput = !!document.querySelector("rich-textarea, div[contenteditable='true'], textarea");
                                const avatarEl = document.querySelector("a[href*='SignOutOptions'], [aria-label*='Google Account' i], [aria-label*='Google គណនី' i], [aria-label*='គណនី Google' i], img[alt*='Google Account' i]");
                                let email = null;
                                if (avatarEl) {
                                    const aria = avatarEl.getAttribute('aria-label') || '';
                                    const m = aria.match(/[\\w\\.-]+@[\\w\\.-]+\\.\\w+/);
                                    if (m) email = m[0];
                                }
                                const isServiceLogin = url.includes('accounts.google.com') || (url.includes('ServiceLogin') && !hasInput);
                                return {
                                    has_input: hasInput,
                                    email: email,
                                    is_login_page: isServiceLogin
                                };
                            }""")
                            if dom_info.get("is_login_page"):
                                acc.status = "needs_login"
                            else:
                                acc.status = "ready"
                                if dom_info.get("email"):
                                    acc.email = dom_info["email"]
                        else:
                            acc.status = "ready"
                except Exception:
                    acc.status = "ready"
                finally:
                    if cdp_b:
                        try:
                            await cdp_b.close()
                        except Exception:
                            pass
            else:
                acc.status = "ready"

            return True
        else:
            if account_id in self.accounts:
                acc = self.accounts[account_id]
                acc.status = "offline"
                if getattr(acc, "context", None):
                    try:
                        if getattr(acc, "cdp_browser", None):
                            await acc.cdp_browser.close()
                    except Exception:
                        pass
                    acc.cdp_browser = None
                    acc.context = None
            return False

    async def launch_official_chrome_cdp(self, port: int = 9222, profile_dir: Optional[str] = None) -> Dict[str, Any]:
        """
        Launches Official Google Chrome with remote debugging enabled on port 9222.
        Uses our dedicated authenticated profile (e.g. bolenstudio1993) so Gemini is already logged in.
        """
        import subprocess
        chrome_exe = get_chrome_executable_path()
        if not chrome_exe or not os.path.exists(chrome_exe):
            return {"success": False, "error": "Google Chrome executable not found on host."}

        # Resolve target profile from PROFILES_DIR and host Chrome User Data
        acc_id = "bolenstudio1993"
        chrome_user_dir = "Profile 24"
        if profile_dir:
            clean_p = profile_dir.strip().lower()
            if "ricky" in clean_p or "profile 25" in clean_p:
                acc_id = "rickywoo169"
                chrome_user_dir = "Profile 25"
            elif "sokchan" in clean_p or "profile 1" in clean_p:
                acc_id = "sokchanvichea3"
                chrome_user_dir = "Profile 1"
            elif "houy" in clean_p or "profile 31" in clean_p:
                acc_id = "houyhouy1993"
                chrome_user_dir = "Profile 31"
            elif "bolen" in clean_p or "profile 24" in clean_p:
                acc_id = "bolenstudio1993"
                chrome_user_dir = "Profile 24"
            elif (PROFILES_DIR / profile_dir).exists():
                acc_id = profile_dir
                acc_obj = self.accounts.get(profile_dir)
                if acc_obj and getattr(acc_obj, "source", "") and "chrome:" in acc_obj.source:
                    chrome_user_dir = acc_obj.source.split("chrome:")[1].strip()

        # If already running on port, don't kill; just return existing connection
        if await self.check_official_chrome_cdp(port=port):
            return {"success": True, "message": f"Official Chrome already active on port {port}!"}

        # Dedicated profile directory for official_chrome so it never conflicts with pool accounts!
        target_dir = (PROFILES_DIR / "official_chrome").resolve()
        target_dir.mkdir(parents=True, exist_ok=True)

        # Clone host Chrome master encryption state & session if available for 1-click zero-sign-in
        import shutil
        host_user_data = Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"
        if host_user_data.exists():
            try:
                host_ls = host_user_data / "Local State"
                if host_ls.exists():
                    shutil.copy2(host_ls, target_dir / "Local State")
                
                src_prof = host_user_data / chrome_user_dir
                dst_prof = target_dir / "Default"
                if src_prof.exists() and not (dst_prof / "Network" / "Cookies").exists():
                    shutil.copytree(src_prof, dst_prof, ignore=shutil.ignore_patterns("Cache*", "Code Cache*", "GPUCache*"), dirs_exist_ok=True)
            except Exception as clone_err:
                logger.debug(f"[OfficialChrome] Profile clone note: {clone_err}")

        # Sync session backup from the chosen profile
        source_session = PROFILES_DIR / acc_id / "portable_session.json"
        dest_session = target_dir / "portable_session.json"
        if source_session.exists():
            try:
                shutil.copy2(source_session, dest_session)
            except Exception:
                pass

        # Clean lock files
        for lock_file in [target_dir / "lockfile", target_dir / "SingletonLock", target_dir / "SingletonCookie", target_dir / "SingletonSocket"]:
            try:
                lock_file.unlink(missing_ok=True)
            except Exception:
                pass

        cmd_list = [
            "cmd.exe",
            "/c",
            "start",
            '""',
            f'"{chrome_exe}"',
            f"--remote-debugging-port={port}",
            "--remote-allow-origins=*",
            f'--user-data-dir="{str(target_dir)}"',
            "--profile-directory=Default",
            "--no-first-run",
            "--no-default-browser-check",
            "https://gemini.google.com/app"
        ]
        cmd_str = " ".join(cmd_list)
        try:
            subprocess.Popen(cmd_str, shell=True)
            for _ in range(12):
                await asyncio.sleep(0.5)
                if await self.check_official_chrome_cdp(port=port):
                    return {"success": True, "message": f"Official Chrome connected on port {port} ({acc_id})!"}
            return {
                "success": True,
                "message": f"Official Chrome process started ({acc_id}). Connecting on port {port}..."
            }
        except Exception as e:
            logger.error(f"Failed to launch Official Chrome CDP: {e}")
            return {"success": False, "error": str(e)}

    async def launch_context(self, profile_path: Path, headless: bool) -> BrowserContext:
        """
        Launches a persistent browser context with anti-bot evasion settings.
        Uses system Chrome channel when available for seamless DPAPI decryption.
        """
        effective_headless = headless
        if getattr(self, "show_chrome", False) or os.environ.get("SHOW_CHROME", "").lower() in ("1", "true", "yes"):
            effective_headless = False

        # Ensure any orphan Chrome processes holding this specific profile directory are terminated
        account_name = profile_path.name
        try:
            kill_profile_browser_processes(account_name)
        except Exception as k_err:
            logger.debug(f"Kill orphan browser note: {k_err}")

        # Clean up stale locks before launching to prevent ProcessSingleton deadlocks on Chrome Portable
        for lock_file in [profile_path / "lockfile", profile_path / "SingletonLock", profile_path / "SingletonCookie", profile_path / "SingletonSocket"]:
            try:
                if lock_file.exists():
                    lock_file.unlink(missing_ok=True)
            except Exception:
                pass

        launch_kwargs = {
            "user_data_dir": str(profile_path),
            "headless": effective_headless,
            "ignore_default_args": [
                "--enable-automation",
                "--disable-background-networking",
                "--disable-field-trial-config",
                "--disable-extensions",
                "--disable-default-apps",
                "--disable-component-update",
                "--disable-client-side-phishing-detection",
                "--enable-unsafe-swiftshader",
            ],
            "args": CHROMIUM_ARGS,
            "user_agent": DEFAULT_USER_AGENT,
            "viewport": {"width": 1280, "height": 850},
            "locale": "en-US",
            "timezone_id": "America/New_York",
            "ignore_https_errors": True,
            "timeout": 180000,
        }
        
        # Explicitly point Playwright directly to official Google Chrome
        system_chrome_exe = get_chrome_executable_path()
        if system_chrome_exe and os.path.exists(system_chrome_exe):
            launch_kwargs["executable_path"] = system_chrome_exe

        context = None
        try:
            context = await self.playwright.chromium.launch_persistent_context(
                **launch_kwargs
            )
        except Exception as first_err:
            if "existing browser session" in str(first_err).lower() or "singleton" in str(first_err).lower():
                logger.warning(f"[{account_name}] Profile locked by existing browser. Terminating orphan Chrome and auto-healing...")
                try:
                    kill_profile_browser_processes(account_name)
                except Exception:
                    pass
                await asyncio.sleep(1.0)
                for lock_file in [profile_path / "lockfile", profile_path / "SingletonLock", profile_path / "SingletonCookie", profile_path / "SingletonSocket"]:
                    try:
                        lock_file.unlink(missing_ok=True)
                    except Exception:
                        pass
                context = await self.playwright.chromium.launch_persistent_context(
                    **launch_kwargs
                )
            else:
                # Fallback to channel="chrome"
                launch_kwargs.pop("executable_path", None)
                context = await self.playwright.chromium.launch_persistent_context(
                    channel="chrome",
                    **launch_kwargs
                )

        # Set default timeouts to 180s to prevent Playwright 30s hard-timeout freezes
        try:
            context.set_default_timeout(180000)
            context.set_default_navigation_timeout(180000)
        except Exception:
            pass

        # Auto-restore session cookies from portable_session.json if available
        session_file = profile_path / "portable_session.json"
        if session_file.exists():
            try:
                raw_text = session_file.read_text(encoding="utf-8")
                s_data = json.loads(raw_text)
                s_cookies = s_data.get("state", {}).get("cookies", [])
                if s_cookies:
                    valid_c = []
                    for c in s_cookies:
                        cd = {
                            "name": str(c.get("name", "")),
                            "value": str(c.get("value", "")),
                            "domain": str(c.get("domain", "")),
                            "path": str(c.get("path", "/")),
                        }
                        if "expires" in c and c["expires"] is not None:
                            cd["expires"] = float(c["expires"])
                        if "httpOnly" in c:
                            cd["httpOnly"] = bool(c["httpOnly"])
                        if "secure" in c:
                            cd["secure"] = bool(c["secure"])
                        if "sameSite" in c and c["sameSite"] in ["Strict", "Lax", "None"]:
                            cd["sameSite"] = c["sameSite"]
                        valid_c.append(cd)
                    if valid_c:
                        await context.add_cookies(valid_c)
                        logger.info(f"[{account_name}] Auto-restored {len(valid_c)} session cookies on context launch.")
            except Exception as s_err:
                logger.debug(f"[{account_name}] Auto cookie restore note: {s_err}")

        # Global anti-bot evasion across all pages, frames, and popups
        try:
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
                window.chrome = window.chrome || { runtime: {} };
            """)
        except Exception:
            pass

        # Smart Network Route Interception: block heavy telemetry, ad analytics, and non-essential trackers
        try:
            async def _route_filter(route):
                req = route.request
                req_url = req.url.lower()
                res_type = req.resource_type.lower() if hasattr(req, "resource_type") else ""

                # 1. Always exclude analytics, telemetry, and tracking endpoints
                blocked_patterns = [
                    "google-analytics.com",
                    "googletagmanager.com",
                    "play.google.com/log",
                    "pagead2.googlesyndication.com",
                    "stats.g.doubleclick.net",
                ]
                if any(p in req_url for p in blocked_patterns):
                    await route.abort()
                    return

                # 2. Speed Optimization: Block heavy media (Images, Web Fonts, Media) if enabled
                if getattr(self, "block_heavy_media", True):
                    if res_type in ["image", "font", "media"]:
                        await route.abort()
                        return

                await route.continue_()

            await context.route("**/*", _route_filter)
        except Exception as route_err:
            logger.debug(f"Route filter setup note: {route_err}")

        return context

    async def load_headless_account(self, account_id: str) -> AccountSession:
        """Loads or reloads an account profile in headless mode."""
        profile_path = PROFILES_DIR / account_id
        profile_path.mkdir(parents=True, exist_ok=True)

        if account_id in self.accounts:
            existing = self.accounts[account_id]
            if existing.context:
                try:
                    await existing.close_warm_page()
                    await existing.context.close()
                except Exception as e:
                    logger.debug(f"Error closing previous context for {account_id}: {e}")
        else:
            self.accounts[account_id] = AccountSession(account_id, profile_path)

        acc = self.accounts[account_id]
        
        # If Chrome saved login session to a sub-profile like 'Profile 1' or 'Profile 24', seed Default if Default has no cookies
        try:
            d_cookies = profile_path / "Default" / "Network" / "Cookies"
            if not d_cookies.exists():
                profile_dirs = [d for d in profile_path.iterdir() if d.is_dir() and d.name.startswith("Profile ")]
                for p_dir in profile_dirs:
                    p_cookies = p_dir / "Network" / "Cookies"
                    if p_cookies.exists():
                        shutil.copytree(p_dir, profile_path / "Default", dirs_exist_ok=True, ignore=shutil.ignore_patterns("*.lock", "*LOCK*", "lockfile"))
                        logger.info(f"[{account_id}] Seeded 'Default' profile from '{p_dir.name}'.")
                        break
        except Exception as sync_err:
            logger.debug(f"Sync error: {sync_err}")

        try:
            is_headless = not (getattr(self, "show_chrome", False) or os.environ.get("SHOW_CHROME", "").lower() in ("1", "true", "yes"))
            acc.context = await self.launch_context(profile_path, headless=is_headless)
            acc.status = "ready"
            acc.last_error = None
            logger.info(f"Account '{account_id}' successfully loaded in {'headless' if is_headless else 'headed'} mode.")

            # Step 1: Check and restore portable session FIRST (before pre-warming tab)
            try:
                auth_cnt = await context_google_auth_count(acc.context)
                session_backup = profile_path / "portable_session.json"
                if auth_cnt < 2 and session_backup.exists():
                    logger.info(f"[{account_id}] Missing Google auth cookies ({auth_cnt}). Auto-restoring portable session...")
                    await self.restore_portable_session(account_id)
                    auth_cnt = await context_google_auth_count(acc.context)
                elif auth_cnt >= 2:
                    # Update portable session backup in background so it stays fresh
                    asyncio.create_task(self.backup_portable_session(account_id))

                if auth_cnt < 2:
                    # No Google login in this profile and nothing usable to restore: the account would
                    # only reach Gemini's signed-out guest page, so keep it out of the request pool.
                    acc.status = "needs_login"
                    acc.last_error = f"Not signed in to Google. {NOT_SIGNED_IN_HINT}"
                    logger.warning(f"[{account_id}] Not signed in to Google (no login cookies). Marked 'needs_login'. {NOT_SIGNED_IN_HINT}")
            except Exception as e_chk:
                logger.debug(f"[{account_id}] Session check note: {e_chk}")

            # Step 2: Pre-warm tab with authenticated cookies
            if self.pre_warmed_pool_enabled and acc.status == "ready":
                asyncio.create_task(acc.prepare_warm_page())
        except Exception as e:
            logger.error(f"Failed to launch headless context for '{account_id}': {e}")
            acc.status = "error"
            acc.last_error = str(e)

        return acc

    async def start_login_session(self, account_id: str):
        """Opens a native visible Google Chrome window on the user's desktop for manual Google authentication."""
        profile_path = PROFILES_DIR / account_id
        profile_path.mkdir(parents=True, exist_ok=True)

        # 1. Close headless context if open
        if account_id in self.accounts:
            acc = self.accounts[account_id]
            if acc.context:
                try:
                    await acc.close_warm_page()
                    await acc.context.close()
                except Exception as e:
                    logger.debug(f"Closed existing context: {e}")
                acc.context = None
            acc.status = "logging_in"
        else:
            self.accounts[account_id] = AccountSession(account_id, profile_path)
            self.accounts[account_id].status = "logging_in"

        acc = self.accounts[account_id]

        # 2. Cancel previous login task if any
        if account_id in self.login_tasks:
            try:
                self.login_tasks[account_id].cancel()
            except Exception:
                pass

        # 3. Force kill any existing background Chrome/headless-shell instances holding this profile directory
        kill_profile_browser_processes(account_id)

        await asyncio.sleep(1.0)

        # 4. Remove stale lockfiles that cause Chrome abort code 21 (ERROR_SHARING_VIOLATION)
        for lf in ["lockfile", "SingletonLock", "SingletonCookie", "SingletonSocket", "SingletonSocket.sock"]:
            try:
                (profile_path / lf).unlink(missing_ok=True)
            except Exception:
                pass
        for item in profile_path.glob("**/Singleton*"):
            try:
                item.unlink(missing_ok=True)
            except Exception:
                pass

        # 5. Launch interactive login on user's desktop
        launcher_bat = PROFILES_DIR.parent / f"_launch_login_{account_id}.bat"
        py_exe = sys.executable.replace("pythonw.exe", "python.exe")
        launcher_content = f"""@echo off
title Gemini Web Router - Google Sign-in: {account_id}
cd /d "%~dp0"
set "PY_EXE=%~dp0python\\python.exe"
if not exist "%PY_EXE%" set "PY_EXE={py_exe}"
if not exist "%PY_EXE%" set "PY_EXE=python.exe"
if exist "%~dp0ms-playwright" set "PLAYWRIGHT_BROWSERS_PATH=%~dp0ms-playwright"
echo ========================================================
echo    Gemini Web API Call Router - Google Account Login
echo    Account: {account_id}
echo ========================================================
echo.
"%PY_EXE%" login_chrome.py {account_id}
echo.
pause
"""
        launcher_bat.write_text(launcher_content, encoding="utf-8")

        logger.info(f"Launching interactive desktop login window for '{account_id}'...")
        launched = False

        # 1. Primary method: Launch via Windows Task Scheduler with /it (Interactive)
        # This breaks out of any background/service/IDE sandbox directly onto the user's active desktop!
        task_name = f"GeminiLogin_{account_id}"
        try:
            bat_path = str(launcher_bat.resolve())
            subprocess.run(
                ["schtasks", "/create", "/tn", task_name, "/tr", f'cmd.exe /c "{bat_path}"', "/sc", "once", "/st", "23:59", "/f", "/it"],
                capture_output=True,
                check=True
            )
            subprocess.run(["schtasks", "/run", "/tn", task_name], capture_output=True, check=True)
            subprocess.run(["schtasks", "/delete", "/tn", task_name, "/f"], capture_output=True)
            launched = True
            logger.info(f"Successfully launched login window for '{account_id}' via interactive Task Scheduler (/it)")
        except Exception as e_sch:
            logger.warning(f"Task Scheduler interactive launch failed ({e_sch}), falling back to direct launch...")

        # 2. Secondary method: Launch explicitly on interactive desktop (WinSta0\default)
        if not launched:
            try:
                si = subprocess.STARTUPINFO()
                si.lpDesktop = r"WinSta0\default"
                proc = subprocess.Popen(
                    ["cmd.exe", "/c", str(launcher_bat.resolve())],
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                    startupinfo=si,
                    cwd=str(PROFILES_DIR.parent)
                )
                launched = True
                logger.info(f"Successfully launched login window for '{account_id}' on WinSta0\\default (PID: {proc.pid})")
            except Exception as e:
                logger.warning(f"Failed to launch on WinSta0\\default ({e}), falling back to os.startfile...")

        if not launched:
            try:
                os.startfile(str(launcher_bat.resolve()))
                launched = True
                logger.info(f"Successfully launched login window for '{account_id}' via os.startfile")
            except Exception as e:
                logger.warning(f"os.startfile failed: {e}")

        if not launched:
            try:
                proc = subprocess.Popen(
                    [py_exe, "login_chrome.py", account_id],
                    cwd=str(PROFILES_DIR.parent),
                    creationflags=subprocess.CREATE_NEW_CONSOLE
                )
                acc.login_proc = proc
                launched = True
                logger.info(f"Successfully launched login_chrome.py for '{account_id}' via CREATE_NEW_CONSOLE")
            except Exception as e:
                logger.error(f"All launcher methods failed for '{account_id}': {e}")

    async def open_manual_chrome(self, account_id: str):
        """
        Opens a visible Google Chrome window on the user's desktop for pure manual browsing and control.
        Releases the headless Playwright lock, marks status as 'manual_control', and launches native Chrome.
        """
        clean_id = account_id.strip()
        profile_path = PROFILES_DIR / clean_id
        profile_path.mkdir(parents=True, exist_ok=True)
        (profile_path / "Default").mkdir(parents=True, exist_ok=True)

        # 1. Close headless context if running
        if clean_id in self.accounts:
            acc = self.accounts[clean_id]
            if acc.context:
                try:
                    await acc.context.close()
                except Exception as e:
                    logger.debug(f"Closed existing context for {clean_id}: {e}")
                acc.context = None
            acc.status = "manual_control"
        else:
            self.accounts[clean_id] = AccountSession(clean_id, profile_path)
            self.accounts[clean_id].status = "manual_control"

        acc = self.accounts[clean_id]

        # 2. Cancel any running login task or previous manual watcher
        if clean_id in self.login_tasks:
            try:
                self.login_tasks[clean_id].cancel()
            except Exception:
                pass
        if clean_id in self.manual_chrome_tasks:
            try:
                self.manual_chrome_tasks[clean_id].cancel()
            except Exception:
                pass

        # 3. Kill stray background browser processes and clean locks
        kill_profile_browser_processes(clean_id)
        await asyncio.sleep(0.5)

        for lf in ["lockfile", "SingletonLock", "SingletonCookie", "SingletonSocket", "SingletonSocket.sock"]:
            try:
                (profile_path / lf).unlink(missing_ok=True)
            except Exception:
                pass
        for item in profile_path.glob("**/Singleton*"):
            try:
                item.unlink(missing_ok=True)
            except Exception:
                pass

        # 4. Generate launcher bat file for manual control
        launcher_bat = PROFILES_DIR.parent / f"_launch_manual_{clean_id}.bat"
        py_exe = sys.executable.replace("pythonw.exe", "python.exe")
        launcher_content = f"""@echo off
title Gemini Web Router - Manual Chrome: {clean_id}
cd /d "%~dp0"
set "PY_EXE=%~dp0python\\python.exe"
if not exist "%PY_EXE%" set "PY_EXE={py_exe}"
if not exist "%PY_EXE%" set "PY_EXE=python.exe"
if exist "%~dp0ms-playwright" set "PLAYWRIGHT_BROWSERS_PATH=%~dp0ms-playwright"
echo ========================================================
echo    Gemini Web API Call Router - Manual Chrome Control
echo    Account: {clean_id}
echo ========================================================
echo.
echo Launching Google Chrome for manual browsing...
"%PY_EXE%" manual_chrome.py "{clean_id}"
"""
        launcher_bat.write_text(launcher_content, encoding="utf-8")

        # 5. Launch interactive window on user's desktop
        logger.info(f"Launching manual Chrome window for '{clean_id}'...")
        launched = False

        task_name = f"GeminiManual_{clean_id}"
        try:
            bat_path = str(launcher_bat.resolve())
            subprocess.run(
                ["schtasks", "/create", "/tn", task_name, "/tr", f'cmd.exe /c "{bat_path}"', "/sc", "once", "/st", "23:59", "/f", "/it"],
                capture_output=True,
                check=True
            )
            subprocess.run(["schtasks", "/run", "/tn", task_name], capture_output=True, check=True)
            subprocess.run(["schtasks", "/delete", "/tn", task_name, "/f"], capture_output=True)
            launched = True
            logger.info(f"Successfully launched manual Chrome for '{clean_id}' via interactive Task Scheduler (/it)")
        except Exception as e_sch:
            logger.warning(f"Task Scheduler interactive launch failed ({e_sch}), falling back to direct launch...")

        if not launched:
            try:
                si = subprocess.STARTUPINFO()
                si.lpDesktop = r"WinSta0\default"
                proc = subprocess.Popen(
                    ["cmd.exe", "/c", str(launcher_bat.resolve())],
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                    startupinfo=si,
                    cwd=str(PROFILES_DIR.parent)
                )
                acc.manual_chrome_proc = proc
                launched = True
                logger.info(f"Successfully launched manual Chrome for '{clean_id}' on WinSta0\\default (PID: {proc.pid})")
            except Exception as e:
                logger.warning(f"Failed to launch on WinSta0\\default ({e}), falling back to os.startfile...")

        if not launched:
            try:
                os.startfile(str(launcher_bat.resolve()))
                launched = True
                logger.info(f"Successfully launched manual Chrome for '{clean_id}' via os.startfile")
            except Exception as e:
                logger.warning(f"os.startfile failed: {e}")

        if not launched:
            try:
                proc = subprocess.Popen(
                    [py_exe, "manual_chrome.py", clean_id],
                    cwd=str(PROFILES_DIR.parent),
                    creationflags=subprocess.CREATE_NEW_CONSOLE
                )
                acc.manual_chrome_proc = proc
                launched = True
                logger.info(f"Successfully launched manual_chrome.py for '{clean_id}' via CREATE_NEW_CONSOLE")
            except Exception as e:
                logger.error(f"All launcher methods failed for '{clean_id}': {e}")
                acc.status = "error"
                acc.last_error = f"Failed to launch Chrome: {e}"

    async def close_manual_chrome(self, account_id: str):
        """Terminates manual Chrome window and re-attaches account into headless pool."""
        clean_id = account_id.strip()
        logger.info(f"Closing manual Chrome session and restoring '{clean_id}' to headless pool...")

        if clean_id in self.manual_chrome_tasks:
            try:
                self.manual_chrome_tasks[clean_id].cancel()
            except Exception:
                pass
            self.manual_chrome_tasks.pop(clean_id, None)

        if clean_id in self.accounts:
            proc = getattr(self.accounts[clean_id], "manual_chrome_proc", None)
            if proc and proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass

        # Force terminate any remaining browser processes locking this profile
        kill_profile_browser_processes(clean_id)
        await asyncio.sleep(0.5)

        # Clean lock files
        p_path = PROFILES_DIR / clean_id
        for lf in ["lockfile", "SingletonLock", "SingletonCookie", "SingletonSocket", "SingletonSocket.sock"]:
            try:
                (p_path / lf).unlink(missing_ok=True)
            except Exception:
                pass
        for item in p_path.glob("**/Singleton*"):
            try:
                item.unlink(missing_ok=True)
            except Exception:
                pass

        # Reload profile into headless pool
        acc = await self.load_headless_account(clean_id)
        # Update portable session backup in background so new login tokens survive Windows reinstall
        asyncio.create_task(self.backup_portable_session(clean_id))
        return acc

    async def backup_portable_session(self, account_id: str) -> Dict[str, Any]:
        """
        Exports decrypted session cookies & storage for account_id to profiles/{account_id}/portable_session.json.
        This enables accounts to stay logged in even if Windows is completely reset or reinstalled.
        """
        clean_id = account_id.strip()
        if clean_id not in self.accounts:
            return {"success": False, "error": f"Account '{clean_id}' not found"}

        acc = self.accounts[clean_id]
        if not acc.context:
            return {"success": False, "error": f"Account '{clean_id}' has no active browser context"}

        try:
            profile_path = PROFILES_DIR / clean_id
            session_file = profile_path / "portable_session.json"
            state = await acc.context.storage_state()
            cookies = state.get("cookies", [])
            auth_cookies = [c for c in cookies if c.get("name") in ("SID", "SSID", "__Secure-1PSID", "SAPISID")]

            # Never replace a backup that holds a real Google login with a signed-out snapshot.
            if len(auth_cookies) < 2 and session_file.exists():
                try:
                    prev = json.loads(session_file.read_text(encoding="utf-8"))
                    if count_google_auth_cookies(prev.get("state", {}).get("cookies", [])) >= 2:
                        logger.warning(f"[{clean_id}] Skipped portable session backup: browser is signed out and the existing backup still has a login.")
                        return {"success": False, "error": "Account is signed out; kept the existing signed-in backup."}
                except Exception:
                    pass

            payload = {
                "account_id": clean_id,
                "saved_at": time.time(),
                "saved_date": time.strftime("%Y-%m-%d %H:%M:%S"),
                "total_cookies": len(cookies),
                "auth_tokens_count": len(auth_cookies),
                "has_auth": len(auth_cookies) >= 2,
                "state": state,
            }
            session_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info(f"[{clean_id}] Portable session backup saved ({len(cookies)} cookies, {len(auth_cookies)} auth tokens).")
            return {
                "success": True,
                "account_id": clean_id,
                "total_cookies": len(cookies),
                "auth_tokens_count": len(auth_cookies),
                "saved_at": payload["saved_date"],
            }
        except Exception as e:
            logger.error(f"[{clean_id}] Failed to backup portable session: {e}")
            return {"success": False, "error": str(e)}

    async def restore_portable_session(self, account_id: str) -> Dict[str, Any]:
        """
        Restores decrypted session cookies & storage from portable_session.json into the browser context.
        Re-encrypts cookies under the current Windows installation's DPAPI key.
        """
        clean_id = account_id.strip()
        profile_path = PROFILES_DIR / clean_id
        session_file = profile_path / "portable_session.json"

        if not session_file.exists():
            return {"success": False, "error": f"No portable_session.json found for '{clean_id}'"}

        acc = self.accounts.get(clean_id)
        if not acc or not acc.context:
            return {"success": False, "error": f"Account '{clean_id}' context is not running"}

        try:
            raw_text = session_file.read_text(encoding="utf-8")
            data = json.loads(raw_text)
            state = data.get("state", {})
            cookies = state.get("cookies", [])
            if not cookies:
                return {"success": False, "error": "No cookies found in portable_session.json"}

            if count_google_auth_cookies(cookies) < 2:
                # The backup was taken while signed out: restoring it cannot log the account in.
                msg = f"portable_session.json for '{clean_id}' has no Google login cookies (saved while signed out). {NOT_SIGNED_IN_HINT}"
                logger.warning(f"[{clean_id}] {msg}")
                return {"success": False, "error": msg, "needs_login": True}

            valid_cookies = []
            for c in cookies:
                cookie_dict = {
                    "name": str(c.get("name", "")),
                    "value": str(c.get("value", "")),
                    "domain": str(c.get("domain", "")),
                    "path": str(c.get("path", "/")),
                }
                if "expires" in c and c["expires"] is not None:
                    cookie_dict["expires"] = float(c["expires"])
                if "httpOnly" in c:
                    cookie_dict["httpOnly"] = bool(c["httpOnly"])
                if "secure" in c:
                    cookie_dict["secure"] = bool(c["secure"])
                if c.get("sameSite") in ("Strict", "Lax", "None"):
                    cookie_dict["sameSite"] = c["sameSite"]
                valid_cookies.append(cookie_dict)

            await acc.context.add_cookies(valid_cookies)

            # Restore localStorage origins if any
            origins = state.get("origins", [])
            if origins:
                for orig in origins:
                    o_url = orig.get("origin")
                    ls_entries = orig.get("localStorage", [])
                    if o_url and ls_entries and "google.com" in o_url:
                        page = await acc.context.new_page()
                        try:
                            await page.goto(o_url, wait_until="domcontentloaded", timeout=12000)
                            for entry in ls_entries:
                                k = entry.get("name")
                                v = entry.get("value")
                                if k and v is not None:
                                    await page.evaluate("([k, v]) => localStorage.setItem(k, v)", [k, v])
                        except Exception:
                            pass
                        finally:
                            try:
                                await page.close()
                            except Exception:
                                pass

            acc.status = "ready"
            # Invalidate previous warm page if any, so standby tab picks up the restored cookies
            if getattr(acc, "warm_page", None):
                try:
                    await acc.close_warm_page()
                except Exception:
                    pass
            if self.pre_warmed_pool_enabled:
                asyncio.create_task(acc.prepare_warm_page())

            logger.info(f"[{clean_id}] Successfully restored portable session ({len(valid_cookies)} cookies).")
            return {
                "success": True,
                "account_id": clean_id,
                "restored_cookies": len(valid_cookies),
            }
        except Exception as e:
            logger.error(f"[{clean_id}] Failed to restore portable session: {e}")
            return {"success": False, "error": str(e)}

    async def backup_all_portable_sessions(self) -> Dict[str, Any]:
        """Backs up portable sessions for all active accounts."""
        results = {}
        for acc_id in list(self.accounts.keys()):
            results[acc_id] = await self.backup_portable_session(acc_id)
        succeeded = sum(1 for r in results.values() if r.get("success"))
        return {
            "total": len(results),
            "succeeded": succeeded,
            "results": results,
            "message": f"Successfully backed up {succeeded}/{len(results)} account sessions to portable storage."
        }

    async def restore_all_portable_sessions(self) -> Dict[str, Any]:
        """Restores portable sessions for all accounts."""
        results = {}
        for acc_id in list(self.accounts.keys()):
            results[acc_id] = await self.restore_portable_session(acc_id)
        succeeded = sum(1 for r in results.values() if r.get("success"))
        return {
            "total": len(results),
            "succeeded": succeeded,
            "results": results,
            "message": f"Successfully restored {succeeded}/{len(results)} account sessions from portable storage."
        }

    async def import_from_chrome(self, chrome_profile_dir: str, target_account_id: str) -> Dict[str, Any]:
        """
        Lightweight One-Click Chrome Profile Import:
        Only extracts display name, email, avatar image, and inferred Gemini plan from Chrome Local State.
        Does NOT copy large storage databases or run headless browser calibration.
        """
        chrome_root = get_system_chrome_user_data_dir()
        if not chrome_root:
            raise RuntimeError("Google Chrome User Data directory was not found on this system.")

        src_profile = chrome_root / chrome_profile_dir
        if not src_profile.exists():
            raise RuntimeError(f"Chrome profile folder '{chrome_profile_dir}' does not exist.")

        dest_dir = PROFILES_DIR / target_account_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_default = dest_dir / "Default"
        dest_default.mkdir(parents=True, exist_ok=True)

        logger.info(f"Importing metadata from Chrome profile '{chrome_profile_dir}' into '{target_account_id}'...")

        # 1. Copy Google Profile Picture if available
        for pic_name in ["Google Profile Picture.png", "Google Profile Picture"]:
            src_pic = src_profile / pic_name
            if src_pic.exists():
                try:
                    shutil.copy2(src_pic, dest_dir / "Google Profile Picture.png")
                    shutil.copy2(src_pic, dest_default / "Google Profile Picture.png")
                except Exception as e:
                    logger.debug(f"Warning copying avatar: {e}")

        # 2. Extract profile name, email, and inferred plan directly from Chrome Local State
        inferred_plan = get_chrome_profile_inferred_plan(target_account_id, chrome_profile_dir) or "Free"
        
        display_name = ""
        email_addr = ""
        local_state_file = chrome_root / "Local State"
        if local_state_file.exists():
            try:
                with open(local_state_file, "r", encoding="utf-8") as f:
                    ls_data = json.load(f)
                info_cache = ls_data.get("profile", {}).get("info_cache", {})
                p_info = info_cache.get(chrome_profile_dir, {})
                display_name = p_info.get("name", "")
                email_addr = p_info.get("user_name", "")
            except Exception as e:
                logger.warning(f"Error reading Chrome Local State for {chrome_profile_dir}: {e}")

        acc = AccountSession(target_account_id, dest_dir)
        acc.source = f"chrome:{chrome_profile_dir}"
        acc.plan = inferred_plan
        acc.email = email_addr
        acc.display_name = display_name
        acc.status = "ready"
        acc.is_calibrated = True
        acc.save_meta()
        self.accounts[target_account_id] = acc

        logger.info(f"Imported '{target_account_id}' ({email_addr}): Plan={inferred_plan}, Display={display_name}")

        return {
            "status": "imported",
            "account_id": target_account_id,
            "chrome_profile": chrome_profile_dir,
            "email": email_addr,
            "display_name": display_name,
            "plan": inferred_plan,
            "is_authenticated": True,
        }

    async def delete_account(self, account_id: str):
        """Closes context and removes profile directory from disk."""
        if account_id in self.accounts:
            acc = self.accounts[account_id]
            if acc.context:
                try:
                    await acc.context.close()
                except Exception as e:
                    logger.debug(f"Error closing context during delete: {e}")
            del self.accounts[account_id]

        profile_path = PROFILES_DIR / account_id
        if profile_path.exists():
            shutil.rmtree(profile_path, ignore_errors=True)
            logger.info(f"Deleted profile directory for '{account_id}'")

    async def get_next_account(self, model: Optional[str] = None) -> AccountSession:
        """Thread-safe Smart Load Balancer with Strategy Selection and Quota Protection."""
        async with self.rr_lock:
            # 1. TOP PRIORITY: Direct Official Chrome Fast Lane (Port 9222) - only if NOT busy
            official_acc = self.accounts.get("official_chrome")
            if official_acc and getattr(official_acc, "is_cdp", False) and official_acc.status == "ready" and not official_acc.is_busy:
                logger.info(f"[FastLane] Prioritizing Direct Official Chrome CDP ({official_acc.account_id}) for ultra-fast native response!")
                official_acc.is_busy = True
                return official_acc

            ready_accounts = [
                acc for acc in self.accounts.values()
                if acc.status == "ready" and not getattr(acc, "is_paused", False)
                and acc.account_id != "official_chrome"
            ]

            if not ready_accounts:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="No active Gemini accounts available. Please add or import an account via the Web Dashboard at /.",
                )

            # Circuit Breaker: Prioritize accounts that are not in cooldown
            active_circuit = [acc for acc in ready_accounts if not acc.is_cooling_down()]
            if active_circuit:
                ready_accounts = active_circuit
            else:
                # All ready accounts are currently cooling down. Route to the one closest to expiry
                ready_accounts.sort(key=lambda a: a.get_cooldown_remaining())
                logger.warning(
                    f"[CircuitBreaker] All ready accounts are cooling down! Routing to '{ready_accounts[0].account_id}' "
                    f"({ready_accounts[0].get_cooldown_remaining()}s remaining)"
                )

            # If a Pro or Ultra model is requested, prioritize accounts with Pro or Ultra plan
            pro_accounts = [
                acc for acc in ready_accounts
                if getattr(acc, "plan", "Free") in ("Pro", "Ultra")
            ]
            if model:
                model_l = model.lower()
                is_pro_model = any(k in model_l for k in ["pro", "ultra", "advanced"])
                if is_pro_model and pro_accounts:
                    ready_accounts = pro_accounts
                elif pro_accounts and any(not acc.is_busy for acc in pro_accounts):
                    ready_accounts = pro_accounts
            elif pro_accounts and any(not acc.is_busy for acc in pro_accounts):
                ready_accounts = pro_accounts

            # Smart Quota Protection: Filter out accounts exceeding quota threshold (default >= 95%)
            if getattr(self, "quota_guard_enabled", True):
                unexhausted = [
                    acc for acc in ready_accounts
                    if not acc.is_quota_protected(threshold=getattr(self, "quota_threshold", 95))
                ]
                if unexhausted:
                    ready_accounts = unexhausted

            # Prioritize accounts that are currently idle (not busy)
            idle_accounts = [acc for acc in ready_accounts if not acc.is_busy]
            candidates = idle_accounts if idle_accounts else ready_accounts

            # Fast Lane: If Direct Official Chrome CDP is ready and idle, prioritize it for ultra-fast 5-7s response!
            fast_lane_cdp = next(
                (acc for acc in candidates if getattr(acc, "is_cdp", False) and not acc.is_busy and not acc.is_cooling_down()),
                None
            )
            if fast_lane_cdp:
                logger.info(f"[FastLane] Prioritizing Direct Official Chrome CDP ({fast_lane_cdp.account_id}) for 5-7s native response!")
                return fast_lane_cdp

            strategy = getattr(self, "routing_strategy", "balanced")
            if strategy == "lowest_latency":
                # Prioritize accounts with lowest measured average latency
                candidates.sort(key=lambda a: (a.avg_latency if a.avg_latency and a.avg_latency > 0 else 999.0))
                selected = candidates[0]
            elif strategy == "lowest_quota":
                # Prioritize accounts with lowest weekly usage percentage to preserve headroom
                candidates.sort(key=lambda a: (getattr(a, "weekly_usage_pct", 0) or 0))
                selected = candidates[0]
            else:
                # Balanced / round-robin
                self.rr_index = (self.rr_index + 1) % len(candidates)
                selected = candidates[self.rr_index]

            selected.is_busy = True
            return selected

    async def shutdown(self):
        """Gracefully closes all browser contexts and stops Playwright."""
        logger.info("Shutting down Gemini Account Manager...")
        for acc_id, acc in self.accounts.items():
            try:
                await acc.close_warm_page()
            except Exception:
                pass
            if acc.context:
                try:
                    await acc.context.close()
                    logger.info(f"Closed context for '{acc_id}'")
                except Exception as e:
                    logger.warning(f"Error closing context '{acc_id}': {e}")
        if self.playwright:
            try:
                await self.playwright.stop()
                logger.info("Playwright stopped.")
            except Exception as e:
                logger.warning(f"Error stopping Playwright: {e}")

# Global Account Manager Instance
account_manager = AccountManager()

def attach_interactive_desktop():
    """Attaches current thread to interactive user desktop station on Windows so Win32 GUI calls work."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32
        h_winsta = user32.OpenWindowStationW("WinSta0", False, 0x037F)
        if h_winsta:
            user32.SetProcessWindowStation(h_winsta)
        h_desk = user32.OpenDesktopW("Default", 0, False, 0x01FF)
        if h_desk:
            user32.SetThreadDesktop(h_desk)
    except Exception:
        pass

def get_automation_chrome_hwnds() -> List[tuple]:
    """Returns list of (hwnd, title) for all open automation Chrome windows on the user desktop."""
    if sys.platform != "win32":
        return []
    attach_interactive_desktop()
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        hwnds = []

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def enum_cb(hwnd, lparam):
            cls_buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls_buf, 256)
            if cls_buf.value == "Chrome_WidgetWin_1":
                buf = ctypes.create_unicode_buffer(512)
                user32.GetWindowTextW(hwnd, buf, 512)
                title = buf.value
                if "- Google Chrome" in title or "about:blank" in title or "Gemini" in title:
                    if "Antigravity" not in title and "Edge" not in title:
                        rect = wintypes.RECT()
                        user32.GetWindowRect(hwnd, ctypes.byref(rect))
                        w = rect.right - rect.left
                        h = rect.bottom - rect.top
                        if w > 200 and h > 200:
                            hwnds.append((hwnd, title))
            return True

        user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
        return hwnds
    except Exception as e:
        logger.debug(f"Error enumerating Chrome windows: {e}")
        return []

def set_chrome_windows_visibility(visible: bool) -> int:
    """Instantly shows (restores) or hides all automation Chrome windows on desktop."""
    if sys.platform != "win32":
        return 0
    attach_interactive_desktop()
    try:
        import ctypes
        user32 = ctypes.windll.user32
        windows = get_automation_chrome_hwnds()
        count = 0
        cmd = 9 if visible else 0  # 9 = SW_RESTORE, 0 = SW_HIDE
        for hwnd, _ in windows:
            user32.ShowWindow(hwnd, cmd)
            count += 1
        return count
    except Exception as e:
        logger.debug(f"Error setting Chrome window visibility: {e}")
        return 0

def arrange_chrome_windows(layout: str = "tile") -> Dict[str, Any]:
    """Arranges all active Chrome automation windows neatly in side-by-side tiles or grid."""
    if sys.platform != "win32":
        return {"status": "unsupported", "count": 0, "message": "Window arrangement is only supported on Windows."}
    attach_interactive_desktop()
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        windows = get_automation_chrome_hwnds()
        if not windows:
            return {"status": "success", "count": 0, "message": "No active Chrome automation windows found on desktop."}

        rect = wintypes.RECT()
        user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)  # SPI_GETWORKAREA
        screen_w = rect.right - rect.left
        screen_h = rect.bottom - rect.top
        base_x = rect.left
        base_y = rect.top

        n = len(windows)
        SWP_NOZORDER = 0x0004
        SWP_SHOWWINDOW = 0x0040

        if n == 1:
            w = int(screen_w * 0.75)
            h = int(screen_h * 0.85)
            x = base_x + (screen_w - w) // 2
            y = base_y + (screen_h - h) // 2
            hwnd = windows[0][0]
            user32.ShowWindow(hwnd, 9)
            user32.SetWindowPos(hwnd, 0, x, y, w, h, SWP_NOZORDER | SWP_SHOWWINDOW)
        elif n == 2:
            w = screen_w // 2
            h = screen_h
            for i, (hwnd, _) in enumerate(windows):
                user32.ShowWindow(hwnd, 9)
                user32.SetWindowPos(hwnd, 0, base_x + i * w, base_y, w, h, SWP_NOZORDER | SWP_SHOWWINDOW)
        elif n == 3:
            w = screen_w // 2
            h_half = screen_h // 2
            user32.ShowWindow(windows[0][0], 9)
            user32.SetWindowPos(windows[0][0], 0, base_x, base_y, w, screen_h, SWP_NOZORDER | SWP_SHOWWINDOW)
            user32.ShowWindow(windows[1][0], 9)
            user32.SetWindowPos(windows[1][0], 0, base_x + w, base_y, w, h_half, SWP_NOZORDER | SWP_SHOWWINDOW)
            user32.ShowWindow(windows[2][0], 9)
            user32.SetWindowPos(windows[2][0], 0, base_x + w, base_y + h_half, w, h_half, SWP_NOZORDER | SWP_SHOWWINDOW)
        else:
            cols = 2 if n <= 4 else 3
            rows = (n + cols - 1) // cols
            cell_w = screen_w // cols
            cell_h = screen_h // rows
            for i, (hwnd, _) in enumerate(windows):
                c = i % cols
                r = i // cols
                x = base_x + c * cell_w
                y = base_y + r * cell_h
                user32.ShowWindow(hwnd, 9)
                user32.SetWindowPos(hwnd, 0, x, y, cell_w, cell_h, SWP_NOZORDER | SWP_SHOWWINDOW)

        return {"status": "success", "count": n, "message": f"Successfully arranged {n} Chrome window(s) neatly on your screen."}
    except Exception as e:
        logger.warning(f"Error arranging Chrome windows: {e}")
        return {"status": "error", "count": 0, "message": str(e)}

# ---------------------------------------------------------------------------
# Chrome Profile Discovery Helper
# ---------------------------------------------------------------------------
# Chrome Profile Discovery & Inferred Plan Helper
# ---------------------------------------------------------------------------
def get_chrome_profile_inferred_plan(account_id: str, source: str = "") -> Optional[str]:
    """
    Inspects Chrome's system Local State for the account's officially synced
    ai_subscription_tier (0=Free, 1=Pro/Advanced, 2=Ultra) and profile name tags.
    """
    try:
        chrome_root = get_system_chrome_user_data_dir()
        if not chrome_root:
            return None
        local_state_file = chrome_root / "Local State"
        if not local_state_file.exists():
            return None
        with open(local_state_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        info_cache = data.get("profile", {}).get("info_cache", {})

        target_clean = account_id.lower().replace("_", "").replace("-", "")
        for dir_name, info in info_cache.items():
            u = (info.get("user_name") or "").lower()
            n = (info.get("name") or "").lower()
            tier = info.get("ai_subscription_tier", 0)
            e_clean = u.split("@")[0].replace(".", "").lower() if "@" in u else ""
            d_clean = dir_name.lower().replace(" ", "")

            matched = False
            if source and dir_name.lower() in source.lower():
                matched = True
            elif e_clean and e_clean == target_clean:
                matched = True
            elif d_clean and d_clean == target_clean:
                matched = True
            elif u and target_clean in u:
                matched = True

            if matched:
                if tier == 2 or "ultra" in n:
                    return "Ultra"
                elif tier == 1 or "pro" in n or "advanced" in n:
                    return "Pro"
                elif "free" in n:
                    return "Free"
    except Exception as e:
        logger.debug(f"Error checking Chrome inferred plan for {account_id}: {e}")
    return None

def discover_local_chrome_profiles() -> List[Dict[str, Any]]:
    """Discovers all local Google Chrome profiles and accounts from Local State."""
    chrome_root = get_system_chrome_user_data_dir()
    if not chrome_root:
        return []

    local_state_file = chrome_root / "Local State"
    if not local_state_file.exists():
        return []

    results = []
    try:
        with open(local_state_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        info_cache = data.get("profile", {}).get("info_cache", {})
        for dir_name, info in info_cache.items():
            name = info.get("name") or dir_name
            user_name = info.get("user_name") or ""
            gaia_name = info.get("gaia_name") or ""
            avatar_url = info.get("last_downloaded_gaia_picture_url_with_size") or ""
            tier = info.get("ai_subscription_tier", 0)
            
            # Infer plan from Chrome's official subscription tier or profile name
            name_lower = name.lower()
            if tier == 2 or "ultra" in name_lower:
                inferred_plan = "Ultra"
            elif tier == 1 or "pro" in name_lower or "advanced" in name_lower:
                inferred_plan = "Pro"
            else:
                inferred_plan = "Free"

            # Suggest a clean account ID based on email or name
            clean_id = ""
            if user_name and "@" in user_name:
                clean_id = user_name.split("@")[0].replace(".", "_").lower()
            else:
                clean_id = dir_name.lower().replace(" ", "_")

            is_already_imported = clean_id in account_manager.accounts

            results.append({
                "directory": dir_name,
                "name": name,
                "email": user_name,
                "gaia_name": gaia_name,
                "avatar_url": avatar_url,
                "suggested_id": clean_id,
                "is_already_imported": is_already_imported,
                "ai_subscription_tier": tier,
                "inferred_plan": inferred_plan,
            })
    except Exception as e:
        logger.error(f"Error scanning Chrome profiles: {e}")

    return results

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Gemini Web Automation Workflow & Plan Detection
# ---------------------------------------------------------------------------
async def dismiss_gemini_overlays(page: Page, account_id: str = "", press_escape: bool = True) -> bool:
    """Detects and dismisses Gemini Web CDK overlays, onboarding callouts, dialogs, and tooltips."""
    dismissed = False
    try:
        # 1. Click explicit action buttons (e.g. 'Continue', 'Use without content apps', 'Got it', 'Dismiss', 'I agree', 'Close', 'OK', etc.)
        btn_dismissed = await page.evaluate("""() => {
            let clicked = false;
            
            // First check explicit dialog containers (e.g. "Personal Intelligence", "Extensions", "Welcome")
            const dialogButtons = Array.from(document.querySelectorAll(
                "mat-dialog-container button, [role='dialog'] button, .cdk-overlay-pane button, " +
                ".cdk-overlay-container button, [class*='dialog'] button, [class*='modal'] button, " +
                "[data-test-id*='dismiss'], [data-test-id*='close'], button[aria-label*='Close' i], " +
                "button[aria-label*='Dismiss' i], button[aria-label*='Agree' i]"
            ));
            
            const dismissKeywords = [
                'continue', 'use without content apps', 'use without', 'got it', 'dismiss', 
                'close', 'agree', 'i agree', 'đồng ý', 'đóng', 'hiểu', 'ok', 'not now', 
                'skip', 'next', 'done', 'maybe later', 'remind me later', 'no thanks'
            ];

            for (const b of dialogButtons) {
                // Ensure we do not click chat submit or input buttons
                if (b.closest("rich-textarea, .send-button-container, .input-area, form")) continue;
                const txt = (b.innerText || b.textContent || b.getAttribute('aria-label') || '').toLowerCase().trim();
                
                if (
                    dismissKeywords.some(kw => txt.includes(kw)) ||
                    txt === '✕' ||
                    txt === 'x'
                ) {
                    try {
                        b.click();
                        clicked = true;
                    } catch(e) {}
                }
            }
            return clicked;
        }""")
        if btn_dismissed:
            dismissed = True
            logger.info(f"[{account_id}] Clicked dismiss/continue/got-it button in Gemini overlay.")
            await asyncio.sleep(0.4)

        # 2. Press Escape key to close transient tooltips, popovers, or Angular CDK overlays
        if press_escape:
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass

        # 3. Clean up educational callouts/tooltips intercepting clicks (e.g. "To get more relevant responses...")
        overlay_cleaned = await page.evaluate("""() => {
            let removedCount = 0;
            const panes = document.querySelectorAll('.cdk-overlay-container .cdk-overlay-pane, .cdk-overlay-container > div');
            panes.forEach(pane => {
                const text = (pane.innerText || pane.textContent || '').toLowerCase();
                const isPromoOrTip = text.includes('relevant responses') || 
                                     text.includes('connectors') || 
                                     text.includes('skills') || 
                                     text.includes('try asking') || 
                                     text.includes('personal intelligence') ||
                                     text.includes('tip:');
                if (isPromoOrTip) {
                    const btn = pane.querySelector('button');
                    if (btn) {
                        try { btn.click(); removedCount++; } catch(e) {}
                    } else {
                        pane.style.pointerEvents = 'none';
                        pane.style.display = 'none';
                        removedCount++;
                    }
                }
            });

            // Neutralize non-dialog CDK backdrops
            const backdrops = document.querySelectorAll('.cdk-overlay-backdrop');
            backdrops.forEach(bd => {
                if (!document.querySelector('mat-dialog-container, [role="dialog"]')) {
                    bd.style.pointerEvents = 'none';
                }
            });
            return removedCount;
        }""")
        if overlay_cleaned > 0:
            dismissed = True
            logger.info(f"[{account_id}] Dismissed/neutralized {overlay_cleaned} educational overlay callout(s).")

    except Exception as err:
        logger.debug(f"[{account_id}] Overlay dismissal check: {err}")

    return dismissed


async def extract_dom_plan_tier(page: Page, interactive: bool = True) -> Dict[str, Any]:
    """Inspects Gemini Web DOM to automatically determine if the account is signed in and whether it is Free, Pro, or Ultra plan."""
    # In interactive mode, dismiss any blocking dialogs, expand sidebar to reveal user profile card badge, and expand model switcher
    if interactive:
        try:
            await dismiss_gemini_overlays(page)
            await asyncio.sleep(0.4)

            # 1. Expand sidebar if collapsed so the user profile card (Avatar + Name + 'Pro' / 'Ultra') is mounted
            await page.evaluate("""() => {
                const sidebarBtn = document.querySelector(
                    "button[aria-label*='Open sidebar' i], button[aria-label*='Main menu' i], " +
                    "button[aria-label*='Expand menu' i], button[aria-label*='Expand' i]"
                );
                if (sidebarBtn) {
                    sidebarBtn.click();
                }
            }""")
            await asyncio.sleep(0.6)

            # 2. Expand mode switcher to discover available models in dropdown
            await page.evaluate("""() => {
                const modeBtn = document.querySelector(
                    "bard-mode-switcher button, button[aria-label*='mode picker' i], button[aria-label*='Open mode picker' i], " +
                    "bard-mode-switcher [role='button'], bard-mode-switcher, [data-test-id*='mode-switcher']"
                );
                if (modeBtn) {
                    modeBtn.click();
                } else {
                    const btns = Array.from(document.querySelectorAll("button, [role='button']"));
                    for (const b of btns) {
                        if (b.closest("mat-list-item, [class*='conversation'], [class*='chat-history']")) continue;
                        const txt = (b.innerText || b.getAttribute('aria-label') || '').toLowerCase().trim();
                        if ((txt.startsWith('flash') || txt.startsWith('pro') || txt.startsWith('advanced') || txt.startsWith('argon') || txt.startsWith('hydrogen') || txt === 'gemini' || txt.includes('model')) && 
                            (b.querySelector("mat-icon, svg, [class*='arrow'], [class*='chevron'], [class*='dropdown']") || txt.includes('mode picker'))) {
                            b.click();
                            break;
                        }
                    }
                }
            }""")
            await asyncio.sleep(0.8)
        except Exception as click_err:
            logger.debug(f"Model switcher / sidebar expand attempt: {click_err}")

    # Now evaluate the comprehensive DOM state
    dom_data = await page.evaluate("""() => {
        const text = document.body ? document.body.innerText : '';
        const lowerText = text.toLowerCase();
        
        // 0. Check authentication state (Are we logged into a Google Account?)
        const allButtons = Array.from(document.querySelectorAll("a, button, [role='button']"));
        const buttonTexts = allButtons.map(el => (el.innerText || el.getAttribute('aria-label') || '').trim()).filter(Boolean);
        
        const hasSignInButton = allButtons.some(el => {
            const t = (el.innerText || el.getAttribute('aria-label') || '').toLowerCase().trim();
            const href = el.getAttribute('href') || '';
            return t === 'sign in' || t === 'đăng nhập' || href.includes('ServiceLogin');
        });
        const hasSignInText = lowerText.includes('sign in to save activity') || lowerText.includes('meet gemini, your personal ai assistant');
        const hasAvatar = !!document.querySelector("a[href*='SignOutOptions'], a[aria-label*='Google Account' i], img[alt*='Google Account' i], button[aria-label*='Google Account' i], [aria-label*='Google គណនី' i]");

        const isSignedIn = hasAvatar || (!hasSignInButton && !hasSignInText);

        // 1. Check for User Profile Card Plan Badge (e.g. 'Pro' or 'Ultra' directly under user name in sidebar)
        let sidebarBadgeTier = null;
        
        // Specifically search profile / user containers in sidebar first (matches user screenshot: Avatar + Name + Pro/Ultra)
        const profileContainers = Array.from(document.querySelectorAll(
            "aside, nav, [class*='side'], [class*='user'], [class*='profile'], [class*='account-details'], " +
            "[data-test-id*='profile'], [data-test-id*='user'], mat-sidenav, bard-sidenav"
        ));
        
        for (const container of profileContainers) {
            const leaves = Array.from(container.querySelectorAll('*')).filter(el => el.children.length === 0);
            for (const el of leaves) {
                const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                if (t === 'ultra') {
                    sidebarBadgeTier = 'Ultra';
                    break;
                } else if (t === 'pro' || t === 'advanced') {
                    sidebarBadgeTier = 'Pro';
                    break;
                }
            }
            if (sidebarBadgeTier) break;
        }

        // Global fallback for leaf elements matching Pro or Ultra
        if (!sidebarBadgeTier) {
            const allLeaves = Array.from(document.querySelectorAll("*")).filter(el => {
                if (el.children.length > 0) return false;
                const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                return t === 'pro' || t === 'ultra' || t === 'advanced';
            });
            for (const el of allLeaves) {
                const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                if (t === 'ultra') {
                    sidebarBadgeTier = 'Ultra';
                    break;
                } else if (t === 'pro' || t === 'advanced') {
                    sidebarBadgeTier = 'Pro';
                    break;
                }
            }
        }

        // 2. Check Google Account button membership status (e.g. "Google membership" or "Google One")
        const hasMembershipBadge = allButtons.some(el => {
            const aria = (el.getAttribute('aria-label') || el.innerText || '').toLowerCase();
            return aria.includes('google membership') || aria.includes('google one');
        });

        // 3. Check for Upgrade buttons / links anywhere on the page
        const upgradeLabels = ['upgrade', 'try gemini advanced', 'upgrade to gemini advanced', 'nâng cấp', 'try advanced', 'get advanced', 'explore gemini advanced'];
        let upgradeButtonFound = false;
        let upgradeSnippet = '';
        for (const label of buttonTexts) {
            const l = label.toLowerCase();
            if (upgradeLabels.some(u => l === u || l.includes(u))) {
                upgradeButtonFound = true;
                upgradeSnippet = label;
                break;
            }
        }

        const upgradeLinks = Array.from(document.querySelectorAll("a[href*='one.google.com'], a[href*='subscriptions'], [data-test-id*='upgrade']"));
        if (upgradeLinks.length > 0) {
            upgradeButtonFound = true;
            if (!upgradeSnippet) upgradeSnippet = upgradeLinks[0].innerText || upgradeLinks[0].getAttribute('href') || 'upgrade link';
        }

        // 4. Check Header & Logo
        const headerEls = Array.from(document.querySelectorAll("header, [role='banner'], .header, bard-mode-switcher, [data-test-id*='logo'], h1, [aria-label*='Advanced' i]"));
        const headerText = headerEls.map(el => el.innerText || el.getAttribute('aria-label') || '').join(' ').toLowerCase();
        
        const hasAdvancedInHeader = headerText.includes('advanced');
        const hasAdvancedLogo = !!document.querySelector("img[alt*='Advanced' i], svg[aria-label*='Advanced' i], [aria-label*='Gemini Advanced' i]");
        
        // 5. Check opened menu / modal / dropdown items (including gem-menu-item and bard-mode-option)
        const menuItems = Array.from(document.querySelectorAll(
            "gem-menu-item, [data-test-id*='bard-mode-option'], [data-mode-id], [role='menuitem'], [role='option'], mat-option, .mat-mdc-menu-item, [role='menu'] button, [role='menu'] div"
        ));
        const menuTexts = menuItems.map(el => (el.innerText || el.textContent || el.getAttribute('aria-label') || '').trim()).filter(Boolean);
        const menuCombined = menuTexts.join(' | ').toLowerCase();

        // 6. Ultra indicators (Ultra badge, Argon flagship model, or Ultra header)
        const hasUltraInHeader = headerText.includes('ultra');
        const hasUltraText = /gemini ultra/i.test(lowerText) || /gemini ultra/i.test(menuCombined);
        const hasArgonModel = headerText.includes('argon') || menuCombined.includes('4 argon') || menuCombined.includes('argon');

        // 7. Advanced / Pro indicators
        const hasProInMenu = menuCombined.includes('3.1 pro') || menuCombined.includes('1.5 pro') || menuCombined.includes('2.5 pro') || menuCombined.includes('advanced reasoning') || menuCombined.includes('pro ') || menuCombined.includes('pro\\n');
        const hasAdvancedInMenu = menuCombined.includes('advanced') || hasProInMenu;
        const isAdvancedSubscribed = (hasAdvancedInHeader || hasAdvancedLogo || (hasAdvancedInMenu && !upgradeButtonFound)) && !upgradeButtonFound;

        let detectedPlan = "Free";
        if (sidebarBadgeTier === "Ultra" || hasUltraInHeader || hasUltraText) {
            detectedPlan = "Ultra";
        } else if (sidebarBadgeTier === "Pro" || sidebarBadgeTier === "Advanced") {
            detectedPlan = "Pro";
        } else if (hasArgonModel && !upgradeButtonFound) {
            detectedPlan = "Pro";
        } else if (hasProInMenu && !upgradeButtonFound) {
            detectedPlan = "Pro";
        } else if (isAdvancedSubscribed || (hasMembershipBadge && hasAdvancedInMenu) || (hasAdvancedInMenu && !upgradeButtonFound)) {
            detectedPlan = "Pro";
        } else if (hasMembershipBadge && !upgradeButtonFound) {
            detectedPlan = "Pro";
        } else if (lowerText.includes("gemini advanced") && !upgradeButtonFound) {
            detectedPlan = "Pro";
        } else {
            detectedPlan = "Free";
        }

        return {
            isSignedIn,
            plan: detectedPlan,
            sidebarBadgeTier,
            hasMembershipBadge,
            hasSignInButton,
            hasAvatar,
            upgradeButtonFound,
            upgradeSnippet,
            hasAdvancedInHeader,
            hasAdvancedLogo,
            hasUltraInHeader,
            hasUltraText,
            hasArgonModel,
            hasProInMenu,
            headerSnippet: headerText.slice(0, 150),
            menuSnippet: menuCombined.slice(0, 200),
            buttonCount: buttonTexts.length,
            sampleButtons: buttonTexts.slice(0, 10),
        };
    }""")

    # Close any open menu by pressing Escape
    try:
        await page.keyboard.press("Escape")
    except Exception:
        pass

    if interactive:
        await dismiss_gemini_overlays(page)

    return dom_data


async def detect_account_gemini_plan(account: AccountSession) -> Dict[str, Any]:
    """Navigates to Gemini Web using the account's own isolated profile and inspects the live page DOM + Chrome Local State to auto-detect Free, Pro, or Ultra plan."""
    # If the account is explicitly marked as CDP, inspect over CDP
    if getattr(account, "is_cdp", False):
        try:
            logger.info(f"[{account.account_id}] Inspecting plan over CDP on {account.cdp_url}...")
            browser = await account_manager.playwright.chromium.connect_over_cdp(account.cdp_url)
            ctx = browser.contexts[0]
            gemini_pages = [p for p in ctx.pages if "gemini.google.com" in p.url]
            page = gemini_pages[0] if gemini_pages else await ctx.new_page()
            if not gemini_pages:
                await page.goto(GEMINI_URL, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(1.0)
            await dismiss_gemini_overlays(page, account.account_id)
            info = await extract_dom_plan_tier(page, interactive=False)
            detected = info.get("plan", "Free")
            chrome_inferred = get_chrome_profile_inferred_plan(account.account_id, getattr(account, "source", ""))
            final_plan = detected if detected in ("Pro", "Ultra") else (chrome_inferred if chrome_inferred in ("Pro", "Ultra") else detected)
            account.plan = final_plan
            account.status = "ready" if info.get("isSignedIn", False) else "needs_login"
            account.save_meta()
            return {"account_id": account.account_id, "plan": account.plan, "is_signed_in": info.get("isSignedIn", False), "status": "success", "indicators": info}
        except Exception as e:
            logger.warning(f"[{account.account_id}] CDP plan check failed: {e}")
            return {"account_id": account.account_id, "plan": getattr(account, "plan", "Free"), "status": "error", "error": str(e)}

    # Standard account: inspect its own profile directly
    async with account.lock:
        account.is_busy = True
        page: Optional[Page] = None
        try:
            if not account.context:
                account.context = await account_manager.launch_context(account.profile_path, headless=True)

            try:
                page = await account.context.new_page()
            except Exception as ctx_err:
                logger.warning(f"[{account.account_id}] Context closed or invalid ({ctx_err}). Auto-healing...")
                account.context = await account_manager.launch_context(account.profile_path, headless=True)
                page = await account.context.new_page()

            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            """)

            logger.info(f"[{account.account_id}] Auto-detecting Gemini Plan from live UI...")
            await page.goto(GEMINI_URL, wait_until="domcontentloaded", timeout=45000)

            if "accounts.google.com" in page.url:
                account.status = "needs_login"
                return {
                    "account_id": account.account_id,
                    "plan": account.plan,
                    "is_signed_in": False,
                    "status": "needs_login",
                    "error": "Session expired or redirected to Google Login."
                }

            # 1. Dismiss any blocking dialogs, onboarding callouts, or "Personal Intelligence" modals
            await dismiss_gemini_overlays(page, account.account_id)
            await asyncio.sleep(1.0)

            # Wait for main UI elements to render
            try:
                await page.wait_for_selector(
                    "rich-textarea, div[contenteditable='true'], header, [role='banner'], main, bard-mode-switcher",
                    timeout=20000,
                    state="visible"
                )
            except Exception:
                pass

            # 2. Dismiss again in case late modal arrived
            await dismiss_gemini_overlays(page, account.account_id)
            await asyncio.sleep(0.8)

            # Capture live debug screenshot for verification
            try:
                await page.screenshot(path=f"debug_plan_{account.account_id}.png")
            except Exception as ss_err:
                logger.debug(f"Screenshot error: {ss_err}")

            # 3. Extract DOM plan tier (clicks mode picker to inspect model options)
            info = await extract_dom_plan_tier(page, interactive=True)
            is_signed_in = info.get("isSignedIn", False)

            if not is_signed_in:
                session_backup = PROFILES_DIR / account.account_id / "portable_session.json"
                if session_backup.exists() and not getattr(account, "_auto_restore_attempted", False):
                    account._auto_restore_attempted = True
                    logger.info(f"[{account.account_id}] Guest mode detected, but portable_session backup exists. Auto-recovering session...")
                    await account_manager.restore_portable_session(account.account_id)
                    await page.goto(GEMINI_URL, wait_until="domcontentloaded", timeout=20000)
                    await asyncio.sleep(1.5)
                    info = await extract_dom_plan_tier(page, interactive=False)
                    is_signed_in = info.get("isSignedIn", False)

            if not is_signed_in:
                account.status = "needs_login"
                logger.warning(f"[{account.account_id}] Account is NOT signed in to Google on Gemini Web (showing guest mode).")
                return {
                    "account_id": account.account_id,
                    "plan": account.plan,
                    "is_signed_in": False,
                    "status": "needs_login",
                    "message": "Account is not signed in on Gemini Web (showing guest preview). Click 'Re-login' in the dashboard to sign into your Google account.",
                    "indicators": info,
                }

            detected = info.get("plan", "Free")
            
            # 4. Cross-check with Chrome Local State official subscription tier
            chrome_inferred = get_chrome_profile_inferred_plan(account.account_id, getattr(account, "source", ""))
            
            # Resolve plan:
            # - If DOM explicitly confirms Pro or Ultra, use it
            # - If DOM returned Free (due to transient modal or render delay), but Chrome Local State certified Pro or Ultra, adopt Chrome tier
            # - Otherwise use detected
            if detected in ("Pro", "Ultra"):
                final_plan = detected
            elif chrome_inferred in ("Pro", "Ultra"):
                logger.info(f"[{account.account_id}] DOM returned '{detected}', but Chrome Local State confirmed '{chrome_inferred}'. Promoting to '{chrome_inferred}'.")
                final_plan = chrome_inferred
            else:
                final_plan = detected

            account.plan = final_plan
            account.save_meta()
            account.status = "ready"
            logger.info(f"[{account.account_id}] Plan successfully detected & saved: {account.plan} (DOM={detected}, Chrome={chrome_inferred})")
            
            return {
                "account_id": account.account_id,
                "plan": account.plan,
                "is_signed_in": True,
                "status": "success",
                "indicators": info,
                "chrome_inferred": chrome_inferred
            }
        except Exception as e:
            logger.error(f"[{account.account_id}] Error detecting plan: {e}")
            return {
                "account_id": account.account_id,
                "plan": account.plan,
                "status": "error",
                "error": str(e)
            }
        finally:
            account.is_busy = False
            if page:
                try:
                    await page.close()
                except Exception:
                    pass

async def calibrate_and_warmup_account(account: AccountSession) -> Dict[str, Any]:
    """
    Calibrates and warms up a new or existing account to prevent initial freezing:
    1. Launches a page in the account's persistent browser context.
    2. Dismisses all Google Gemini onboarding overlays, dialogs, and 'Agree' modals.
    3. Auto-switches the UI model to '3.5 Flash-Lite' (which avoids the 40-180s Thinking mode delay).
    4. Dismisses any leftover menus/overlays.
    5. Verifies prompt input area readiness.
    6. Marks account as calibrated in metadata so subsequent requests execute with 0 initial delay.
    """
    if getattr(account, "is_cdp", False) or account.account_id == "official_chrome":
        account.is_calibrated = True
        return {
            "account_id": account.account_id,
            "status": "calibrated",
            "is_calibrated": True,
            "input_ready": True
        }

    async with account.lock:
        account.is_busy = True
        page: Optional[Page] = None
        try:
            if not account.context:
                account.context = await account_manager.launch_context(account.profile_path, headless=True)

            try:
                page = await account.context.new_page()
            except Exception as ctx_err:
                logger.warning(f"[{account.account_id}] Context closed or invalid ({ctx_err}). Auto-healing...")
                account.context = await account_manager.launch_context(account.profile_path, headless=True)
                page = await account.context.new_page()

            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            """)

            logger.info(f"[{account.account_id}] Auto-calibrating account to prevent initial freezing...")
            await page.goto(GEMINI_URL, wait_until="domcontentloaded", timeout=45000)

            # Check if redirected to login
            if "accounts.google.com" in page.url:
                account.status = "needs_login"
                return {
                    "account_id": account.account_id,
                    "status": "needs_login",
                    "error": "Session expired or redirected to Google Login."
                }

            # 1. Dismiss any initial dialogs / welcome overlays
            await dismiss_gemini_overlays(page, account.account_id)

            # Check and auto-dismiss reminder modal (e.g. "Agree" button)
            agree_btn = page.locator("button:has-text('Agree'), button:has-text('I agree'), button:has-text('Đồng ý'), button[aria-label*='Agree' i]")
            if await agree_btn.count() > 0 and await agree_btn.first.is_visible():
                try:
                    await agree_btn.first.click(force=True, timeout=2000)
                    logger.info(f"[{account.account_id}] Dismissed initial agreement modal.")
                except Exception:
                    pass

            # 2. Switch model to preferred fast model (Flash-Lite) so Google saves it in cookies
            preferred = getattr(account_manager, "preferred_fast_model", "gemini-3.5-flash-lite")
            await switch_gemini_model_ui(page, preferred, account.account_id)

            # 3. Dismiss any overlays that opened during model switch
            await dismiss_gemini_overlays(page, account.account_id)

            # 4. Verify input area
            has_input = False
            for sel in ["rich-textarea div[contenteditable='true']", "div.ql-editor[contenteditable='true']", "rich-textarea", "div[contenteditable='true']", "textarea"]:
                if await page.locator(sel).count() > 0:
                    has_input = True
                    break

            account.status = "ready"
            account.is_calibrated = True
            account.save_meta()
            logger.info(f"[{account.account_id}] Account successfully calibrated & tuned to 3.5 Flash-Lite! (Input ready: {has_input})")
            return {
                "account_id": account.account_id,
                "status": "calibrated",
                "is_calibrated": True,
                "input_ready": has_input
            }
        except Exception as e:
            logger.warning(f"[{account.account_id}] Error during calibration: {e}")
            return {
                "account_id": account.account_id,
                "status": "warning",
                "error": str(e)
            }
        finally:
            account.is_busy = False
            if page:
                try:
                    await page.close()
                except Exception:
                    pass

async def fetch_account_usage(account: AccountSession) -> Dict[str, Any]:
    """Navigates to https://gemini.google.com/usage using the account's own context and extracts Current usage and Weekly limit."""
    # If the account is explicitly marked as CDP, inspect over CDP
    if getattr(account, "is_cdp", False):
        try:
            browser = await account_manager.playwright.chromium.connect_over_cdp(account.cdp_url)
            ctx = browser.contexts[0]
            usage_pages = [p for p in ctx.pages if "gemini.google.com/usage" in p.url]
            page = usage_pages[0] if usage_pages else await ctx.new_page()
            if not usage_pages:
                await page.goto("https://gemini.google.com/usage", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2.0)
            usage_info = await page.evaluate("""() => {
                const body = document.body.innerText;
                let currentPct = null, currentReset = null, weeklyPct = null, weeklyReset = null;
                const lines = body.split('\\n').map(l => l.trim()).filter(Boolean);
                for (let i = 0; i < lines.length; i++) {
                    const line = lines[i];
                    if (line.includes('Current usage')) {
                        for (let j = i; j < Math.min(lines.length, i + 6); j++) {
                            const m = lines[j].match(/(\\d+)\\s*%\\s*used/i);
                            if (m && currentPct === null) currentPct = parseInt(m[1], 10);
                            if (lines[j].toLowerCase().includes('resets at') && !currentReset) currentReset = lines[j];
                        }
                    }
                    if (line.includes('Weekly limit')) {
                        for (let j = i; j < Math.min(lines.length, i + 6); j++) {
                            const m = lines[j].match(/(\\d+)\\s*%\\s*used/i);
                            if (m && weeklyPct === null) weeklyPct = parseInt(m[1], 10);
                            if (lines[j].toLowerCase().includes('resets ') && !weeklyReset) weeklyReset = lines[j];
                        }
                    }
                }
                return { currentPct, currentReset, weeklyPct, weeklyReset };
            }""")
            if not usage_pages:
                try:
                    await page.close()
                except Exception:
                    pass
            if usage_info.get("currentPct") is not None:
                account.current_usage_pct = usage_info["currentPct"]
            if usage_info.get("currentReset"):
                account.current_usage_reset = usage_info["currentReset"]
            if usage_info.get("weeklyPct") is not None:
                account.weekly_usage_pct = usage_info["weeklyPct"]
            if usage_info.get("weeklyReset"):
                account.weekly_usage_reset = usage_info["weeklyReset"]
            account.usage_updated_at = time.time()
            account.save_meta()
            return {"status": "success", "account_id": account.account_id, "plan": account.plan, "current_usage_pct": account.current_usage_pct, "weekly_usage_pct": account.weekly_usage_pct}
        except Exception as e:
            logger.warning(f"[{account.account_id}] CDP usage fetch failed: {e}")
            return {"status": "error", "account_id": account.account_id, "error": str(e)}

    # Standard account: inspect its own profile directly
    async with account.lock:
        account.is_busy = True
        page: Optional[Page] = None
        try:
            if not account.context:
                account.context = await account_manager.launch_context(account.profile_path, headless=True)

            try:
                page = await account.context.new_page()
            except Exception as ctx_err:
                logger.warning(f"[{account.account_id}] Context closed or invalid ({ctx_err}). Auto-healing...")
                account.context = await account_manager.launch_context(account.profile_path, headless=True)
                page = await account.context.new_page()

            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            """)

            logger.info(f"[{account.account_id}] Navigating to https://gemini.google.com/usage ...")
            await page.goto("https://gemini.google.com/usage", wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(2.5)

            if "accounts.google.com" in page.url:
                account.status = "needs_login"
                account.save_meta()
                return {
                    "status": "error",
                    "account_id": account.account_id,
                    "error": "Account needs login",
                    "plan": account.plan,
                    "current_usage_pct": account.current_usage_pct,
                    "weekly_usage_pct": account.weekly_usage_pct
                }

            usage_info = await page.evaluate("""() => {
                const body = document.body.innerText;
                
                let currentPct = null;
                let currentReset = null;
                let weeklyPct = null;
                let weeklyReset = null;

                const lines = body.split('\\n').map(l => l.trim()).filter(Boolean);
                
                for (let i = 0; i < lines.length; i++) {
                    const line = lines[i];
                    if (line.includes('Current usage')) {
                        for (let j = i; j < Math.min(lines.length, i + 6); j++) {
                            const m = lines[j].match(/(\\d+)\\s*%\\s*used/i);
                            if (m && currentPct === null) currentPct = parseInt(m[1], 10);
                            if (lines[j].toLowerCase().includes('resets at') && !currentReset) {
                                currentReset = lines[j];
                            }
                        }
                    }
                    if (line.includes('Weekly limit')) {
                        for (let j = i; j < Math.min(lines.length, i + 6); j++) {
                            const m = lines[j].match(/(\\d+)\\s*%\\s*used/i);
                            if (m && weeklyPct === null) weeklyPct = parseInt(m[1], 10);
                            if (lines[j].toLowerCase().includes('resets') && !weeklyReset && !lines[j].includes('Weekly limit')) {
                                weeklyReset = lines[j];
                            }
                        }
                    }
                }

                return {
                    currentUsagePct: currentPct !== null ? currentPct : 0,
                    currentReset: currentReset || '',
                    weeklyUsagePct: weeklyPct !== null ? weeklyPct : 0,
                    weeklyReset: weeklyReset || ''
                };
            }""")

            account.current_usage_pct = usage_info.get("currentUsagePct", 0)
            account.current_usage_reset = usage_info.get("currentReset", "")
            account.weekly_usage_pct = usage_info.get("weeklyUsagePct", 0)
            account.weekly_usage_reset = usage_info.get("weeklyReset", "")
            account.usage_updated_at = time.time()
            account.save_meta()

            logger.info(
                f"[{account.account_id}] Usage updated: Plan={account.plan}, "
                f"Current={account.current_usage_pct}% ({account.current_usage_reset}), "
                f"Weekly={account.weekly_usage_pct}% ({account.weekly_usage_reset})"
            )

            return {
                "status": "success",
                "account_id": account.account_id,
                "plan": account.plan,
                "current_usage_pct": account.current_usage_pct,
                "current_usage_reset": account.current_usage_reset,
                "weekly_usage_pct": account.weekly_usage_pct,
                "weekly_usage_reset": account.weekly_usage_reset,
                "usage_updated_at": account.usage_updated_at
            }
        except Exception as e:
            logger.error(f"[{account.account_id}] Failed to fetch usage from Gemini: {e}")
            return {"status": "error", "error": str(e), "account_id": account.account_id}
        finally:
            account.is_busy = False
            if page:
                try:
                    await page.close()
                except Exception:
                    pass

def ensure_mp3_audio(file_path: str) -> tuple[str, bool]:
    """
    Ensures the media file is an MP3 audio file. If given an MP4 or other video file,
    automatically extracts the clean audio track to a temporary .mp3 file using ffmpeg.
    Returns (target_audio_path, is_temporary).
    """
    if not file_path or not os.path.exists(file_path):
        return file_path, False

    ext = Path(file_path).suffix.lower()
    video_exts = [".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv", ".ts", ".m4v"]
    if ext in video_exts:
        try:
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
            tmp.close()
            cmd = ["ffmpeg", "-y", "-i", file_path, "-vn", "-c:a", "libmp3lame", "-b:a", "128k", tmp.name]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if res.returncode == 0 and os.path.getsize(tmp.name) > 0:
                logger.info(f"Auto-extracted audio from video '{Path(file_path).name}' -> MP3 '{Path(tmp.name).name}'")
                return tmp.name, True
            else:
                logger.warning(f"ffmpeg conversion failed: {res.stderr.decode('utf-8', errors='ignore')[:300]}")
        except Exception as e:
            logger.warning(f"Error during video-to-mp3 extraction: {e}")
    return file_path, False

async def switch_gemini_model_ui(page: Page, model_id: str, account_id: str = "") -> bool:
    """Switches the model and/or thinking level in the Gemini Web UI reliably with DOM event fallback and verification."""
    if not model_id or model_id == "gemini-web":
        return True

    model_lower = model_id.lower()
    target_key = None
    target_model_name = None
    target_thinking = None

    if "hydrogen" in model_lower:
        # Must be tested before the generic "4" / Argon branch below
        target_key = "hydrogen"
        target_model_name = "4 Hydrogen"
        keywords = ["hydrogen"]
        exclude_keywords = []
    elif "3.5" in model_lower or "flash-lite" in model_lower or "lite" in model_lower:
        target_key = "lite"
        target_model_name = "3.5 Flash-Lite"
        keywords = ["flash-lite", "lite", "fastest"]
        exclude_keywords = []
    elif "3.1" in model_lower or "pro" in model_lower:
        target_key = "pro"
        target_model_name = "3.1 Pro"
        keywords = ["pro", "advanced reasoning"]
        exclude_keywords = []
    elif "4" in model_lower or "argon" in model_lower:
        target_key = "argon"
        target_model_name = "4 Argon"
        keywords = ["argon", "complex"]
        exclude_keywords = []
    elif "3.8" in model_lower or "flash" in model_lower:
        # Gemini Web's picker no longer lists "3.8 Flash" (entries are now 3.5 Flash-Lite, 4 Hydrogen,
        # 3.1 Pro, 4 Argon, 4 Carbon). Its "everyday" slot is 4 Hydrogen, so 3.8 Flash requests select
        # Hydrogen instead of silently staying on Flash-Lite.
        target_key = "hydrogen"
        target_model_name = "4 Hydrogen"
        keywords = ["hydrogen"]
        exclude_keywords = []
    else:
        target_key = "lite"
        target_model_name = "3.5 Flash-Lite"
        keywords = ["flash-lite", "lite", "fastest"]
        exclude_keywords = []

    if "-low" in model_lower:
        target_thinking = "Low"
    else:
        # Standard fast execution: Thinking Medium and High have been completely removed
        # to ensure instant responses without deep reasoning stalls.
        target_thinking = None

    def matches_active(t: str, a: str) -> bool:
        t_low = t.lower()
        a_low = a.lower()
        if target_key == "lite":
            return "lite" in t_low or "lite" in a_low
        elif target_key == "flash":
            return "flash" in t_low and "lite" not in t_low and "lite" not in a_low
        elif target_key == "pro":
            return "pro" in t_low or "pro" in a_low
        elif target_key == "argon":
            return "argon" in t_low or "argon" in a_low
        elif target_key == "hydrogen":
            return "hydrogen" in t_low or "hydrogen" in a_low
        return False

    acc = account_manager.accounts.get(account_id) if account_id else None
    current_acc_thinking = getattr(acc, "active_thinking_level", None) if acc else None

    try:
        mode_btn = page.locator("button[data-test-id='bard-mode-menu-button'], bard-mode-switcher button, button[aria-label*='mode picker' i], button.input-area-switch, button[data-test-id*='mode-picker']").first
        if await mode_btn.count() == 0:
            mode_btn = page.locator("button:has-text('Flash'), button:has-text('Pro'), button:has-text('Argon'), button:has-text('Hydrogen'), button:has-text('Advanced')").first
        
        try:
            await mode_btn.wait_for(state="visible", timeout=3000)
        except Exception:
            pass

        if await mode_btn.count() == 0 or not await mode_btn.is_visible():
            await dismiss_gemini_overlays(page, account_id)
            await asyncio.sleep(0.5)
            mode_btn = page.locator("button[data-test-id='bard-mode-menu-button'], bard-mode-switcher button, button[aria-label*='mode picker' i], button.input-area-switch, button[data-test-id*='mode-picker'], button:has-text('Flash'), button:has-text('Pro'), button:has-text('Argon'), button:has-text('Hydrogen')").first
            try:
                await mode_btn.wait_for(state="visible", timeout=2000)
            except Exception:
                pass
            if await mode_btn.count() == 0 or not await mode_btn.is_visible():
                logger.warning(f"[{account_id}] Could not locate Gemini Web model switcher button.")
                return False

        btn_text = (await mode_btn.inner_text()).strip()
        btn_aria = (await mode_btn.get_attribute("aria-label") or "").strip()

        # Check if already active and thinking level matches
        if matches_active(btn_text, btn_aria):
            if not target_thinking or target_key == "lite" or current_acc_thinking == target_thinking:
                return True

        logger.info(f"[{account_id}] Switching Gemini Web model from '{btn_text}' to '{target_model_name}' (thinking: {target_thinking})...")

        # Perform model switch with retry
        menu_entries: List[str] = []
        for attempt in range(2):
            is_expanded = (await mode_btn.get_attribute("aria-expanded") or "").lower() == "true"
            if not is_expanded:
                try:
                    await mode_btn.click(timeout=2500)
                except Exception:
                    await page.evaluate("(btn) => btn.click()", await mode_btn.element_handle())
                await asyncio.sleep(0.1)

            # Wait for menu items
            try:
                await page.wait_for_selector("gem-menu-item, [role='menuitem'], [role='option']", timeout=3000, state="visible")
            except Exception:
                pass

            try:
                menu_entries = await page.evaluate(r"""() => Array.from(document.querySelectorAll('gem-menu-item, [role="menuitem"], [role="option"]'))
                    .map(i => (i.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 60)).filter(Boolean).slice(0, 15)""") or menu_entries
            except Exception:
                pass

            # 1. Select target model if not already matching
            if not matches_active(btn_text, btn_aria):
                click_result = await page.evaluate("""(args) => {
                    const items = Array.from(document.querySelectorAll('gem-menu-item, [role="menuitem"], [role="option"]'));
                    for (const item of items) {
                        const text = (item.innerText || '').toLowerCase();
                        const matches = args.keywords.some(k => text.includes(k.toLowerCase()));
                        const excluded = args.exclude_keywords && args.exclude_keywords.some(k => text.includes(k.toLowerCase()));
                        if (matches && !excluded) {
                            item.scrollIntoView({ block: 'nearest' });
                            item.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
                            item.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
                            item.click();
                            return { success: true, itemText: item.innerText };
                        }
                    }
                    return { success: false };
                }""", {"keywords": keywords, "exclude_keywords": exclude_keywords})

                if not click_result.get("success"):
                    # Fallback locator click
                    opt = page.locator(f"gem-menu-item:has-text('{target_model_name}'), [role='menuitem']:has-text('{target_model_name}')").first
                    if await opt.count() > 0 and await opt.is_visible():
                        await opt.click(force=True, timeout=2000)

                # Continue as soon as the picker button shows the new model (max 0.8s, as before)
                for _ in range(10):
                    await asyncio.sleep(0.08)
                    try:
                        if matches_active((await mode_btn.inner_text()).strip(), (await mode_btn.get_attribute("aria-label") or "").strip()):
                            break
                    except Exception:
                        break

            # 2. Select thinking level if specified
            if target_thinking and target_key != "lite":
                # Ensure menu is open
                t_item = page.locator("gem-menu-item[value='thinking_level'], [aria-label*='Thinking' i]").first
                if await t_item.count() == 0 or not await t_item.is_visible():
                    await mode_btn.click(timeout=2000)
                    await asyncio.sleep(0.5)
                    t_item = page.locator("gem-menu-item[value='thinking_level'], [aria-label*='Thinking' i]").first

                if await t_item.count() > 0:
                    try:
                        await t_item.hover(timeout=2000)
                    except Exception:
                        pass
                    await asyncio.sleep(0.5)

                    await page.evaluate("""(lvl) => {
                        const items = Array.from(document.querySelectorAll('gem-menu-item, [role="menuitem"], [role="option"]'));
                        for (const item of items) {
                            if ((item.innerText || '').toLowerCase().startsWith(lvl.toLowerCase())) {
                                item.scrollIntoView({ block: 'nearest' });
                                item.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
                                item.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
                                item.click();
                                return true;
                            }
                        }
                        return false;
                    }""", target_thinking)
                    await asyncio.sleep(0.6)
                    logger.info(f"[{account_id}] Selected Gemini Thinking level: {target_thinking}")

            # Verify if mode switched
            btn_text = (await mode_btn.inner_text()).strip()
            btn_aria = (await mode_btn.get_attribute("aria-label") or "").strip()
            if matches_active(btn_text, btn_aria):
                logger.info(f"[{account_id}] Successfully switched Gemini model to: '{btn_text}' (aria: '{btn_aria}')")
                break

        else:
            # Neither attempt ended with the picker showing the requested model: say so instead of
            # silently answering with whatever model is still selected.
            logger.warning(f"[{account_id}] Model switch to '{target_model_name}' was NOT confirmed (picker still shows '{btn_text}'). The reply will come from the model currently selected in Gemini Web. Picker entries: {menu_entries}")

        # Always close leftover overlay/menus cleanly
        try:
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.05)
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.05)
        except Exception:
            pass

        if acc:
            acc.active_model_id = model_id
            acc.active_thinking_level = target_thinking
            acc.save_meta()

        return True
    except Exception as e:
        logger.warning(f"[{account_id}] Could not switch model in UI to '{model_id}': {e}")
        try:
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.1)
            await page.keyboard.press("Escape")
        except Exception:
            pass
        return False

class GeminiInternalError(RuntimeError):
    """Raised when Google Gemini Web responds with an internal server error bubble."""
    pass

class RequestCancelledError(RuntimeError):
    """Raised when an active Gemini generation or request is stopped / cancelled by user."""
    pass

# ---------------------------------------------------------------------------
# Google sign-in state helpers
# ---------------------------------------------------------------------------
GOOGLE_AUTH_COOKIE_NAMES = ("SID", "SSID", "__Secure-1PSID", "SAPISID", "OSID")
NOT_SIGNED_IN_HINT = "Click 'Re-login' in the dashboard (or run Login_Account.bat) to sign in to Google again."

def count_google_auth_cookies(cookies: Any) -> int:
    """Counts Google login cookies in a Playwright cookie list. Fewer than 2 means the profile is signed out."""
    return sum(1 for c in (cookies or []) if c.get("name") in GOOGLE_AUTH_COOKIE_NAMES and c.get("value"))

async def context_google_auth_count(context: Any) -> int:
    cookies = await context.cookies(["https://gemini.google.com", "https://accounts.google.com"])
    return count_google_auth_cookies(cookies)

async def is_gemini_page_signed_in(account: Any, page: Any) -> bool:
    """
    Returns False when the account's Gemini tab is in signed-out guest mode.
    Guest mode still shows a prompt box, so without this check requests were silently sent through
    the anonymous guest page (limited models, frequent 'I encountered an error' replies).
    Any failure of the check itself is treated as signed in, so it can never block a healthy account.
    """
    try:
        if await context_google_auth_count(account.context) < 2:
            return False
    except Exception as ck_err:
        logger.debug(f"[{account.account_id}] Sign-in cookie check note: {ck_err}")
        return True
    try:
        guest_ui = await page.evaluate("""() => {
            const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
            const hasAvatar = !!document.querySelector("a[href*='SignOutOptions'], a[aria-label*='Google Account' i], img[alt*='Google Account' i], button[aria-label*='Google Account' i], [aria-label*='Google គណនី' i], [aria-label*='គណនី Google' i]");
            if (hasAvatar) return false;
            if (document.querySelector('[data-test-id="sign-out-banner"]')) return true;
            // Guest page shows "Sign in" as plain buttons as well as links, so check every clickable element
            // (a session can be dead on Google's side while its cookies are still present in the profile).
            return Array.from(document.querySelectorAll("a, button, [role='button']")).some(el => {
                if (!visible(el)) return false;
                const t = (el.innerText || el.getAttribute('aria-label') || '').trim().toLowerCase();
                const href = el.getAttribute('href') || '';
                return t === 'sign in' || t === 'đăng nhập' || t === 'ចូល' || href.includes('ServiceLogin');
            });
        }""")
        return not guest_ui
    except Exception as dom_err:
        logger.debug(f"[{account.account_id}] Sign-in DOM check note: {dom_err}")
        return True

GEMINI_INTERNAL_ERROR_PATTERNS = [
    "encountering an error",
    "encountered an error",
    "something went wrong",
    "couldn't complete that request",
    "could not complete that request",
    "an error occurred",
    "can't generate a response right now",
    "cannot generate a response right now",
    "unable to help with that request",
    "can't help with that request",
    "try something else for you",
    "please try your request again",
    "try your request again",
    "could you try again",
    "please try again later",
    "try again later",
    "i'm having trouble responding right now",
    "having trouble responding right now",
    "having a hard time fulfilling your request",
    "having a hard time",
    "can i help you with something else instead",
    "can i help with something else instead",
    "trouble with that right now",
    "unable to fulfill this request",
    "can't fulfill this request",
    "cannot fulfill this request",
]

GEMINI_RATE_LIMIT_PATTERNS = [
    "reached your limit",
    "reached the limit",
    "limit reached",
    "too many requests",
    "quota exceeded",
    "rate limit",
    "daily limit",
    "temporary limit",
    "capacity reached",
    "try again in",
    "you've reached your usage limit",
    "you have reached your usage limit",
    "you're out of prompts",
]

def is_gemini_internal_error(text: str) -> bool:
    """Checks if the extracted response is actually a canned Google Gemini Web error message."""
    if not text or not isinstance(text, str):
        return False
    t = text.strip().lower()
    if len(t) < 260 and any(p in t for p in GEMINI_INTERNAL_ERROR_PATTERNS):
        return True
    return False

def is_gemini_rate_limit_error(text: str) -> bool:
    """Checks if the extracted response is a Google Gemini rate limit / quota exhaustion error."""
    if not text or not isinstance(text, str):
        return False
    t = text.strip().lower()
    if len(t) < 300 and any(p in t for p in GEMINI_RATE_LIMIT_PATTERNS):
        return True
    return False

def dedupe_streamed_duplicate(text: str) -> str:
    """
    Collapses a response whose DOM text was captured twice (a partial streamed copy immediately
    followed by the complete copy, or vice-versa). Only triggers when the first 60 characters
    reappear later AND one segment is a pure prefix of the other, so genuine answers are untouched.
    """
    if not text or len(text) < 80:
        return text
    head = text[:60]
    idx = text.find(head, 1)
    if idx < 40:
        return text
    first, second = text[:idx].strip(), text[idx:].strip()
    if not first or not second:
        return text
    if second.startswith(first) or first.startswith(second):
        longer = second if len(second) >= len(first) else first
        logger.warning(f"[ResponseGuard] Collapsed duplicated DOM capture ({len(text)} -> {len(longer)} chars).")
        return longer
    return text

def looks_like_unfinished_json(text: str) -> bool:
    """True when the text is evidently a JSON answer (starts with { or [, optionally after a code-fence /
    'JSON' label) that does not parse yet, i.e. Gemini is most likely still streaming it."""
    if not text:
        return False
    t = text.strip()
    if t.startswith("```"):
        lines = t.splitlines()[1:]  # drop the opening ```json fence line
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        t = " ".join(lines)
    starts = [i for i in (t.find("{"), t.find("[")) if 0 <= i <= 12]
    if not starts:
        return False
    try:
        json.loads(t[min(starts):].strip())
        return False
    except Exception:
        return True

def validate_gemini_output(account_or_id: Any, text: str) -> str:
    """Validates the extracted text from Gemini Web. Triggers circuit breaker cooldown if rate limited or repeated errors occur."""
    account = account_or_id if isinstance(account_or_id, AccountSession) else account_manager.accounts.get(str(account_or_id))
    acc_id = account.account_id if account else str(account_or_id)
    text = dedupe_streamed_duplicate(text)

    if is_gemini_rate_limit_error(text):
        clean_msg = text.strip().replace("\n", " ")[:150]
        if account:
            account.trigger_cooldown(600, reason=f"Rate limit: {clean_msg}")
        logger.warning(f"[{acc_id}] [CircuitBreaker] Rate limit detected! Cooldown activated for 10m: '{clean_msg}'")
        raise GeminiInternalError(f"Rate limited by Google Gemini Web: '{clean_msg}'")

    if is_gemini_internal_error(text):
        clean_msg = text.strip().replace("\n", " ")[:150]
        if account:
            account.consecutive_failures += 1
            if account.consecutive_failures >= 3 and not account.is_cooling_down():
                account.trigger_cooldown(300, reason=f"3 consecutive internal errors: {clean_msg}")
        logger.warning(f"[{acc_id}] Google Gemini Web returned internal error: '{clean_msg}'")
        raise GeminiInternalError(f"Gemini Web internal error from Google: '{clean_msg}'")

    if account:
        account.consecutive_failures = 0
    return text

async def execute_gemini_chat(
    account: AccountSession,
    prompt: str,
    audio_path: Optional[str] = None,
    auto_delete: Optional[bool] = None,
    model: Optional[str] = None,
    timeout: Optional[int] = None,
) -> str:
    """Executes a single chat interaction on Gemini Web, optionally attaching an audio file and auto-deleting history."""
    async with account.lock:
        account.cancelled = False
        account.is_busy = True
        try:
            account.active_task = asyncio.current_task()
        except Exception:
            pass
        account.last_used_at = time.time()
        page: Optional[Page] = None
        temp_audio_clean: Optional[str] = None
        try:
            # The next standby tab is pre-warmed after the prompt is submitted (see below), so its page
            # load does not slow down model switching / prompt entry of this request.
            page = await account.get_or_create_page(rewarm=False)

            # Check if redirected to Google sign-in
            if "accounts.google.com" in page.url:
                session_backup = PROFILES_DIR / account.account_id / "portable_session.json"
                if session_backup.exists() and not getattr(account, "_chat_auto_restore_attempted", False):
                    account._chat_auto_restore_attempted = True
                    logger.info(f"[{account.account_id}] Session redirect detected. Auto-recovering from portable_session backup...")
                    await account_manager.restore_portable_session(account.account_id)
                    await page.goto(GEMINI_URL, wait_until="domcontentloaded", timeout=20000)
                    await asyncio.sleep(1.5)
                if "accounts.google.com" in page.url:
                    account.status = "needs_login"
                    raise RuntimeError(
                        f"Session expired or logged out for account '{account.account_id}'. Please re-import or log in via the dashboard."
                    )

            # Check if Google returned unusual traffic / reCAPTCHA challenge
            from captcha_solver import CaptchaSolver
            if await CaptchaSolver.is_captcha_present(page):
                logger.info(f"[{account.account_id}] Google reCAPTCHA / unusual traffic detected on page!")
                if getattr(account_manager, "captcha_solver_enabled", True):
                    try:
                        settings_dict = {
                            "captcha_provider": getattr(account_manager, "captcha_provider", "free_audio"),
                            "captcha_api_key": getattr(account_manager, "captcha_api_key", ""),
                        }
                        logger.info(f"[{account.account_id}] Initiating Auto CAPTCHA solver ({settings_dict['captcha_provider']})...")
                        solved = await CaptchaSolver.handle_page_captcha(page, account.account_id, settings_dict)
                        if solved:
                            logger.info(f"[{account.account_id}] Auto CAPTCHA unlock succeeded!")
                            await page.goto(GEMINI_URL, wait_until="domcontentloaded", timeout=20000)
                            await asyncio.sleep(2.0)
                        else:
                            logger.warning(f"[{account.account_id}] Auto CAPTCHA unlock could not fully resolve the challenge.")
                    except Exception as solver_err:
                        logger.error(f"[{account.account_id}] Error in Auto CAPTCHA solver: {solver_err}")

                still_blocked = await CaptchaSolver.is_captcha_present(page)
                if still_blocked:
                    account.status = "needs_login"
                    raise RuntimeError(
                        f"Google presented a verification challenge ('unusual traffic detected') for account '{account.account_id}'. "
                        f"Please click 'Re-login' in the dashboard to complete the verification in Chrome."
                    )

            # Ensure tab is on Gemini app URL
            curr_url = page.url or ""
            if not curr_url.startswith("https://gemini.google.com/app"):
                logger.info(f"[{account.account_id}] Current URL '{curr_url}' is not on /app. Navigating to {GEMINI_URL}...")
                await page.goto(GEMINI_URL, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(1.0)

            # Refuse to run in Gemini's signed-out guest mode: take the account out of rotation instead
            if not getattr(account, "is_cdp", False) and not await is_gemini_page_signed_in(account, page):
                account.status = "needs_login"
                account.last_error = f"Not signed in to Google. {NOT_SIGNED_IN_HINT}"
                try:
                    await account.close_warm_page()
                except Exception:
                    pass
                raise RuntimeError(
                    f"Account '{account.account_id}' is not signed in to Google (Gemini Web is showing guest mode). {NOT_SIGNED_IN_HINT}"
                )

            # Ensure fresh conversation session (reset any previous stuck conversation, loaded chat, or stop button)
            try:
                has_stuck_or_existing = await page.evaluate("""() => {
                    const hasConvInUrl = window.location.pathname.startsWith('/app/') && window.location.pathname !== '/app/' && window.location.pathname !== '/app';
                    const hasBubbles = document.querySelectorAll('user-query, model-response').length > 0;
                    const stopBtn = document.querySelector("button[aria-label*='Stop' i], button[aria-label*='Dừng' i], button[aria-label*='បញ្ឈប់' i], button[aria-label*='停止' i], button[data-test-id*='stop'], button.stop-button, button[aria-label*='Stop response' i], button[aria-label*='Stop generating' i]");
                    const hasStop = stopBtn && stopBtn.offsetWidth > 0 && window.getComputedStyle(stopBtn).display !== 'none';
                    return hasConvInUrl || hasBubbles || !!hasStop;
                }""")
                if has_stuck_or_existing:
                    logger.info(f"[{account.account_id}] Existing conversation or active generation detected. Starting New Chat...")
                    new_chat_btn = page.locator("a[href='/app'], button[aria-label*='New chat' i], a[aria-label*='New chat' i], [data-test-id*='new-chat'], [aria-label*='New chat' i]").first
                    if await new_chat_btn.count() > 0 and await new_chat_btn.is_visible():
                        await new_chat_btn.click(timeout=2000)
                        await asyncio.sleep(0.5)
                    else:
                        # Fast SPA reset without full network reload
                        await page.evaluate("() => { if (window.location.pathname !== '/app') window.location.pathname = '/app'; }")
                        await asyncio.sleep(0.6)
            except Exception as nc_err:
                logger.debug(f"New chat reset check: {nc_err}")

            # Dismiss any blocking overlays before checking or switching mode
            await dismiss_gemini_overlays(page, account.account_id)

            # Switch model in Gemini Web UI FIRST so input area matches active model mode
            if model and model != "gemini-web":
                await switch_gemini_model_ui(page, model, account.account_id)
            elif not getattr(account, "is_calibrated", False):
                # Auto-tune to preferred fast model (Flash-Lite)
                preferred = getattr(account_manager, "preferred_fast_model", "gemini-3.5-flash-lite")
                await switch_gemini_model_ui(page, preferred, account.account_id)
                account.is_calibrated = True
                account.save_meta()

            # Wait for the chat input selector AFTER model switch
            input_selectors = [
                "rich-textarea div[contenteditable='true']",
                "div[contenteditable='true'].ql-editor",
                "div[role='textbox'][contenteditable='true']",
                "div[contenteditable='true']",
                "textarea[aria-label*='prompt' i]",
            ]

            input_locator = None
            for sel in input_selectors:
                try:
                    loc = page.locator(sel).first
                    if await loc.count() > 0 and await loc.is_visible():
                        input_locator = loc
                        break
                except Exception:
                    continue

            if not input_locator:
                try:
                    input_locator = await page.wait_for_selector(
                        ", ".join(input_selectors),
                        timeout=20000,
                        state="visible"
                    )
                except Exception:
                    raise RuntimeError("Timed out waiting for Gemini prompt input area. Page may still be loading or needs authentication.")

            # If audio_path is provided, attach the audio file to Gemini Web
            if audio_path and os.path.exists(audio_path):
                target_audio, is_temp = ensure_mp3_audio(audio_path)
                if is_temp:
                    temp_audio_clean = target_audio
                abs_audio = os.path.abspath(target_audio)
                file_name = Path(abs_audio).name
                logger.info(f"[{account.account_id}] Attaching audio file: {abs_audio}...")
                attached = False

                # 1. Click 'Upload & tools' button to open the upload menu and mount hidden file inputs
                upload_btn = page.locator(
                    "button[aria-label*='Upload & tools' i], button[aria-label*='Upload' i], button[data-tooltip*='Upload' i], button[aria-label*='Tải lên' i]"
                ).first

                if await upload_btn.count() > 0:
                    await dismiss_gemini_overlays(page, account.account_id)
                    try:
                        await upload_btn.click(timeout=4000)
                    except Exception:
                        await upload_btn.click(force=True, timeout=2000)
                    await asyncio.sleep(1.0)

                    # Check if Gemini Web is in signed-out/guest mode where uploads are disabled
                    is_signed_out_menu = await page.evaluate("""() => {
                        const banner = document.querySelector('[data-test-id="sign-out-banner"]');
                        const uploadBtn = document.querySelector('[data-test-id="local-images-files-uploader-button"]');
                        const isBtnDisabled = uploadBtn && (uploadBtn.hasAttribute('disabled') || uploadBtn.getAttribute('aria-disabled') === 'true');
                        return !!banner || !!isBtnDisabled;
                    }""")

                    if is_signed_out_menu:
                        account.status = "needs_login"
                        raise RuntimeError(
                            f"Account '{account.account_id}' is not signed into Google on Gemini Web (showing guest mode). "
                            f"File/audio upload is disabled by Google in guest mode. "
                            f"Please click 'Re-login' in the dashboard to sign in your Google account."
                        )

                    # 2. Set the file directly on the mounted file input
                    file_inps = page.locator("input[type='file'].hidden-file-input, input[type='file']")
                    if await file_inps.count() > 0:
                        try:
                            await file_inps.first.set_input_files(abs_audio)
                            attached = True
                            logger.info(f"[{account.account_id}] Audio file set via file input.")
                        except Exception as inp_err:
                            logger.warning(f"[{account.account_id}] Direct set_input_files failed: {inp_err}")

                if not attached:
                    raise RuntimeError(
                        f"[{account.account_id}] Failed to attach audio file '{file_name}'. "
                        f"Upload button or file input was not accessible on Gemini Web."
                    )

                # 3. Wait for audio attachment chip in DOM
                logger.info(f"[{account.account_id}] Waiting for audio chip to attach in DOM...")
                file_stem = Path(file_name).stem
                chip_confirmed = False
                for _ in range(30):
                    chips = await page.locator(
                        "attachment-chip, file-chip, .file-chip, [data-test-id*='attachment'], div[aria-label*='Remove file' i], div[aria-label*='audio' i], .attachment-preview, div[data-test-id*='file']"
                    ).count()
                    if chips > 0:
                        chip_confirmed = True
                        logger.info(f"[{account.account_id}] Audio attachment chip confirmed in DOM ({chips} chip(s)).")
                        break
                    try:
                        if await page.get_by_text(file_name).first.is_visible() or await page.get_by_text(file_stem).first.is_visible():
                            chip_confirmed = True
                            logger.info(f"[{account.account_id}] Audio chip '{file_name}' confirmed by text.")
                            break
                    except Exception:
                        pass
                    await asyncio.sleep(0.5)

                if not chip_confirmed:
                    logger.warning(f"[{account.account_id}] Warning: Audio chip not detected after 15s. Proceeding cautiously...")

                # 4. Wait for audio chip upload processing spinner to complete (scoped to attachment chip / input bar)
                for _ in range(25):
                    chip_spinners = await page.locator(
                        "attachment-chip mat-progress-spinner, file-chip mat-progress-spinner, .file-chip mat-progress-spinner, "
                        "attachment-chip [role='progressbar'], file-chip [role='progressbar'], .attachment-preview mat-progress-spinner, "
                        "rich-textarea ~ * mat-progress-spinner, .input-area mat-progress-spinner"
                    ).count()
                    if chip_spinners == 0:
                        logger.info(f"[{account.account_id}] Audio chip upload processing complete.")
                        break
                    await asyncio.sleep(0.5)

                await asyncio.sleep(0.5)

            # Dismiss any blocking overlays/tooltips/dialogs before entering prompt
            await dismiss_gemini_overlays(page, account.account_id)

            # Record existing response count before entering prompt to isolate new responses
            init_response_count = await page.locator("model-response").count()

            # Focus and instant input prompt (no human typing simulation delay)
            logger.info(f"[{account.account_id}] Entering prompt ({len(prompt)} chars). Header: {prompt[:120]!r}...")
            
            try:
                if input_locator:
                    await input_locator.click(timeout=1000)
            except Exception:
                pass

            # Instant text insertion
            min_expected_len = min(len(prompt), 20)
            dom_text = ""
            try:
                await page.keyboard.insert_text(prompt)
            except Exception:
                pass

            try:
                dom_text = await input_locator.evaluate("el => (el.innerText || el.textContent || '').trim()")
            except Exception:
                dom_text = ""

            if len(dom_text) < min_expected_len:
                # Fast DOM fallback
                try:
                    await page.evaluate("""([selectors, text]) => {
                        let el = null;
                        for (const s of selectors) {
                            el = document.querySelector(s);
                            if (el) break;
                        }
                        if (el) {
                            el.focus();
                            document.execCommand('selectAll', false, null);
                            document.execCommand('insertText', false, text);
                            el.dispatchEvent(new Event('input', { bubbles: true }));
                            el.dispatchEvent(new Event('change', { bubbles: true }));
                        }
                    }""", [input_selectors, prompt])
                    dom_text = await input_locator.evaluate("el => (el.innerText || el.textContent || '').trim()")
                except Exception:
                    pass

            logger.info(f"[{account.account_id}] Verified in Gemini Web DOM ({len(dom_text)} chars): {dom_text[:120]!r}...")

            # Submit prompt: wait for send button to become enabled
            send_selectors = [
                "button[aria-label*='Send' i]",
                "button[aria-label*='ផ្ញើ' i]",
                "button[aria-label*='Gửi' i]",
                "button[aria-label*='发送' i]",
                "button[data-test-id*='send' i]",
                ".send-button.submit button:not([aria-label*='Stop' i])",
                ".send-button button:not([aria-label*='Stop' i])",
                "button.send-button:not([aria-label*='Stop' i])",
                "div.send-button-container button:not([aria-label*='Stop' i]):not([data-test-id*='stop'])",
            ]
            send_btn = page.locator(", ".join(send_selectors)).first
            for _ in range(15):
                if await send_btn.count() > 0 and await send_btn.is_enabled():
                    break
                await asyncio.sleep(0.08)

            try:
                await send_btn.click(timeout=1500)
                logger.info(f"[{account.account_id}] Clicked Send button.")
            except Exception:
                try:
                    await send_btn.click(force=True, timeout=1000)
                    logger.info(f"[{account.account_id}] Clicked Send button (forced).")
                except Exception:
                    try:
                        await send_btn.evaluate("el => el.click()")
                        logger.info(f"[{account.account_id}] Clicked Send button (evaluated).")
                    except Exception:
                        await page.keyboard.press("Control+Enter")
                        await page.keyboard.press("Enter")
                        logger.info(f"[{account.account_id}] Clicked Send via Enter key fallback.")

            # Check and auto-dismiss reminder modal (e.g. "A reminder about creating videos")
            agree_btn = page.locator("button:has-text('Agree'), button:has-text('I agree'), button:has-text('Đồng ý'), button[aria-label*='Agree' i]")
            if await agree_btn.count() > 0 and await agree_btn.first.is_visible():
                logger.info(f"[{account.account_id}] Dismissing reminder modal ('Agree')...")
                try:
                    await agree_btn.first.click(force=True, timeout=2000)
                except Exception:
                    pass

            logger.info(f"[{account.account_id}] Prompt submitted. Monitoring Gemini response...")

            # Pre-warm the next standby tab now, while this request is only waiting on Gemini
            if not getattr(account, "is_cdp", False):
                asyncio.create_task(account.prepare_warm_page())

            # Determine dynamic polling interval based on speed optimization setting
            poll_interval = 0.12 if getattr(account_manager, "turbo_dom_polling", True) else 0.25

            # Wait for generation to start (Stop button visible OR new model-response node mounted)
            saw_generation_start = False
            for _ in range(40 if poll_interval < 0.2 else 25):
                if getattr(account, "cancelled", False):
                    logger.info(f"[{account.account_id}] Execution stopped before generation started: cancelled by user.")
                    raise RequestCancelledError(f"Request cancelled by user for account '{account.account_id}'")

                gen_state = await page.evaluate("""(initCount) => {
                    const stopBtn = document.querySelector("button[aria-label*='Stop' i], button[aria-label*='Dừng' i], button[aria-label*='បញ្ឈប់' i], button[aria-label*='停止' i], button[data-test-id*='stop'], button.stop-button, button[aria-label*='Stop response' i], button[aria-label*='Stop generating' i]");
                    const isStopVisible = stopBtn ? (
                        stopBtn.offsetWidth > 0 &&
                        stopBtn.offsetHeight > 0 &&
                        window.getComputedStyle(stopBtn).display !== 'none'
                    ) : false;
                    
                    const count = document.querySelectorAll('model-response').length;
                    const hasNewNode = count > initCount;
                    return { isStopVisible, hasNewNode };
                }""", init_response_count)

                if gen_state.get('isStopVisible') or gen_state.get('hasNewNode'):
                    saw_generation_start = True
                    logger.info(f"[{account.account_id}] Generation actively started (stop={gen_state.get('isStopVisible')}, new_node={gen_state.get('hasNewNode')}).")
                    break
                await asyncio.sleep(poll_interval)

            # Monitor generation until Stop button disappears and response text is present
            start_gen = time.time()
            if timeout and int(timeout) > 0:
                max_wait = int(timeout)
            else:
                default_audio = getattr(account_manager, "audio_timeout", 360)
                default_gen = getattr(account_manager, "generation_timeout", 180)
                max_wait = default_audio if audio_path else default_gen
            final_text = ""
            last_logged_tick = 0
            stable_count = 0
            last_len = 0
            stopped_empty_count = 0

            while time.time() - start_gen < max_wait:
                if getattr(account, "cancelled", False):
                    logger.info(f"[{account.account_id}] Execution stopped: cancellation requested by user.")
                    raise RequestCancelledError(f"Request cancelled by user for account '{account.account_id}'")

                await asyncio.sleep(poll_interval)
                elapsed = round(time.time() - start_gen, 1)

                state = await page.evaluate(r"""() => {
                    const stopBtn = document.querySelector("button[aria-label*='Stop' i], button[aria-label*='Dừng' i], button[aria-label*='បញ្ឈប់' i], button[aria-label*='停止' i], button[data-test-id*='stop'], button.stop-button, button[aria-label*='Stop response' i], button[aria-label*='Stop generating' i]");
                    const isStopVisible = stopBtn ? (
                        stopBtn.offsetWidth > 0 &&
                        stopBtn.offsetHeight > 0 &&
                        window.getComputedStyle(stopBtn).display !== 'none' &&
                        window.getComputedStyle(stopBtn).visibility !== 'hidden'
                    ) : false;

                    // Accurately extract complete text from the latest model-response
                    let latestTxt = '';
                    const responses = document.querySelectorAll('model-response, pending-response, response-container');
                    let contentNodes = [];
                    const THOUGHT_SEL = 'expandable-thought, thought-disclosure, model-thought, thinking-overlay, .thought-container, .thoughts-wrapper, details';
                    const isRendered = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                    if (responses.length > 0) {
                        const lastResp = responses[responses.length - 1];
                        // Read ONLY rendered answer nodes of the LIVE element. innerText on a detached clone
                        // behaves like textContent: it also returns hidden draft/duplicate copies, which
                        // produced doubled JSON answers on thinking models (4 Argon / Pro).
                        let answerNodes = Array.from(lastResp.querySelectorAll('message-content, structured-content-container, .response-content, .markdown'))
                            .filter(el => isRendered(el) && !el.closest(THOUGHT_SEL));
                        answerNodes = answerNodes.filter(el => !answerNodes.some(o => o !== el && o.contains(el)));
                        if (answerNodes.length > 0) {
                            latestTxt = answerNodes.map(el => (el.innerText || '').trim()).filter(Boolean).join('\n').trim();
                        } else {
                            const thoughts = Array.from(lastResp.querySelectorAll(THOUGHT_SEL));
                            const prevDisplay = thoughts.map(th => th.style.display);
                            thoughts.forEach(th => { th.style.display = 'none'; });
                            try {
                                latestTxt = (lastResp.innerText || '').trim();
                            } finally {
                                thoughts.forEach((th, i) => { th.style.display = prevDisplay[i]; });
                            }
                        }
                    }
                    if (!latestTxt) {
                        contentNodes = Array.from(document.querySelectorAll('message-content, structured-content-container, .response-content'));
                        if (contentNodes.length > 0) {
                            latestTxt = (contentNodes[contentNodes.length - 1].innerText || '').trim();
                        }
                    }

                    // Strip any 'Gemini said' or language variants from anywhere in line starts/ends
                    latestTxt = latestTxt.replace(/^(?:Gemini said|Gemini បាននិយាយថា|Gemini đã nói|Gemini 说)[\s:]*/gim, '').trim();
                    latestTxt = latestTxt.replace(/[\s:]*(?:Gemini said|Gemini បាននិយាយថា|Gemini đã nói|Gemini 说)$/gim, '').trim();

                    let rateLimitMsg = '';
                    const alertNodes = Array.from(document.querySelectorAll('.toast, [role="alert"], snackbar, .mat-snack-bar-container, .error-message, .alert-box, mat-snack-bar-container'));
                    for (const an of alertNodes) {
                        const txt = (an.innerText || an.textContent || '').trim();
                        if (/reached your limit|too many requests|rate limit|quota exceeded|try again in/i.test(txt)) {
                            rateLimitMsg = txt;
                            break;
                        }
                    }

                    // Scoped copy button detection (ignore 'Copy prompt' on user query)
                    const copyBtns = Array.from(document.querySelectorAll("button[aria-label*='Copy' i], button[data-test-id*='copy'], [data-tooltip*='Copy' i], button[aria-label*='Sao chép' i], button[aria-label*='ចម្លង' i], button[aria-label*='复制' i]"));
                    const respCopyBtn = copyBtns.find(b => {
                        const lbl = (b.getAttribute('aria-label') || '').toLowerCase();
                        return !lbl.includes('prompt') && !lbl.includes('lời nhắc') && !lbl.includes('សារ') && b.offsetWidth > 0;
                    });
                    const isCopyVisible = !!respCopyBtn;

                    return {
                        hasStop: isStopVisible,
                        isCopyVisible: isCopyVisible,
                        modelResponsesCount: responses.length || contentNodes.length,
                        latestTxtLen: latestTxt.length,
                        latestTxt: latestTxt,
                        rateLimitMsg: rateLimitMsg
                    };
                }""")

                if state.get('rateLimitMsg'):
                    clean_rl = state['rateLimitMsg'].replace('\n', ' ')[:140]
                    account.trigger_cooldown(600, reason=f"Rate limit: {clean_rl}")
                    raise GeminiInternalError(f"Rate limited by Google Gemini Web: '{clean_rl}'")

                # CRITICAL: If Stop button is currently visible, Gemini is ACTIVELY GENERATING OR THINKING.
                # NEVER finalize while hasStop is True!
                if state['hasStop']:
                    saw_generation_start = True
                    stable_count = 0
                    last_len = state['latestTxtLen']
                    stopped_empty_count = 0
                    if state['latestTxtLen'] > 0:
                        final_text = state['latestTxt']
                    
                    curr_tick = int(elapsed // 5)
                    if curr_tick > last_logged_tick:
                        last_logged_tick = curr_tick
                        logger.info(f"[{account.account_id}] [Generation {elapsed}s] actively generating (hasStop=True, models={state['modelResponsesCount']}, textLen={state['latestTxtLen']})")
                    
                    # StallGuard: If thinking or generation hangs with 0 output tokens:
                    # Flash/Lite: abort after 35s to prevent freezing
                    # Pro/Argon: allow up to 90s for deep reasoning
                    is_complex_model = any(k in (model or "").lower() for k in ("pro", "argon", "think"))
                    max_stall = 140.0 if is_complex_model else 45.0
                    if elapsed > max_stall and state['latestTxtLen'] == 0:
                        logger.warning(f"[{account.account_id}] [StallGuard] Model '{model}' thinking deadlock detected (>{max_stall}s with 0 output). Aborting for instant fast auto-recovery...")
                        raise GeminiInternalError(f"Gemini Web thinking process stalled on '{model}' (>{max_stall}s with 0 output tokens)")
                    continue

                if saw_generation_start and state['latestTxtLen'] > 0:
                    final_text = state['latestTxt']

                curr_tick = int(elapsed // 5)
                if curr_tick > last_logged_tick:
                    last_logged_tick = curr_tick
                    logger.info(f"[{account.account_id}] [Generation {elapsed}s] hasStop={state['hasStop']}, models={state['modelResponsesCount']}, textLen={state['latestTxtLen']}")

                # Fast Finalize Condition 1: Copy button visible (Gemini marks response completely ready)
                if saw_generation_start and state.get('isCopyVisible') and state['latestTxtLen'] > 0:
                    if state['latestTxtLen'] == last_len:
                        stable_count += 1
                        if stable_count >= 1:
                            logger.info(f"[{account.account_id}] Fast Finalized via Copy button ({state['latestTxtLen']} chars) at {elapsed}s.")
                            account.total_requests += 1
                            account.record_latency(elapsed)
                            try:
                                account.last_conversation_url = page.url
                                if "/app/" in page.url and page.url != GEMINI_URL:
                                    account.last_conversation_id = page.url.split("/app/")[-1].split("?")[0].split("#")[0]
                                    logger.info(f"[{account.account_id}] Captured Gemini Conversation URL: {account.last_conversation_url} (ID: {account.last_conversation_id})")
                            except Exception as url_err:
                                logger.debug(f"Error capturing conversation URL: {url_err}")

                            should_del = auto_delete if auto_delete is not None else getattr(account, "auto_delete_history", False)
                            if should_del and getattr(account, "last_conversation_id", None):
                                conv_id = account.last_conversation_id
                                try:
                                    await delete_conversation_on_page(page, conv_id)
                                except Exception as del_err:
                                    logger.debug(f"Auto-delete note: {del_err}")

                            return validate_gemini_output(account, state['latestTxt'])
                    else:
                        stable_count = 0
                        last_len = state['latestTxtLen']

                # Fast Finalize Condition 2: Text has stabilized (Stop button gone OR stabilized for 2 ticks)
                elif saw_generation_start and state['latestTxtLen'] > 0 and state['latestTxtLen'] == last_len:
                    stable_count += 1
                    # Stability is measured in seconds, not poll ticks, so fast polling cannot finalize a reply
                    # during a short pause in streaming (that returned cut-off answers such as '{"lines":').
                    if not state['hasStop']:
                        need_secs = 0.6
                    else:
                        need_secs = 0.5 if getattr(account, "is_cdp", False) else 0.75
                    if looks_like_unfinished_json(state['latestTxt']):
                        need_secs = max(need_secs, 3.0)  # a JSON answer that does not parse yet is still being written
                    req_stability = max(1, int(-(-need_secs // poll_interval)))
                    if stable_count >= req_stability:
                        logger.info(f"[{account.account_id}] Fast Finalized via text stabilization ({state['latestTxtLen']} chars, hasStop={state['hasStop']}) at {elapsed}s.")
                        account.total_requests += 1
                        account.record_latency(elapsed)
                        try:
                            account.last_conversation_url = page.url
                            if "/app/" in page.url and page.url != GEMINI_URL:
                                account.last_conversation_id = page.url.split("/app/")[-1].split("?")[0].split("#")[0]
                                logger.info(f"[{account.account_id}] Captured Gemini Conversation URL: {account.last_conversation_url} (ID: {account.last_conversation_id})")
                        except Exception as url_err:
                            logger.debug(f"Error capturing conversation URL: {url_err}")

                        should_del = auto_delete if auto_delete is not None else getattr(account, "auto_delete_history", False)
                        if should_del and getattr(account, "last_conversation_id", None):
                            conv_id = account.last_conversation_id
                            try:
                                await delete_conversation_on_page(page, conv_id)
                            except Exception as del_err:
                                logger.debug(f"Auto-delete note: {del_err}")

                        return validate_gemini_output(account, state['latestTxt'])
                else:
                    stable_count = 0
                    last_len = state['latestTxtLen']

                if saw_generation_start and not state['hasStop'] and state['latestTxtLen'] == 0:
                    stopped_empty_count += 1
                    if stopped_empty_count >= 25:
                        logger.warning(f"[{account.account_id}] Generation stopped without producing response text ({elapsed}s).")
                        break
                else:
                    stopped_empty_count = 0

            # If while loop exited without returning, generation timed out or halted incomplete
            should_del = auto_delete if auto_delete is not None else getattr(account, "auto_delete_history", False)
            if should_del and getattr(account, "last_conversation_id", None):
                logger.info(f"[{account.account_id}] Auto-deleting conversation '{account.last_conversation_id}' from Gemini...")
                try:
                    await delete_conversation_on_page(page, account.last_conversation_id)
                except Exception as del_err:
                    logger.warning(f"[{account.account_id}] Auto-delete error: {del_err}")

            try:
                await page.screenshot(path="gemini_timeout_error.png")
            except Exception:
                pass
            raise TimeoutError(f"Gemini generation timed out after {max_wait}s without producing a complete response.")

        finally:
            account.active_task = None
            if temp_audio_clean and os.path.exists(temp_audio_clean):
                try:
                    os.unlink(temp_audio_clean)
                except Exception:
                    pass
            account.is_busy = False
            if page:
                try:
                    if getattr(account, "is_cdp", False):
                        # For Direct Official Chrome CDP, keep the Gemini tab alive for instantaneous next responses!
                        pass
                    else:
                        await page.close()
                except Exception as e:
                    logger.debug(f"Error closing page: {e}")
            # Make sure a standby tab exists even when the request failed before the prompt was submitted
            if not getattr(account, "is_cdp", False):
                try:
                    asyncio.create_task(account.prepare_warm_page())
                except Exception:
                    pass

async def get_account_chat_history(account: AccountSession, limit: int = 20) -> List[Dict[str, Any]]:
    """Navigates to Gemini Web using the account's own context and inspects the sidebar to extract recent chat conversations."""
    # If the account is explicitly marked as CDP, query over CDP
    if getattr(account, "is_cdp", False):
        try:
            browser = await account_manager.playwright.chromium.connect_over_cdp(account.cdp_url)
            ctx = browser.contexts[0]
            gemini_pages = [p for p in ctx.pages if "gemini.google.com" in p.url]
            page = gemini_pages[0] if gemini_pages else await ctx.new_page()
            if not gemini_pages:
                await page.goto(GEMINI_URL, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(1.0)
            try:
                open_btn = page.locator("button[aria-label*='Open sidebar' i], button[aria-label*='Expand sidebar' i]").first
                if await open_btn.count() > 0 and await open_btn.is_visible():
                    await open_btn.click()
                    await asyncio.sleep(0.5)
            except Exception:
                pass
            history = await page.evaluate("""() => {
                const results = [];
                const links = Array.from(document.querySelectorAll("a[href*='/app/']"));
                for (const a of links) {
                    const href = a.getAttribute('href') || '';
                    if (!href.startsWith('/app/') || href === '/app' || href === '/app/') continue;
                    const fullText = (a.innerText || a.getAttribute('aria-label') || '').trim();
                    const title = fullText.split('\\n')[0].trim();
                    const convId = href.replace('/app/', '').split('?')[0].split('#')[0].trim();
                    if (convId && !results.some(r => r.conversation_id === convId)) {
                        results.push({ conversation_id: convId, title: title || 'Untitled Conversation', url: `https://gemini.google.com/app/${convId}` });
                    }
                }
                return results;
            }""")
            return history[:limit]
        except Exception as e:
            logger.warning(f"[{account.account_id}] CDP history fetch failed: {e}")
            return []

    # Standard account: inspect its own profile directly
    async with account.lock:
        page = None
        try:
            if not account.context:
                account.context = await account_manager.launch_context(account.profile_path, headless=True)
            page = await account.context.new_page()
            await page.goto(GEMINI_URL, wait_until="domcontentloaded", timeout=40000)
            await asyncio.sleep(2.0)

            # Ensure sidebar is open
            try:
                open_btn = page.locator("button[aria-label*='Open sidebar' i], button[aria-label*='Expand sidebar' i]").first
                if await open_btn.count() > 0 and await open_btn.is_visible():
                    await open_btn.click()
                    await asyncio.sleep(0.5)
            except Exception:
                pass

            history = await page.evaluate("""() => {
                const results = [];
                const links = Array.from(document.querySelectorAll("a[href*='/app/']"));
                for (const a of links) {
                    const href = a.getAttribute('href') || '';
                    if (!href.startsWith('/app/') || href === '/app' || href === '/app/') continue;
                    const fullText = (a.innerText || a.getAttribute('aria-label') || '').trim();
                    const title = fullText.split('\\n')[0].trim();
                    const convId = href.replace('/app/', '').split('?')[0].split('#')[0].trim();
                    if (convId && !results.some(r => r.conversation_id === convId)) {
                        results.push({
                            title: title || 'Untitled Conversation',
                            conversation_id: convId,
                            url: 'https://gemini.google.com' + href,
                            relative_url: href
                        });
                    }
                }
                return results;
            }""")
            return history[:limit]
        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass

async def delete_conversation_on_page(page: Page, conv_id: str) -> bool:
    """Deletes a conversation by conv_id on an active Gemini Web page."""
    try:
        # Ensure sidebar is open
        open_btn = page.locator("button[aria-label*='Open sidebar' i], button[aria-label*='Expand sidebar' i]").first
        if await open_btn.count() > 0 and await open_btn.is_visible():
            await open_btn.click()
            await asyncio.sleep(0.15)

        # Locate conversation link in sidebar
        link = page.locator(f"a[href*='{conv_id}']").first
        if await link.count() == 0:
            logger.debug(f"Conversation link '{conv_id}' not found in sidebar to delete.")
            return False

        await link.hover()
        await asyncio.sleep(0.08)

        # Find More options button
        parent = link.locator("..")
        more_btn = parent.locator("button[aria-label*='More options' i], button[aria-label*='options' i]").first
        if await more_btn.count() == 0:
            more_btn = link.locator("../..").locator("button[aria-label*='More options' i], button[aria-label*='options' i]").first

        if await more_btn.count() == 0:
            logger.debug(f"More options button not found for '{conv_id}'.")
            return False

        try:
            await more_btn.click(timeout=1500)
        except Exception:
            await more_btn.click(force=True, timeout=1000)
        await asyncio.sleep(0.15)

        # Click Delete from popup menu
        delete_item = page.locator("[data-test-id='delete-button'], [role='menuitem']:has-text('Delete'), button.mat-mdc-menu-item:has-text('Delete'), .cdk-overlay-pane button:has-text('Delete')").first
        if await delete_item.count() == 0:
            logger.debug(f"Delete menuitem not found for '{conv_id}'.")
            return False

        try:
            await delete_item.click(timeout=1500)
        except Exception:
            await delete_item.click(force=True, timeout=1000)
        await asyncio.sleep(0.15)

        # Click confirmation Delete button in modal dialog
        confirm_btn = page.locator("[role='dialog'] button:has-text('Delete'), mat-dialog-container button:has-text('Delete'), button[data-test-id='confirm-button']:has-text('Delete')").last
        if await confirm_btn.count() > 0:
            try:
                await confirm_btn.click(timeout=1500)
            except Exception:
                await confirm_btn.click(force=True, timeout=1000)
            await asyncio.sleep(0.3)
            logger.info(f"Successfully deleted conversation '{conv_id}' from Gemini Web.")
            return True
        else:
            logger.warning(f"Confirmation dialog Delete button not found for '{conv_id}'.")
            return False
    except Exception as e:
        logger.error(f"Error deleting conversation '{conv_id}' on page: {e}")
        return False

async def delete_account_conversation(account: AccountSession, conv_id: str) -> bool:
    """Opens Gemini Web and deletes the specified conversation from the account."""
    async with account.lock:
        page = None
        try:
            if not account.context:
                account.context = await account_manager.launch_context(account.profile_path, headless=True)
            page = await account.context.new_page()
            await page.goto(GEMINI_URL, wait_until="domcontentloaded", timeout=40000)
            await asyncio.sleep(2.0)
            return await delete_conversation_on_page(page, conv_id)
        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass

async def delete_all_account_conversations(account: AccountSession, limit: int = 30) -> int:
    """Deletes all recent conversations detected for an account in Gemini Web."""
    history = await get_account_chat_history(account, limit=limit)
    deleted_count = 0
    if not history:
        return 0

    async with account.lock:
        page = None
        try:
            if not account.context:
                account.context = await account_manager.launch_context(account.profile_path, headless=True)
            page = await account.context.new_page()
            await page.goto(GEMINI_URL, wait_until="domcontentloaded", timeout=40000)
            await asyncio.sleep(2.0)

            for item in history:
                conv_id = item.get("conversation_id")
                if conv_id:
                    ok = await delete_conversation_on_page(page, conv_id)
                    if ok:
                        deleted_count += 1
                        await asyncio.sleep(0.5)
            return deleted_count
        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass

# ---------------------------------------------------------------------------
# Account Request Cancellation & Stop Helpers
# ---------------------------------------------------------------------------
async def stop_account_requests(account: AccountSession) -> bool:
    """Cancels and interrupts any in-flight generation or request on the specified account."""
    account.cancelled = True
    stopped_any = False

    # Mark in-flight logs cancelled for this account
    await request_logger.cancel_inflight_for_account(account.account_id)

    # 1. Immediately cancel the asyncio task running execute_gemini_chat if active
    task = getattr(account, "active_task", None)
    if task and not task.done():
        try:
            task.cancel()
            stopped_any = True
            logger.info(f"[{account.account_id}] Cancelled active asyncio task on stop.")
        except Exception as te:
            logger.debug(f"[{account.account_id}] Task cancel note: {te}")

    # 2. Click Google's native Stop button (for CDP accounts, DO NOT close pages to avoid terminating Chrome!)
    if account.context:
        is_cdp_account = bool(getattr(account, "is_cdp", False))
        try:
            pages = list(account.context.pages)
            for page in pages:
                stopped_any = True
                try:
                    await page.evaluate("""() => {
                        const stopBtn = document.querySelector("button[aria-label*='Stop' i], button[aria-label*='Dừng' i], button[aria-label*='បញ្ឈប់' i], button[aria-label*='停止' i], button[data-test-id*='stop'], button.stop-button, button[aria-label*='Stop response' i], button[aria-label*='Stop generating' i]");
                        if (stopBtn) {
                            stopBtn.click();
                            return true;
                        }
                        try { window.stop(); } catch(e){}
                        return false;
                    }""")
                except Exception:
                    pass
                if not is_cdp_account:
                    try:
                        await page.close()
                    except Exception as e:
                        logger.debug(f"[{account.account_id}] Notice closing page on stop: {e}")
        except Exception as e:
            logger.warning(f"[{account.account_id}] Error stopping pages: {e}")

    # 3. Clean up warm tab if held (non-CDP accounts only)
    if not getattr(account, "is_cdp", False):
        try:
            if getattr(account, "warm_page", None):
                if not account.warm_page.is_closed():
                    await account.warm_page.close()
                account.warm_page = None
        except Exception:
            pass

    account.is_busy = False
    return stopped_any or True


async def stop_all_accounts_requests() -> int:
    """Stops/cancels in-flight requests across all accounts in the pool."""
    count = 0
    await request_logger.cancel_inflight_for_account(None)
    for acc in list(account_manager.accounts.values()):
        try:
            stopped = await stop_account_requests(acc)
            if stopped:
                count += 1
        except Exception as e:
            logger.warning(f"Error stopping account {acc.account_id}: {e}")
        finally:
            acc.cancelled = True
            acc.is_busy = False
    return count

# ---------------------------------------------------------------------------
# Analytics & Workload Intelligence Engine
# ---------------------------------------------------------------------------
def compute_analytics_summary(
    range_type: str = "today",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> Dict[str, Any]:
    """
    Computes comprehensive telemetry, custom period reports (daily, weekly, monthly, custom),
    model latencies, and token estimates directly from persistent SQLite records.
    """
    try:
        from telemetry_db import telemetry_db
        return telemetry_db.compute_analytics(
            range_type=range_type,
            start_date=start_date,
            end_date=end_date,
            account_manager_accounts=account_manager.accounts,
            routing_meta={
                "strategy": getattr(account_manager, "routing_strategy", "balanced"),
                "quota_guard_enabled": getattr(account_manager, "quota_guard_enabled", True),
                "quota_threshold": getattr(account_manager, "quota_threshold", 95),
            }
        )
    except Exception as e:
        logger.error(f"[compute_analytics_summary] Error querying telemetry_db: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}

