import os
import sys
import time
import asyncio
import logging
import tempfile
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Dict, Any, Optional
from playwright.async_api import Page, FrameLocator

logger = logging.getLogger("gemini_router.captcha")

# Configure pydub to use our local ffmpeg.exe
FFMPEG_PATH = Path(__file__).parent / "ffmpeg.exe"
if FFMPEG_PATH.exists():
    try:
        from pydub import AudioSegment
        AudioSegment.converter = str(FFMPEG_PATH)
        os.environ["PATH"] = str(FFMPEG_PATH.parent) + os.pathsep + os.environ.get("PATH", "")
    except Exception as e:
        logger.debug(f"Pydub ffmpeg init note: {e}")


class CaptchaSolver:
    """
    Automated solver for Google reCAPTCHA v2 / Enterprise challenges ('I'm not a robot' & Unusual Traffic).
    Supports:
    1. 2Captcha API with data-s / enterprise support for Google Search /sorry/index.
    2. CapSolver API.
    3. Free Audio AI Challenge Solver (SpeechRecognition via Google Speech-to-Text).
    """

    @classmethod
    async def is_captcha_present(cls, page: Page) -> bool:
        """Checks if an actual reCAPTCHA challenge or unusual traffic block exists on the page."""
        try:
            return await page.evaluate("""() => {
                // If input area is present on /app, the session is authenticated and NOT blocked
                const hasInput = !!document.querySelector("div[contenteditable='true'], textarea, rich-textarea");
                if (hasInput && window.location.pathname.startsWith('/app')) {
                    return false;
                }
                const text = (document.body ? document.body.innerText : '').toLowerCase();
                const hasUnusual = text.includes('unusual traffic') || text.includes("i'm not a robot") || text.includes('detected unusual traffic') || text.includes('to continue, please type the characters');
                const isSorryPage = window.location.href.includes('/sorry/');
                
                // Only consider recaptcha iframes that are VISIBLE challenges (not invisible telemetry tokens)
                let hasVisibleChallenge = false;
                const iframes = document.querySelectorAll('iframe[src*="recaptcha"]');
                for (const f of iframes) {
                    const rect = f.getBoundingClientRect();
                    if (rect.width > 50 && rect.height > 50) {
                        hasVisibleChallenge = true;
                        break;
                    }
                }
                return isSorryPage || hasUnusual || hasVisibleChallenge;
            }""")
        except Exception:
            return False

    @classmethod
    async def handle_page_captcha(cls, page: Page, account_id: str, settings: Dict[str, Any]) -> bool:
        """
        Coordinates full handling of Google reCAPTCHA:
        1. Checks if checkbox is present and unclicked.
        2. Clicks checkbox.
        3. If solved directly (green checkmark), returns True.
        4. If audio/image challenge popup appears, executes 2Captcha, CapSolver, or Free Audio solver.
        """
        logger.info(f"[{account_id}] [reCAPTCHA] Checking page for reCAPTCHA challenge...")

        provider = settings.get("captcha_provider", "free_audio").lower()
        api_key = settings.get("captcha_api_key", "").strip()

        # Step 1: Find reCAPTCHA anchor iframe
        anchor_frame = page.frame_locator("iframe[src*='recaptcha/api2/anchor'], iframe[src*='recaptcha/enterprise/anchor']").first
        bframe = page.frame_locator("iframe[src*='recaptcha/api2/bframe'], iframe[src*='recaptcha/enterprise/bframe']").first

        # If anchor checkbox exists, click it first
        try:
            checkbox = anchor_frame.locator("#recaptcha-anchor, .recaptcha-checkbox").first
            if await checkbox.count() > 0:
                is_checked = await checkbox.get_attribute("aria-checked")
                if is_checked != "true":
                    logger.info(f"[{account_id}] [reCAPTCHA] Clicking 'I'm not a robot' checkbox...")
                    await checkbox.scroll_into_view_if_needed()
                    await asyncio.sleep(0.5)
                    await checkbox.click(delay=120)
                    await asyncio.sleep(2.5)

                # Check if checkbox was resolved without challenge
                is_checked = await checkbox.get_attribute("aria-checked")
                if is_checked == "true":
                    logger.info(f"[{account_id}] [reCAPTCHA] Checkbox passed immediately without challenge!")
                    # If this is /sorry/ index form, submit it
                    await cls._submit_page_form(page)
                    await asyncio.sleep(2.0)
                    return True
        except Exception as e:
            logger.debug(f"[{account_id}] [reCAPTCHA] Checkbox click step note: {e}")

        # Step 2: Route solver according to user configuration
        if provider == "2captcha" and api_key:
            solved = await cls.solve_2captcha(page, account_id, api_key)
            if solved:
                return True
        elif provider == "capsolver" and api_key:
            solved = await cls.solve_capsolver(page, account_id, api_key)
            if solved:
                return True

        # Fallback to Free Audio Challenge
        try:
            audio_btn = bframe.locator("#recaptcha-audio-button, button#recaptcha-audio-button").first
            if await audio_btn.count() > 0 and await audio_btn.is_visible():
                logger.info(f"[{account_id}] [reCAPTCHA] Audio challenge button found.")
                return await cls.solve_audio_challenge(page, bframe, account_id)
        except Exception as e:
            logger.debug(f"[{account_id}] [reCAPTCHA] Audio button check note: {e}")

        # Final verification: check if 'unusual traffic' has gone
        await asyncio.sleep(2.0)
        still_present = await cls.is_captcha_present(page)
        return not still_present

    @classmethod
    async def solve_audio_challenge(cls, page: Page, bframe: FrameLocator, account_id: str) -> bool:
        """Solves Google reCAPTCHA audio challenge completely for free using Google's Speech-to-Text engine."""
        try:
            audio_btn = bframe.locator("#recaptcha-audio-button, button#recaptcha-audio-button").first
            await audio_btn.scroll_into_view_if_needed()
            await asyncio.sleep(0.4)
            await audio_btn.click(delay=100)
            await asyncio.sleep(2.0)

            error_notice = bframe.locator(".rc-doscaptcha-body-text, .rc-audiochallenge-error-message").first
            if await error_notice.count() > 0 and await error_notice.is_visible():
                notice_text = await error_notice.inner_text()
                logger.warning(f"[{account_id}] [reCAPTCHA] Google blocked audio challenge: '{notice_text.strip()}'")
                return False

            download_link = bframe.locator(".rc-audiochallenge-tdownload-link, a[href*='audio.mp3'], a.rc-audiochallenge-tdownload-link").first
            if await download_link.count() == 0:
                logger.warning(f"[{account_id}] [reCAPTCHA] Could not locate audio download link.")
                return False

            audio_url = await download_link.get_attribute("href")
            if not audio_url:
                logger.warning(f"[{account_id}] [reCAPTCHA] Empty audio URL.")
                return False

            logger.info(f"[{account_id}] [reCAPTCHA] Downloading challenge audio from: {audio_url[:60]}...")

            with tempfile.TemporaryDirectory() as tmp_dir:
                mp3_path = Path(tmp_dir) / "challenge.mp3"
                wav_path = Path(tmp_dir) / "challenge.wav"

                urllib.request.urlretrieve(audio_url, mp3_path)

                from pydub import AudioSegment
                import speech_recognition as sr

                sound = AudioSegment.from_mp3(str(mp3_path))
                sound.export(str(wav_path), format="wav")

                recognizer = sr.Recognizer()
                with sr.AudioFile(str(wav_path)) as source:
                    audio_data = recognizer.record(source)

                transcription = recognizer.recognize_google(audio_data)
                logger.info(f"[{account_id}] [reCAPTCHA] Transcribed audio challenge: '{transcription}'")

                resp_input = bframe.locator("#audio-response, input#audio-response").first
                await resp_input.scroll_into_view_if_needed()
                await resp_input.click()
                await asyncio.sleep(0.3)
                await resp_input.fill(transcription)
                await asyncio.sleep(0.5)

                verify_btn = bframe.locator("#recaptcha-verify-button, button#recaptcha-verify-button").first
                await verify_btn.click(delay=80)
                logger.info(f"[{account_id}] [reCAPTCHA] Clicked 'VERIFY' button.")
                await asyncio.sleep(2.5)

                anchor_frame = page.frame_locator("iframe[src*='recaptcha/api2/anchor'], iframe[src*='recaptcha/enterprise/anchor']").first
                checkbox = anchor_frame.locator("#recaptcha-anchor, .recaptcha-checkbox").first
                if await checkbox.count() > 0:
                    is_checked = await checkbox.get_attribute("aria-checked")
                    if is_checked == "true":
                        logger.info(f"[{account_id}] [reCAPTCHA] Audio solve verified successful! (aria-checked=true)")
                        await cls._submit_page_form(page)
                        return True

                still_present = await cls.is_captcha_present(page)
                if not still_present:
                    logger.info(f"[{account_id}] [reCAPTCHA] Challenge resolved successfully.")
                    return True

        except Exception as e:
            logger.error(f"[{account_id}] [reCAPTCHA] Error during audio challenge solve: {e}")

        return False

    @classmethod
    async def solve_capsolver(cls, page: Page, account_id: str, api_key: str) -> bool:
        """Solves reCAPTCHA v2/Enterprise using CapSolver API."""
        try:
            import httpx
            logger.info(f"[{account_id}] [reCAPTCHA] Calling CapSolver API...")
            details = await cls._extract_captcha_dom_details(page)

            task_type = "ReCaptchaV2EnterpriseTaskProxyLess" if details["is_enterprise"] else "ReCaptchaV2TaskProxyLess"
            task_dict = {
                "type": task_type,
                "websiteURL": page.url,
                "websiteKey": details["sitekey"],
            }
            if details.get("data_s"):
                task_dict["enterprisePayload"] = {"s": details["data_s"]}

            payload = {
                "clientKey": api_key,
                "task": task_dict
            }
            async with httpx.AsyncClient(timeout=60.0) as client:
                res = await client.post("https://api.capsolver.com/createTask", json=payload)
                task_data = res.json()
                task_id = task_data.get("taskId")
                if not task_id:
                    logger.error(f"[{account_id}] CapSolver task creation failed: {task_data}")
                    return False

                for _ in range(30):
                    await asyncio.sleep(2.0)
                    check_res = await client.post("https://api.capsolver.com/getTaskResult", json={"clientKey": api_key, "taskId": task_id})
                    res_json = check_res.json()
                    if res_json.get("status") == "ready":
                        token = res_json.get("solution", {}).get("gRecaptchaResponse")
                        if token:
                            await cls._inject_token(page, token)
                            return True
                    elif res_json.get("status") == "failed":
                        logger.error(f"[{account_id}] CapSolver failed: {res_json}")
                        return False

        except Exception as e:
            logger.error(f"[{account_id}] CapSolver error: {e}")
        return False

    @classmethod
    async def solve_2captcha(cls, page: Page, account_id: str, api_key: str) -> bool:
        """Solves reCAPTCHA v2 / Enterprise with data-s parameter using 2Captcha API."""
        try:
            import httpx
            logger.info(f"[{account_id}] [reCAPTCHA] Inspecting page details for 2Captcha...")
            details = await cls._extract_captcha_dom_details(page)
            
            site_key = details["sitekey"] or "6LfwuyUTAAAAAOAmoS0fdqijC2PbbdH4kjq62Y1b"
            data_s = details.get("data_s", "")
            is_enterprise = details.get("is_enterprise", False)
            
            logger.info(f"[{account_id}] [2Captcha] Submitting task: sitekey={site_key[:15]}..., enterprise={is_enterprise}, data-s={bool(data_s)}...")

            submit_params = {
                "key": api_key,
                "method": "userrecaptcha",
                "googlekey": site_key,
                "pageurl": page.url,
                "json": 1
            }
            if data_s:
                submit_params["data-s"] = data_s
            if is_enterprise:
                submit_params["enterprise"] = 1

            async with httpx.AsyncClient(timeout=60.0) as client:
                in_res = await client.get("https://2captcha.com/in.php", params=submit_params)
                in_data = in_res.json()
                if in_data.get("status") != 1:
                    logger.error(f"[{account_id}] 2Captcha submission failed: {in_data}")
                    return False
                request_id = in_data.get("request")
                logger.info(f"[{account_id}] [2Captcha] Task accepted (ID: {request_id}). Waiting for solution...")

                for attempt in range(35):
                    await asyncio.sleep(3.0)
                    out_res = await client.get(f"https://2captcha.com/res.php?key={api_key}&action=get&id={request_id}&json=1")
                    out_data = out_res.json()
                    if out_data.get("status") == 1:
                        token = out_data.get("request")
                        if token:
                            logger.info(f"[{account_id}] [2Captcha] Solution received ({len(token)} chars)! Injecting into DOM...")
                            await cls._inject_token(page, token)
                            await asyncio.sleep(2.0)
                            return True
                    elif out_data.get("request") != "CAPCHA_NOT_READY":
                        logger.error(f"[{account_id}] 2Captcha error: {out_data}")
                        return False

        except Exception as e:
            logger.error(f"[{account_id}] 2Captcha error: {e}")
        return False

    @classmethod
    async def _extract_captcha_dom_details(cls, page: Page) -> Dict[str, Any]:
        """Extracts sitekey, data-s, enterprise flag, and form action from the live page."""
        return await page.evaluate(r"""() => {
            let sitekey = '';
            let data_s = '';
            let is_enterprise = false;

            const el = document.querySelector('[data-sitekey]');
            if (el) {
                sitekey = el.getAttribute('data-sitekey') || '';
                data_s = el.getAttribute('data-s') || '';
            }

            const iframes = Array.from(document.querySelectorAll('iframe[src*="recaptcha"]'));
            for (const f of iframes) {
                const src = f.getAttribute('src') || '';
                const mKey = src.match(/[?&]k=([^&]+)/);
                if (mKey && !sitekey) sitekey = mKey[1];
                const mS = src.match(/[?&]s=([^&]+)/);
                if (mS && !data_s) data_s = decodeURIComponent(mS[1]);
                if (src.includes('enterprise')) is_enterprise = true;
            }

            return {
                sitekey: sitekey || '6LfwuyUTAAAAAOAmoS0fdqijC2PbbdH4kjq62Y1b',
                data_s: data_s,
                is_enterprise: is_enterprise
            };
        }""")

    @classmethod
    async def _submit_page_form(cls, page: Page):
        """Finds and submits any Google /sorry/ verification form on page."""
        try:
            await page.evaluate(r"""() => {
                const form = document.querySelector('form');
                if (form) {
                    form.submit();
                }
            }""")
        except Exception:
            pass

    @classmethod
    async def _inject_token(cls, page: Page, token: str):
        """Injects solved reCAPTCHA response token into g-recaptcha-response textarea and submits form."""
        await page.evaluate(r"""(token) => {
            const textareas = document.querySelectorAll('#g-recaptcha-response, [name="g-recaptcha-response"]');
            textareas.forEach(el => {
                el.innerHTML = token;
                el.value = token;
                el.style.display = 'block';
            });

            // Trigger callbacks in grecaptcha configuration
            if (typeof ___grecaptcha_cfg !== 'undefined' && ___grecaptcha_cfg.clients) {
                for (const cid in ___grecaptcha_cfg.clients) {
                    const client = ___grecaptcha_cfg.clients[cid];
                    for (const key in client) {
                        if (client[key] && typeof client[key].callback === 'function') {
                            try { client[key].callback(token); } catch (e) {}
                        }
                    }
                }
            }

            // Submit form on /sorry/ index page
            const form = document.querySelector('form');
            if (form) {
                form.submit();
            }
        }""", token)
        await asyncio.sleep(2.5)
