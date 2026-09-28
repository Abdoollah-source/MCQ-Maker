#!/usr/bin/env python3
"""
Playwright AI Studio Probe - Feasibility Prototype

Modes:
  login    - Open persistent browser, manual Google sign-in, preserve session
  check    - Reopen profile, verify auth persistence, navigate to AI Studio
  inspect  - Open AI Studio, keep browser open for manual DevTools inspection
  run      - Upload PDF, start generation, wait for completion, extract response
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# Add parent dir to path for config import
sys.path.insert(0, str(Path(__file__).parent))
import config


def print_header(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def print_step(step: str, status: str = "INFO"):
    prefix = {"INFO": "→", "OK": "✓", "FAIL": "✗", "WARN": "⚠"}.get(status, "→")
    print(f"  {prefix} {step}")


def print_result(label: str, result: str):
    print(f"  {label}: {result}")


async def find_element(page, selectors: list, timeout: int = 5000):
    """Try multiple selectors, return first matching element or None."""
    for sel in selectors:
        try:
            locator = page.locator(sel).first
            if await locator.is_visible(timeout=timeout):
                return locator, sel
        except Exception:
            continue
    return None, None


async def find_all_elements(page, selectors: list):
    """Find all elements matching any selector."""
    for sel in selectors:
        try:
            locator = page.locator(sel)
            count = await locator.count()
            if count > 0:
                return locator, sel, count
        except Exception:
            continue
    return None, None, 0


async def detect_generation_state(page) -> tuple[str, str]:
    """
    Detect current generation state by checking Run/Stop button.
    Returns (state, selector_used)
    """
    # Check for Stop button (generating)
    stop_locator, stop_sel = await find_element(page, config.SELECTORS["stop_button"], timeout=2000)
    if stop_locator:
        return config.GenerationState.GENERATING, stop_sel

    # Check for Run button (idle)
    run_locator, run_sel = await find_element(page, config.SELECTORS["run_button"], timeout=2000)
    if run_locator:
        return config.GenerationState.IDLE, run_sel

    # Check generic run/stop button
    generic_locator, generic_sel = await find_element(page, config.SELECTORS["run_or_stop_button"], timeout=2000)
    if generic_locator:
        aria_label = await generic_locator.get_attribute("aria-label") or ""
        if "stop" in aria_label.lower():
            return config.GenerationState.GENERATING, generic_sel
        return config.GenerationState.IDLE, generic_sel

    return config.GenerationState.UNKNOWN, ""


async def upload_file(page, pdf_path: str) -> tuple[bool, str]:
    """
    Try both upload mechanisms:
    1. Direct input[type='file'] with set_input_files() (works on hidden inputs)
    2. Click Add Media button + expect_file_chooser() + set_files()
    Returns (success, mechanism_used)
    """
    pdf_path = Path(pdf_path).resolve()
    if not pdf_path.exists():
        print_step(f"PDF not found: {pdf_path}", "FAIL")
        return False, "file_not_found"

    print_step(f"Attempting upload: {pdf_path.name}")

    # Mechanism 1: Direct file input (does NOT require visibility)
    print_step("Trying mechanism 1: direct input[type='file']")
    for input_sel in config.SELECTORS["file_input"]:
        try:
            file_inputs = page.locator(input_sel)
            count = await file_inputs.count()
            if count > 0:
                print_step(f"Found {count} file input(s) via {input_sel}")
                # Try each input until one works
                for i in range(count):
                    try:
                        await file_inputs.nth(i).set_input_files(str(pdf_path))
                        await page.wait_for_timeout(2000)
                        print_step(f"Upload via direct input succeeded (selector: {input_sel}, index: {i})", "OK")
                        return True, f"direct_input:{input_sel}[{i}]"
                    except Exception as e:
                        idx = i
                        print_step(f"Input[{idx}] set_input_files failed: {e}", "WARN")
                        continue
        except Exception as e:
            print_step(f"Selector {input_sel} error: {e}", "WARN")
            continue

    # Mechanism 2: File chooser via Add Media button
    print_step("Trying mechanism 2: file chooser via Add Media button")
    for trigger_sel in config.SELECTORS["file_chooser_trigger"]:
        try:
            trigger = page.locator(trigger_sel).first
            if await trigger.is_visible(timeout=3000):
                async with page.expect_file_chooser(timeout=config.FILE_UPLOAD_TIMEOUT_MS) as fc_info:
                    await trigger.click()
                file_chooser = await fc_info.value
                await file_chooser.set_files(str(pdf_path))
                await page.wait_for_timeout(3000)
                print_step(f"Upload via file chooser succeeded (trigger: {trigger_sel})", "OK")
                return True, f"file_chooser:{trigger_sel}"
        except Exception as e:
            print_step(f"Trigger {trigger_sel} failed: {e}", "WARN")
            continue

    print_step("All upload mechanisms failed", "FAIL")
    return False, "none"


async def click_run(page) -> tuple[bool, str]:
    """Click the Run button. Returns (success, selector_used)."""
    print_step("Looking for Run button...")
    run_btn, run_sel = await find_element(page, config.SELECTORS["run_button"], timeout=config.UI_ACTION_TIMEOUT_MS)
    if not run_btn:
        print_step("Run button not found", "FAIL")
        return False, ""

    try:
        aria_label = await run_btn.get_attribute("aria-label") or ""
        print_step(f"Found Run button: {aria_label} (selector: {run_sel})")
        await run_btn.click()
        await page.wait_for_timeout(1000)
        return True, run_sel
    except Exception as e:
        print_step(f"Click failed: {e}", "FAIL")
        return False, ""


async def wait_for_generation_complete(page) -> tuple[bool, str]:
    """
    Wait for generation to complete by detecting:
    1. Initial state: Run button (idle)
    2. After click: Stop button appears (generating) - short timeout
    3. Final state: Run button returns (complete) - long timeout
    """
    print_step("Waiting for generation to start (Stop button)...")
    
    # Phase 1: Wait for Stop button to appear (generation started) - SHORT timeout
    start_time = asyncio.get_event_loop().time()
    while (asyncio.get_event_loop().time() - start_time) * 1000 < config.GENERATION_START_TIMEOUT_MS:
        state, sel = await detect_generation_state(page)
        if state == config.GenerationState.GENERATING:
            print_step(f"Generation started (Stop detected via {sel})", "OK")
            break
        await asyncio.sleep(config.POLL_INTERVAL_MS / 1000)
    else:
        print_step(f"Timeout ({config.GENERATION_START_TIMEOUT_MS//1000}s) waiting for generation to start", "FAIL")
        return False, "timeout_start"

    print_step("Waiting for generation to complete (Run button returns)...")
    
    # Phase 2: Wait for Run button to return (generation complete) - LONG timeout
    # Use a fresh start time for the generation phase
    gen_start_time = asyncio.get_event_loop().time()
    while (asyncio.get_event_loop().time() - gen_start_time) * 1000 < config.GENERATION_TIMEOUT_MS:
        state, sel = await detect_generation_state(page)
        if state == config.GenerationState.IDLE:
            print_step(f"Generation complete (Run returned via {sel})", "OK")
            return True, sel
        await asyncio.sleep(config.POLL_INTERVAL_MS / 1000)
    else:
        print_step(f"Timeout ({config.GENERATION_TIMEOUT_MS//60000}min) waiting for generation to complete", "FAIL")
        return False, "timeout_complete"


async def extract_response(page) -> tuple[str | None, str]:
    """Extract the final model response text. Returns (text, selector_used)."""
    print_step("Extracting model response...")

    # Try response containers
    for sel in config.SELECTORS["response_container"]:
        try:
            locator = page.locator(sel)
            count = await locator.count()
            if count > 0:
                last = locator.nth(count - 1)
                text = await last.inner_text(timeout=5000)
                if text and text.strip():
                    print_step(f"Response extracted via {sel} ({len(text)} chars)", "OK")
                    return text.strip(), sel
        except Exception as e:
            continue

    # Fallback: try response_text selectors
    for sel in config.SELECTORS["response_text"]:
        try:
            locator = page.locator(sel).last
            text = await locator.inner_text(timeout=5000)
            if text and text.strip():
                print_step(f"Response extracted via {sel} ({len(text)} chars)", "OK")
                return text.strip(), sel
        except Exception:
            continue

    print_step("No response text found", "FAIL")
    return None, ""


async def save_artifact(response_text: str, pdf_path: str, upload_mechanism: str = "", response_selector: str = "") -> tuple[Path, Path]:
    """Save raw response text and metadata JSON to artifacts directory."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    pdf_name = Path(pdf_path).stem
    base_name = f"{timestamp}_{pdf_name}"
    
    # Raw response text file
    text_path = config.ARTIFACTS_DIR / f"{base_name}_response.txt"
    text_path.write_text(response_text, encoding="utf-8")
    
    # Metadata JSON file
    meta_path = config.ARTIFACTS_DIR / f"{base_name}_meta.json"
    metadata = {
        "timestamp": timestamp,
        "source_pdf": str(pdf_path),
        "upload_mechanism": upload_mechanism,
        "response_selector": response_selector,
        "response_length": len(response_text),
    }
    meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    
    return text_path, meta_path


def _launch_context(playwright, channel: str):
    """Launch persistent context with specified browser channel."""
    launch_args = {
        "user_data_dir": str(config.PROFILE_DIR),
        "headless": False,
        "args": config.BROWSER_ARGS,
    }
    if channel == "chrome":
        launch_args["channel"] = "chrome"
    return playwright.chromium.launch_persistent_context(**launch_args)


async def run_login(playwright, browser_channel: str = "chromium"):
    """Mode: login - Manual sign-in, preserve session."""
    print_header("MODE: LOGIN - Manual Google Sign-In")
    
    print_step(f"Launching persistent browser profile: {config.PROFILE_DIR} (channel: {browser_channel})")
    context = await _launch_context(playwright, browser_channel)
    
    page = await context.new_page()
    
    print_step("Navigating to Google Sign-In...")
    await page.goto(config.GOOGLE_SIGNIN_URL, wait_until="domcontentloaded", timeout=config.NAVIGATION_TIMEOUT_MS)
    
    print_step("Please sign in to Google manually in the browser window.")
    print_step("After signing in, navigate to AI Studio to verify access.")
    print_step("Close the browser window when done. Session will be saved.")
    
    # Wait for browser to close
    await context.wait_for_event("close")
    
    print_step("Browser closed. Profile saved.", "OK")
    return True


async def run_check(playwright, browser_channel: str = "chromium"):
    """Mode: check - Verify auth persistence."""
    print_header("MODE: CHECK - Verify Auth Persistence")
    
    if not config.PROFILE_DIR.exists():
        print_step("Profile directory not found. Run 'login' first.", "FAIL")
        return False
    
    print_step(f"Reopening persistent profile: {config.PROFILE_DIR} (channel: {browser_channel})")
    context = await _launch_context(playwright, browser_channel)
    
    page = await context.new_page()
    
    print_step(f"Navigating to AI Studio: {config.AI_STUDIO_URL}")
    await page.goto(config.AI_STUDIO_URL, wait_until="domcontentloaded", timeout=config.NAVIGATION_TIMEOUT_MS)
    
    await page.wait_for_timeout(3000)
    
    # Check if redirected to sign-in
    current_url = page.url
    if "accounts.google.com" in current_url:
        print_step("Redirected to Google Sign-In - session NOT persisted", "FAIL")
        print_result("Login Persistence", "FAIL")
        await context.close()
        return False
    
    # Check for user avatar / signed-in indicators
    avatar, avatar_sel = await find_element(page, config.SELECTORS["user_avatar"], timeout=5000)
    if avatar:
        print_step(f"User avatar found (selector: {avatar_sel}) - session appears active", "OK")
        print_result("Login Persistence", "PASS")
    else:
        print_step("No user avatar detected - session state uncertain", "WARN")
        print_result("Login Persistence", "PARTIAL")
    
    # Check for AI Studio UI elements
    print_step("Checking for AI Studio UI...")
    prompt_area, _ = await find_element(page, config.SELECTORS["prompt_textarea"], timeout=5000)
    if prompt_area:
        print_step("Prompt input area found - AI Studio loaded", "OK")
        print_result("AI Studio Navigation", "PASS")
    else:
        print_step("Prompt input area not found", "WARN")
        print_result("AI Studio Navigation", "PARTIAL")
    
    print_step("Leaving browser open for manual verification. Close when done.")
    await context.wait_for_event("close")
    return True


async def run_inspect(playwright, browser_channel: str = "chromium"):
    """Mode: inspect - Open AI Studio for manual DevTools inspection."""
    print_header("MODE: INSPECT - Manual Element Discovery")
    
    if not config.PROFILE_DIR.exists():
        print_step("Profile directory not found. Run 'login' first.", "FAIL")
        return False
    
    print_step(f"Opening AI Studio with persistent profile (channel: {browser_channel})...")
    context = await _launch_context(playwright, browser_channel)
    
    page = await context.new_page()
    await page.goto(config.AI_STUDIO_URL, wait_until="domcontentloaded", timeout=config.NAVIGATION_TIMEOUT_MS)
    await page.wait_for_timeout(3000)
    
    print_step("Browser opened. Use DevTools (F12) to inspect elements.")
    print_step("Discovering accessible selectors automatically...")
    
    # Auto-discover and report candidate selectors
    print("\n  Discovered Elements:")
    
    # File upload triggers
    for name, selectors in [
        ("Add Media Button", config.SELECTORS["add_media_button"]),
        ("File Input (direct)", config.SELECTORS["file_input"]),
    ]:
        locator, sel, count = await find_all_elements(page, selectors)
        if count > 0:
            for i in range(min(count, 3)):
                try:
                    el = locator.nth(i)
                    aria = await el.get_attribute("aria-label") or ""
                    role = await el.get_attribute("role") or ""
                    tag = await el.evaluate("e => e.tagName.toLowerCase()")
                    print(f"    {name} [{i}]: {tag} role={role} aria-label='{aria}' (selector: {sel})")
                except Exception:
                    pass
        else:
            print(f"    {name}: NOT FOUND")
    
    # Run/Stop buttons
    run_btn, run_sel = await find_element(page, config.SELECTORS["run_button"], timeout=3000)
    stop_btn, stop_sel = await find_element(page, config.SELECTORS["stop_button"], timeout=3000)
    if run_btn:
        aria = await run_btn.get_attribute("aria-label") or ""
        print(f"    Run Button: aria-label='{aria}' (selector: {run_sel})")
    if stop_btn:
        aria = await stop_btn.get_attribute("aria-label") or ""
        print(f"    Stop Button: aria-label='{aria}' (selector: {stop_sel})")
    
    # Response containers
    print("\n  Response Containers (candidates):")
    for sel in config.SELECTORS["response_container"]:
        locator = page.locator(sel)
        count = await locator.count()
        if count > 0:
            print(f"    {sel}: {count} elements")
            # Show first one's structure
            try:
                first = locator.first
                html = await first.evaluate("e => e.outerHTML.substring(0, 200)")
                print(f"      Sample: {html}...")
            except Exception:
                pass
    
    print_step("\nBrowser will stay open. Close when inspection complete.")
    await context.wait_for_event("close")
    return True


async def run_generation(playwright, pdf_path: str, browser_channel: str = "chromium"):
    """Mode: run - Upload PDF, generate, extract response."""
    print_header(f"MODE: RUN - Full Generation Test")
    print_step(f"PDF: {pdf_path}")
    
    if not config.PROFILE_DIR.exists():
        print_step("Profile directory not found. Run 'login' first.", "FAIL")
        return False
    
    if not Path(pdf_path).exists():
        print_step(f"PDF not found: {pdf_path}", "FAIL")
        return False
    
    print_step(f"Opening AI Studio with persistent profile (channel: {browser_channel})...")
    context = await _launch_context(playwright, browser_channel)
    
    page = await context.new_page()
    
    # Navigate
    print_step(f"Navigating to AI Studio...")
    await page.goto(config.AI_STUDIO_URL, wait_until="domcontentloaded", timeout=config.NAVIGATION_TIMEOUT_MS)
    await page.wait_for_timeout(3000)
    
    # Verify not redirected to sign-in
    if "accounts.google.com" in page.url:
        print_step("Redirected to sign-in - session expired", "FAIL")
        print_result("Login Persistence", "FAIL")
        await context.close()
        return False
    
    print_result("Login Persistence", "PASS")
    print_result("AI Studio Navigation", "PASS")
    
    # Upload PDF
    upload_ok, upload_mechanism = await upload_file(page, pdf_path)
    print_result("PDF Upload", "PASS" if upload_ok else "FAIL")
    
    if not upload_ok:
        print_step("Upload failed. Browser left open for manual upload.", "WARN")
        await context.wait_for_event("close")
        return False
    
    # Click Run
    run_ok, run_sel = await click_run(page)
    print_result("Run Control", "PASS" if run_ok else "FAIL")
    
    if not run_ok:
        await context.wait_for_event("close")
        return False
    
    # Wait for completion
    complete_ok, complete_sel = await wait_for_generation_complete(page)
    print_result("Completion Detection", "PASS" if complete_ok else "FAIL")
    
    if not complete_ok:
        await context.wait_for_event("close")
        return False
    
    # Extract response
    response_text, response_sel = await extract_response(page)
    print_result("Response Extraction", "PASS" if response_text else "FAIL")
    
    if response_text:
        text_path, meta_path = await save_artifact(response_text, pdf_path, upload_mechanism, response_sel)
        print_step(f"Raw response saved to: {text_path}", "OK")
        print_step(f"Metadata saved to: {meta_path}", "OK")
        print(f"\n  Response preview (first 500 chars):")
        print(f"  {response_text[:500]}...")
    else:
        print_step("No response extracted. Browser left open for inspection.", "WARN")
    
    print_step("Generation test complete. Close browser when ready.")
    await context.wait_for_event("close")
    return True


async def run_attach(playwright, cdp_url: str = "http://127.0.0.1:9222"):
    """Mode: attach - Connect to running Chrome via CDP, list pages, detect AI Studio."""
    print_header("MODE: ATTACH - Connect to Running Chrome via CDP")
    
    print_step(f"Connecting to CDP endpoint: {cdp_url}")
    
    cdp_ok = False
    context_ok = False
    aistudio_ok = False
    
    try:
        browser = await playwright.chromium.connect_over_cdp(cdp_url)
        cdp_ok = True
        print_result("CDP Connection", "PASS")
    except Exception as e:
        print_step(f"CDP connection failed: {e}", "FAIL")
        print_result("CDP Connection", "FAIL")
        return False
    
    try:
        # Get the default browser context (first context)
        contexts = browser.contexts
        if contexts:
            context = contexts[0]
            context_ok = True
            print_result("Existing Browser Context", "PASS")
            print_step(f"Found {len(contexts)} context(s)")
        else:
            print_step("No existing browser contexts found", "FAIL")
            print_result("Existing Browser Context", "FAIL")
            await browser.close()
            return False
    except Exception as e:
        print_step(f"Failed to access browser context: {e}", "FAIL")
        print_result("Existing Browser Context", "FAIL")
        await browser.close()
        return False
    
    try:
        pages = context.pages
        print_step(f"Found {len(pages)} open page(s):")
        
        for i, page in enumerate(pages):
            try:
                title = await page.title()
                url = page.url
                print(f"  [{i}] {title[:80]}")
                print(f"      {url}")
                
                if "aistudio.google.com" in url:
                    aistudio_ok = True
                    print(f"      *** AI STUDIO TAB FOUND ***")
            except Exception as e:
                print(f"  [{i}] <error reading page: {e}>")
        
        if aistudio_ok:
            print_result("AI Studio Tab Found", "PASS")
        else:
            print_result("AI Studio Tab Found", "FAIL")
            print_step("No page URL contains 'aistudio.google.com'")
        
    except Exception as e:
        print_step(f"Failed to enumerate pages: {e}", "FAIL")
        print_result("AI Studio Tab Found", "FAIL")
    
    # Don't close the browser - user owns it
    print_step("Leaving Chrome running (attach mode does not close browser).")
    return cdp_ok and context_ok


async def run_inspect_attached(playwright, cdp_url: str = "http://127.0.0.1:9222"):
    """Mode: inspect-attached - Connect to running Chrome via CDP, find AI Studio tab, inspect DOM read-only."""
    print_header("MODE: INSPECT-ATTACHED - Read-Only AI Studio DOM Inspection via CDP")
    
    print_step(f"Connecting to CDP endpoint: {cdp_url}")
    
    browser = None
    try:
        browser = await playwright.chromium.connect_over_cdp(cdp_url)
        print_result("CDP Connection", "PASS")
    except Exception as e:
        print_step(f"CDP connection failed: {e}", "FAIL")
        print_result("CDP Connection", "FAIL")
        return False
    
    # Find AI Studio tab
    aistudio_page = None
    try:
        contexts = browser.contexts
        if not contexts:
            print_step("No existing browser contexts found", "FAIL")
            print_result("Existing Browser Context", "FAIL")
            return False
        
        context = contexts[0]
        print_result("Existing Browser Context", "PASS")
        print_step(f"Found {len(contexts)} context(s)")
        
        for page in context.pages:
            try:
                url = page.url
                if "aistudio.google.com" in url:
                    aistudio_page = page
                    title = await page.title()
                    print_step(f"AI Studio tab found: {title[:80]}")
                    print_step(f"URL: {url}")
                    break
            except Exception:
                continue
        
        if not aistudio_page:
            print_step("No AI Studio tab found among open pages", "FAIL")
            print_result("AI Studio Tab Found", "FAIL")
            return False
        
        print_result("AI Studio Tab Found", "PASS")
        
    except Exception as e:
        print_step(f"Failed to find AI Studio tab: {e}", "FAIL")
        return False
    
    # Inspect the AI Studio page (read-only)
    page = aistudio_page
    report = {
        "timestamp": datetime.now().isoformat(),
        "url": page.url,
        "title": await page.title(),
        "elements": {}
    }
    
    print_step("Inspecting AI Studio DOM (read-only)...")
    
    # Helper to collect element info
    async def inspect_locator(locator, name: str, max_count: int = 5):
        """Inspect locator elements and return list of dicts with attributes."""
        results = []
        try:
            count = await locator.count()
            for i in range(min(count, max_count)):
                try:
                    el = locator.nth(i)
                    info = {
                        "index": i,
                        "tag": await el.evaluate("e => e.tagName.toLowerCase()"),
                        "role": await el.get_attribute("role"),
                        "aria_label": await el.get_attribute("aria-label"),
                        "aria_placeholder": await el.get_attribute("aria-placeholder"),
                        "placeholder": await el.get_attribute("placeholder"),
                        "accept": await el.get_attribute("accept"),
                        "type": await el.get_attribute("type"),
                        "data_testid": await el.get_attribute("data-testid"),
                        "data_attributes": {},
                        "visible_text": "",
                        "is_visible": await el.is_visible(timeout=1000),
                    }
                    # Collect all data-* attributes
                    data_attrs = await el.evaluate("""e => {
                        const attrs = {};
                        for (const attr of e.attributes) {
                            if (attr.name.startsWith('data-')) {
                                attrs[attr.name] = attr.value;
                            }
                        }
                        return attrs;
                    }""")
                    info["data_attributes"] = data_attrs
                    
                    # Get visible text for buttons/links (truncated)
                    if info["tag"] in ("button", "a", "span", "div"):
                        try:
                            text = await el.inner_text(timeout=500)
                            if text and text.strip():
                                info["visible_text"] = text.strip()[:200]
                        except Exception:
                            pass
                    
                    results.append(info)
                except Exception as e:
                    results.append({"index": i, "error": str(e)})
        except Exception as e:
            results.append({"error": f"Locator error: {e}"})
        return results
    
    # 1. Run button candidates
    print_step("  Scanning for Run button candidates...")
    report["elements"]["run_button"] = []
    for sel in config.SELECTORS["run_button"]:
        locator = page.locator(sel)
        items = await inspect_locator(locator, "run_button")
        if items:
            for item in items:
                item["selector"] = sel
            report["elements"]["run_button"].extend(items)
    
    # 2. Stop / generation-state control candidates
    print_step("  Scanning for Stop/generation-state control candidates...")
    report["elements"]["stop_button"] = []
    for sel in config.SELECTORS["stop_button"]:
        locator = page.locator(sel)
        items = await inspect_locator(locator, "stop_button")
        if items:
            for item in items:
                item["selector"] = sel
            report["elements"]["stop_button"].extend(items)
    
    # Also check generic run/stop
    report["elements"]["run_or_stop_button"] = []
    for sel in config.SELECTORS["run_or_stop_button"]:
        locator = page.locator(sel)
        items = await inspect_locator(locator, "run_or_stop_button")
        if items:
            for item in items:
                item["selector"] = sel
            report["elements"]["run_or_stop_button"].extend(items)
    
    # 3. Add Media / attachment control
    print_step("  Scanning for Add Media / attachment control candidates...")
    report["elements"]["add_media_button"] = []
    for sel in config.SELECTORS["add_media_button"]:
        locator = page.locator(sel)
        items = await inspect_locator(locator, "add_media_button")
        if items:
            for item in items:
                item["selector"] = sel
            report["elements"]["add_media_button"].extend(items)
    
    # 4. File input elements (including hidden)
    print_step("  Scanning for file input elements...")
    report["elements"]["file_input"] = []
    for sel in config.SELECTORS["file_input"]:
        locator = page.locator(sel)
        items = await inspect_locator(locator, "file_input")
        if items:
            for item in items:
                item["selector"] = sel
            report["elements"]["file_input"].extend(items)
    
    # Also scan ALL input[type='file'] on page (not just config selectors)
    print_step("  Scanning for ALL input[type='file'] on page...")
    all_file_inputs = page.locator("input[type='file']")
    items = await inspect_locator(all_file_inputs, "all_file_inputs", max_count=20)
    for item in items:
        item["selector"] = "input[type='file'] (all)"
    report["elements"]["all_file_inputs"] = items
    
    # 5. Prompt/editor input
    print_step("  Scanning for prompt/editor input candidates...")
    report["elements"]["prompt_textarea"] = []
    for sel in config.SELECTORS["prompt_textarea"]:
        locator = page.locator(sel)
        items = await inspect_locator(locator, "prompt_textarea")
        if items:
            for item in items:
                item["selector"] = sel
            report["elements"]["prompt_textarea"].extend(items)
    
    # Also check for contenteditable elements
    print_step("  Scanning for contenteditable elements...")
    contenteditable = page.locator("[contenteditable='true']")
    items = await inspect_locator(contenteditable, "contenteditable", max_count=10)
    for item in items:
        item["selector"] = "[contenteditable='true']"
    report["elements"]["contenteditable"] = items
    
    # 6. Model response containers/messages
    print_step("  Scanning for model response containers...")
    report["elements"]["response_container"] = []
    for sel in config.SELECTORS["response_container"]:
        locator = page.locator(sel)
        items = await inspect_locator(locator, "response_container")
        if items:
            for item in items:
                item["selector"] = sel
            report["elements"]["response_container"].extend(items)
    
    # 7. User avatar / session indicators (for context)
    print_step("  Scanning for session indicators...")
    report["elements"]["user_avatar"] = []
    for sel in config.SELECTORS["user_avatar"]:
        locator = page.locator(sel)
        items = await inspect_locator(locator, "user_avatar")
        if items:
            for item in items:
                item["selector"] = sel
            report["elements"]["user_avatar"].extend(items)
    
    # Print summary
    print_step("\n  === INSPECTION SUMMARY ===")
    for category, elements in report["elements"].items():
        if elements:
            valid = [e for e in elements if "error" not in e]
            print_step(f"    {category}: {len(valid)} candidate(s)")
            for e in valid[:3]:
                attrs = []
                if e.get("aria_label"): attrs.append(f'aria-label="{e["aria_label"]}"')
                if e.get("role"): attrs.append(f'role="{e["role"]}"')
                if e.get("tag"): attrs.append(f'tag={e["tag"]}')
                if e.get("placeholder"): attrs.append(f'placeholder="{e["placeholder"]}"')
                if e.get("accept"): attrs.append(f'accept="{e["accept"]}"')
                if e.get("visible_text"): attrs.append(f'text="{e["visible_text"][:50]}"')
                print(f"      [{e['index']}] {' '.join(attrs)} (selector: {e.get('selector', 'N/A')})")
        else:
            print_step(f"    {category}: 0 candidates")
    
    # Save JSON report
    config.ARTIFACTS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = config.ARTIFACTS_DIR / f"{timestamp}_inspect_attached.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print_step(f"\n  Inspection report saved to: {report_path}", "OK")
    
    # Don't close the browser - user owns it
    print_step("Leaving Chrome running (inspect-attached mode does not close browser).")
    return True


async def run_upload_attached(playwright, pdf_path: str, cdp_url: str = "http://127.0.0.1:9222"):
    """Mode: upload-attached - Connect via CDP, find AI Studio tab, upload PDF to file input, verify acknowledgment."""
    print_header("MODE: UPLOAD-ATTACHED - Upload PDF to AI Studio via CDP")
    
    pdf_path = Path(pdf_path).resolve()
    if not pdf_path.exists():
        print_step(f"PDF not found: {pdf_path}", "FAIL")
        return False
    
    print_step(f"PDF: {pdf_path.name} ({pdf_path.stat().st_size} bytes)")
    print_step(f"Connecting to CDP endpoint: {cdp_url}")
    
    browser = None
    report = {
        "timestamp": datetime.now().isoformat(),
        "pdf_filename": pdf_path.name,
        "pdf_size": pdf_path.stat().st_size,
        "cdp_connection": "FAIL",
        "aistudio_tab": "FAIL",
        "file_input_found": "FAIL",
        "set_input_files": "FAIL",
        "upload_acknowledged": "FAIL",
        "file_input_details": {},
        "acknowledgment_evidence": [],
    }
    
    try:
        browser = await playwright.chromium.connect_over_cdp(cdp_url)
        report["cdp_connection"] = "PASS"
        print_result("CDP Connection", "PASS")
    except Exception as e:
        print_step(f"CDP connection failed: {e}", "FAIL")
        print_result("CDP Connection", "FAIL")
        return False
    
    # Find AI Studio tab
    aistudio_page = None
    try:
        contexts = browser.contexts
        if not contexts:
            print_step("No existing browser contexts found", "FAIL")
            return False
        
        context = contexts[0]
        print_result("Existing Browser Context", "PASS")
        
        for page in context.pages:
            try:
                url = page.url
                if "aistudio.google.com" in url:
                    aistudio_page = page
                    title = await page.title()
                    print_step(f"AI Studio tab found: {title[:80]}")
                    print_step(f"URL: {url}")
                    break
            except Exception:
                continue
        
        if not aistudio_page:
            print_step("No AI Studio tab found among open pages", "FAIL")
            print_result("AI Studio Tab", "FAIL")
            return False
        
        report["aistudio_tab"] = "PASS"
        report["aistudio_url"] = aistudio_page.url
        report["aistudio_title"] = await aistudio_page.title()
        print_result("AI Studio Tab", "PASS")
        
    except Exception as e:
        print_step(f"Failed to find AI Studio tab: {e}", "FAIL")
        return False
    
    page = aistudio_page
    
    # Find the file input
    print_step("Locating file input elements...")
    file_inputs = page.locator("input[type='file']")
    count = await file_inputs.count()
    
    if count == 0:
        print_step("No file input elements found", "FAIL")
        print_result("File Input Found", "FAIL")
        return False
    
    print_step(f"Found {count} file input element(s)", "OK")
    report["file_input_found"] = "PASS"
    report["file_input_count"] = count
    
    # Inspect all file inputs
    for i in range(count):
        el = file_inputs.nth(i)
        accept = await el.get_attribute("accept") or ""
        is_visible = await el.is_visible(timeout=1000)
        tag = await el.evaluate("e => e.tagName.toLowerCase()")
        type_attr = await el.get_attribute("type")
        
        info = {
            "index": i,
            "tag": tag,
            "type": type_attr,
            "accept": accept,
            "is_visible": is_visible,
        }
        report["file_input_details"][f"input[{i}]"] = info
        
        print_step(f"  input[{i}]: tag={tag}, type={type_attr}, visible={is_visible}, accept={accept[:80]}{'...' if len(accept) > 80 else ''}")
    
    # Use the first file input (the one we found)
    file_input = file_inputs.first
    accept_attr = await file_input.get_attribute("accept") or ""
    is_visible = await file_input.is_visible(timeout=1000)
    
    # Attempt set_input_files
    print_step("Attempting set_input_files()...")
    print_result("File Input Found", "PASS")
    print_step(f"  Target input: visible={is_visible}, accept={accept_attr[:80]}{'...' if len(accept_attr) > 80 else ''}")
    
    try:
        await file_input.set_input_files(str(pdf_path))
        report["set_input_files"] = "PASS"
        print_result("set_input_files()", "PASS")
    except Exception as e:
        report["set_input_files"] = "FAIL"
        report["set_input_files_error"] = str(e)
        print_step(f"set_input_files() failed: {e}", "FAIL")
        print_result("set_input_files()", "FAIL")
        return False
    
    # Wait briefly for UI to react
    print_step("Waiting for upload acknowledgment...")
    await page.wait_for_timeout(3000)
    
    # Check for acknowledgment evidence
    evidence = []
    
    # 1. Check if PDF filename appears anywhere in the page
    try:
        filename_visible = await page.locator(f"text={pdf_path.name}").count()
        if filename_visible > 0:
            evidence.append(f"Filename '{pdf_path.name}' found in page text")
    except Exception:
        pass
    
    # 2. Look for attachment chips/cards - common patterns
    attachment_selectors = [
        '[data-testid*="attachment" i]',
        '[data-testid*="chip" i]',
        '[role="button"][aria-label*="remove" i]',
        '[role="button"][aria-label*="delete" i]',
        '.attachment',
        '.chip',
        '[data-filename]',
        '[data-file-name]',
    ]
    for sel in attachment_selectors:
        try:
            count = await page.locator(sel).count()
            if count > 0:
                evidence.append(f"Attachment element found: {sel} (count: {count})")
        except Exception:
            pass
    
    # 3. Check near prompt composer for changes
    try:
        prompt_area = page.locator('textarea[aria-label*="prompt" i]').first
        if await prompt_area.count() > 0:
            # Get surrounding HTML to see if attachment UI appeared
            parent = prompt_area.locator("xpath=..")
            html = await parent.evaluate("e => e.outerHTML")
            if pdf_path.name in html or "attachment" in html.lower() or "chip" in html.lower():
                evidence.append("Prompt composer area shows attachment-related changes")
    except Exception:
        pass
    
    # 4. Check for any element containing the filename
    try:
        all_elements = await page.locator(f"*:has-text('{pdf_path.name}')").count()
        if all_elements > 0:
            evidence.append(f"Filename found in {all_elements} element(s) via text search")
    except Exception:
        pass
    
    # 5. Look for file preview or upload progress indicators
    try:
        progress_selectors = [
            '[role="progressbar"]',
            '.progress',
            '[data-testid*="upload" i]',
            '[data-testid*="progress" i]',
        ]
        for sel in progress_selectors:
            count = await page.locator(sel).count()
            if count > 0:
                evidence.append(f"Upload progress indicator found: {sel}")
    except Exception:
        pass
    
    # Report acknowledgment
    if evidence:
        report["upload_acknowledged"] = "PASS"
        print_result("Upload Acknowledged", "PASS")
        for ev in evidence:
            print_step(f"  Evidence: {ev}", "OK")
    else:
        # Check if file input value changed (though this is tricky for hidden inputs)
        try:
            # Re-check file input - sometimes the value gets cleared after upload
            evidence.append("set_input_files succeeded but no visible acknowledgment detected")
        except Exception:
            pass
        report["upload_acknowledged"] = "PARTIAL"
        print_result("Upload Acknowledged", "PARTIAL")
        print_step("  set_input_files succeeded but no clear UI acknowledgment detected", "WARN")
    
    report["acknowledgment_evidence"] = evidence
    
    # Save JSON report
    config.ARTIFACTS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = config.ARTIFACTS_DIR / f"{timestamp}_upload_attached.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print_step(f"\n  Upload report saved to: {report_path}", "OK")
    
    # Don't close the browser - user owns it
    print_step("Leaving Chrome running (upload-attached mode does not close browser).")
    return True


async def run_start_attached(playwright, cdp_url: str = "http://127.0.0.1:9222"):
    """Mode: start-attached - Connect via CDP, find AI Studio tab, click Run, detect generation start via baseline comparison."""
    print_header("MODE: START-ATTACHED - Click Run and Detect Generation Start via CDP")
    
    print_step(f"Connecting to CDP endpoint: {cdp_url}")
    
    browser = None
    report = {
        "timestamp": datetime.now().isoformat(),
        "cdp_connection": "FAIL",
        "aistudio_tab": "FAIL",
        "run_button_found": "FAIL",
        "run_button_state": {},
        "run_click": "FAIL",
        "generation_started": "FAIL",
        "start_evidence": [],
        "elapsed_ms": 0,
        "selector_used": "",
        "baseline": {},
    }
    
    try:
        browser = await playwright.chromium.connect_over_cdp(cdp_url)
        report["cdp_connection"] = "PASS"
        print_result("CDP Connection", "PASS")
    except Exception as e:
        print_step(f"CDP connection failed: {e}", "FAIL")
        print_result("CDP Connection", "FAIL")
        return False
    
    # Find AI Studio tab
    aistudio_page = None
    try:
        contexts = browser.contexts
        if not contexts:
            print_step("No existing browser contexts found", "FAIL")
            return False
        
        context = contexts[0]
        print_result("Existing Browser Context", "PASS")
        
        for page in context.pages:
            try:
                url = page.url
                if "aistudio.google.com" in url:
                    aistudio_page = page
                    title = await page.title()
                    print_step(f"AI Studio tab found: {title[:80]}")
                    print_step(f"URL: {url}")
                    break
            except Exception:
                continue
        
        if not aistudio_page:
            print_step("No AI Studio tab found among open pages", "FAIL")
            print_result("AI Studio Tab", "FAIL")
            return False
        
        report["aistudio_tab"] = "PASS"
        report["aistudio_url"] = aistudio_page.url
        report["aistudio_title"] = await aistudio_page.title()
        print_result("AI Studio Tab", "PASS")
        
    except Exception as e:
        print_step(f"Failed to find AI Studio tab: {e}", "FAIL")
        return False
    
    page = aistudio_page
    
    # Find Run button - try config selectors first, then fallback
    print_step("Locating Run button...")
    run_button = None
    selector_used = ""
    run_state = {}
    
    # Try config selectors for run button
    for sel in config.SELECTORS["run_button"]:
        locator = page.locator(sel).first
        try:
            if await locator.is_visible(timeout=3000):
                run_button = locator
                selector_used = sel
                break
        except Exception:
            continue
    
    # Fallback: button with "Run" text (observed in inspect-attached)
    if not run_button:
        print_step("Config selectors failed, trying fallback: button:has-text('Run')")
        locator = page.locator("button:has-text('Run')").first
        try:
            if await locator.is_visible(timeout=3000):
                run_button = locator
                selector_used = "button:has-text('Run') (fallback)"
        except Exception:
            pass
    
    # Also check run_or_stop_button selectors
    if not run_button:
        for sel in config.SELECTORS["run_or_stop_button"]:
            locator = page.locator(sel).first
            try:
                if await locator.is_visible(timeout=3000):
                    run_button = locator
                    selector_used = sel
                    break
            except Exception:
                continue
    
    if not run_button:
        print_step("No Run button found", "FAIL")
        print_result("Run Button Found", "FAIL")
        return False
    
    # Record Run button state BEFORE clicking
    print_step(f"Run button found via: {selector_used}")
    report["run_button_found"] = "PASS"
    report["selector_used"] = selector_used
    
    try:
        run_state = {
            "tag": await run_button.evaluate("e => e.tagName.toLowerCase()"),
            "aria_label": await run_button.get_attribute("aria-label"),
            "role": await run_button.get_attribute("role"),
            "disabled": await run_button.get_attribute("disabled"),
            "aria_disabled": await run_button.get_attribute("aria-disabled"),
            "class": await run_button.get_attribute("class"),
            "visible_text": await run_button.inner_text(timeout=1000),
            "is_visible": await run_button.is_visible(timeout=1000),
            "is_enabled": await run_button.is_enabled(timeout=1000),
        }
        report["run_button_state"] = run_state
        print_step(f"  Before click: aria-label='{run_state.get('aria_label')}', text='{run_state.get('visible_text', '')[:50]}', disabled={run_state.get('disabled')}, aria-disabled={run_state.get('aria_disabled')}, enabled={run_state.get('is_enabled')}")
    except Exception as e:
        print_step(f"  Could not fully inspect run button: {e}", "WARN")
    
    # Record BASELINE of candidate generation indicators BEFORE clicking Run
    print_step("Recording baseline of generation indicators...")
    baseline = {}
    
    # 1. Stop button candidates (should be absent before generation)
    baseline["stop_button"] = {}
    for sel in config.SELECTORS["stop_button"]:
        try:
            count = await page.locator(sel).count()
            baseline["stop_button"][sel] = {"count": count, "visible": False}
            if count > 0:
                locator = page.locator(sel).first
                baseline["stop_button"][sel]["visible"] = await locator.is_visible(timeout=500)
        except Exception:
            baseline["stop_button"][sel] = {"count": 0, "visible": False, "error": True}
    
    # 2. Progress/loading indicators
    progress_selectors = [
        '[role="progressbar"]',
        '[role="status"]',
        '.loading',
        '.spinner',
        '[data-testid*="loading" i]',
        '[data-testid*="progress" i]',
        '[data-testid*="generating" i]',
        '[aria-busy="true"]',
    ]
    baseline["progress_indicators"] = {}
    for sel in progress_selectors:
        try:
            count = await page.locator(sel).count()
            visible_count = 0
            if count > 0:
                for i in range(count):
                    if await page.locator(sel).nth(i).is_visible(timeout=300):
                        visible_count += 1
            baseline["progress_indicators"][sel] = {"count": count, "visible_count": visible_count}
        except Exception:
            baseline["progress_indicators"][sel] = {"count": 0, "visible_count": 0, "error": True}
    
    # 3. Generation state text elements (aria-live, data-generation-state, etc.)
    state_selectors = [
        '[aria-live="polite"]',
        '[aria-live="assertive"]',
        '[data-generation-state]',
        '[data-testid*="generation" i]',
        '[data-testid*="status" i]',
    ]
    baseline["state_elements"] = {}
    for sel in state_selectors:
        try:
            count = await page.locator(sel).count()
            texts = []
            if count > 0:
                for i in range(min(count, 5)):
                    try:
                        locator = page.locator(sel).nth(i)
                        if await locator.is_visible(timeout=300):
                            text = await locator.inner_text(timeout=300)
                            if text and text.strip():
                                texts.append(text.strip()[:200])
                    except Exception:
                        pass
            baseline["state_elements"][sel] = {"count": count, "texts": texts}
        except Exception:
            baseline["state_elements"][sel] = {"count": 0, "texts": [], "error": True}
    
    # 4. "Stop" text buttons
    try:
        count = await page.locator("button:has-text('Stop')").count()
        baseline["stop_text_buttons"] = {"count": count}
    except Exception:
        baseline["stop_text_buttons"] = {"count": 0, "error": True}
    
    report["baseline"] = baseline
    print_step("  Baseline recorded.")
    
    # Click Run exactly once
    print_step("Clicking Run button...")
    report["run_click"] = "PASS"
    try:
        await run_button.click()
        print_result("Run Click", "PASS")
    except Exception as e:
        print_step(f"Run click failed: {e}", "FAIL")
        print_result("Run Click", "FAIL")
        report["run_click"] = "FAIL"
        report["run_click_error"] = str(e)
        return False
    
    # Observe for up to 30 seconds for generation start evidence (compare against baseline)
    print_step("Observing for generation start evidence (max 30s, comparing to baseline)...")
    start_time = asyncio.get_event_loop().time()
    max_wait_ms = 30000
    poll_interval = 1000  # 1 second
    generation_started = False
    evidence = []
    
    while (asyncio.get_event_loop().time() - start_time) * 1000 < max_wait_ms:
        elapsed_ms = int((asyncio.get_event_loop().time() - start_time) * 1000)
        
        # 1. Stop button APPEARED that was not present/visible in baseline
        for sel in config.SELECTORS["stop_button"]:
            try:
                count = await page.locator(sel).count()
                if count > 0:
                    locator = page.locator(sel).first
                    if await locator.is_visible(timeout=500):
                        baseline_info = baseline.get("stop_button", {}).get(sel, {})
                        baseline_visible = baseline_info.get("visible", False)
                        baseline_count = baseline_info.get("count", 0)
                        if not baseline_visible or count > baseline_count:
                            aria = await locator.get_attribute("aria-label") or ""
                            evidence.append(f"Stop button appeared (was not visible in baseline): aria-label='{aria}' (selector: {sel})")
                            generation_started = True
                            break
            except Exception:
                pass
        if generation_started:
            break
        
        # 2. Run button changed state vs baseline (disabled or aria-label change)
        try:
            is_disabled = await run_button.get_attribute("disabled")
            aria_disabled = await run_button.get_attribute("aria-disabled")
            is_enabled = await run_button.is_enabled(timeout=500)
            new_aria = await run_button.get_attribute("aria-label") or ""
            
            run_state_before = report.get("run_button_state", {})
            aria_before = run_state_before.get("aria_label", "")
            disabled_before = run_state_before.get("disabled")
            enabled_before = run_state_before.get("is_enabled")
            
            # Disabled/enabled state change
            if (is_disabled and not disabled_before) or (aria_disabled == "true" and run_state_before.get("aria_disabled") != "true") or (not is_enabled and enabled_before):
                evidence.append("Run button became disabled (state changed from baseline)")
                generation_started = True
                break
            
            # aria-label change from Run-like to Stop-like
            if new_aria and aria_before and "stop" in new_aria.lower() and "run" not in new_aria.lower() and ("run" in aria_before.lower() or "generate" in aria_before.lower() or "prompt" in aria_before.lower()):
                evidence.append(f"Run button aria-label changed from '{aria_before}' to '{new_aria}'")
                generation_started = True
                break
        except Exception:
            pass
        
        # 3. Progress/loading indicator APPEARED or became visible that was not in baseline
        for sel in progress_selectors:
            try:
                count = await page.locator(sel).count()
                visible_count = 0
                if count > 0:
                    for i in range(count):
                        if await page.locator(sel).nth(i).is_visible(timeout=300):
                            visible_count += 1
                
                baseline_info = baseline.get("progress_indicators", {}).get(sel, {})
                baseline_count = baseline_info.get("count", 0)
                baseline_visible = baseline_info.get("visible_count", 0)
                
                if count > baseline_count or visible_count > baseline_visible:
                    evidence.append(f"Progress indicator appeared/increased: {sel} (baseline count={baseline_count}, visible={baseline_visible}; now count={count}, visible={visible_count})")
                    generation_started = True
                    break
            except Exception:
                pass
        if generation_started:
            break
        
        # 4. Generation state element CHANGED meaningful text/state vs baseline
        for sel in state_selectors:
            try:
                count = await page.locator(sel).count()
                texts = []
                if count > 0:
                    for i in range(min(count, 5)):
                        try:
                            locator = page.locator(sel).nth(i)
                            if await locator.is_visible(timeout=300):
                                text = await locator.inner_text(timeout=300)
                                if text and text.strip():
                                    texts.append(text.strip()[:200])
                        except Exception:
                            pass
                
                baseline_info = baseline.get("state_elements", {}).get(sel, {})
                baseline_texts = baseline_info.get("texts", [])
                
                # Check if any new text appeared that wasn't in baseline
                for text in texts:
                    if text and text not in baseline_texts:
                        evidence.append(f"Generation state text changed: '{text[:100]}' (selector: {sel})")
                        generation_started = True
                        break
                if generation_started:
                    break
            except Exception:
                pass
        if generation_started:
            break
        
        # 5. "Stop" text button APPEARED that was not in baseline
        try:
            count = await page.locator("button:has-text('Stop')").count()
            baseline_count = baseline.get("stop_text_buttons", {}).get("count", 0)
            if count > baseline_count:
                stop_text_btn = page.locator("button:has-text('Stop')").first
                if await stop_text_btn.is_visible(timeout=500):
                    aria = await stop_text_btn.get_attribute("aria-label") or ""
                    evidence.append(f"Button with 'Stop' text appeared (baseline count={baseline_count}, now={count}): aria-label='{aria}'")
                    generation_started = True
                    break
        except Exception:
            pass
        
        await asyncio.sleep(poll_interval / 1000)
    
    report["elapsed_ms"] = int((asyncio.get_event_loop().time() - start_time) * 1000)
    report["start_evidence"] = evidence
    
    if generation_started:
        report["generation_started"] = "PASS"
        print_result("Generation Started", "PASS")
        for ev in evidence:
            print_step(f"  Evidence: {ev}", "OK")
        print_step(f"  Time to detection: {report['elapsed_ms']}ms")
    else:
        report["generation_started"] = "FAIL"
        print_result("Generation Started", "FAIL")
        print_step(f"  No strong start evidence within {max_wait_ms}ms", "FAIL")
    
    # Save JSON report
    config.ARTIFACTS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = config.ARTIFACTS_DIR / f"{timestamp}_start_attached.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print_step(f"\n  Start detection report saved to: {report_path}", "OK")
    
    # Don't close the browser - user owns it
    print_step("Leaving Chrome running (start-attached mode does not close browser).")
    return generation_started


async def run_inspect_settings_attached(playwright, cdp_url: str = "http://127.0.0.1:9222"):
    """Mode: inspect-settings-attached - Connect via CDP, find AI Studio tab, read-only inspection of Run Settings panel controls."""
    print_header("MODE: INSPECT-SETTINGS-ATTACHED - Read-Only Run Settings DOM Inspection via CDP")
    
    print_step(f"Connecting to CDP endpoint: {cdp_url}")
    
    browser = None
    try:
        browser = await playwright.chromium.connect_over_cdp(cdp_url)
        print_result("CDP Connection", "PASS")
    except Exception as e:
        print_step(f"CDP connection failed: {e}", "FAIL")
        print_result("CDP Connection", "FAIL")
        return False
    
    # Find AI Studio tab
    aistudio_page = None
    try:
        contexts = browser.contexts
        if not contexts:
            print_step("No existing browser contexts found", "FAIL")
            return False
        
        context = contexts[0]
        print_result("Existing Browser Context", "PASS")
        
        for page in context.pages:
            try:
                url = page.url
                if "aistudio.google.com" in url:
                    aistudio_page = page
                    title = await page.title()
                    print_step(f"AI Studio tab found: {title[:80]}")
                    print_step(f"URL: {url}")
                    break
            except Exception:
                continue
        
        if not aistudio_page:
            print_step("No AI Studio tab found among open pages", "FAIL")
            return False
        
        print_result("AI Studio Tab", "PASS")
        
    except Exception as e:
        print_step(f"Failed to find AI Studio tab: {e}", "FAIL")
        return False
    
    page = aistudio_page
    
    # Settings categories to inspect
    settings_categories = {
        "model_selector": [
            # Model dropdown/selector
            'button[aria-label*="model" i]',
            '[role="combobox"][aria-label*="model" i]',
            'select[aria-label*="model" i]',
            '[data-testid*="model" i]',
            'button:has-text("gemini" i)',
            'button:has-text("Model" i)',
        ],
        "system_instructions": [
            'textarea[aria-label*="system" i]',
            'textarea[placeholder*="system" i]',
            '[contenteditable="true"][aria-label*="system" i]',
            'button[aria-label*="system" i]',
            '[role="textbox"][aria-label*="system" i]',
        ],
        "thinking_level": [
            'button[aria-label*="thinking" i]',
            '[role="combobox"][aria-label*="thinking" i]',
            '[role="slider"][aria-label*="thinking" i]',
            '[data-testid*="thinking" i]',
            'input[type="range"][aria-label*="thinking" i]',
        ],
        "structured_outputs": [
            'button[aria-label*="structured" i]',
            '[role="switch"][aria-label*="structured" i]',
            'input[type="checkbox"][aria-label*="structured" i]',
            '[data-testid*="structured" i]',
        ],
        "code_execution": [
            'button[aria-label*="code" i]',
            '[role="switch"][aria-label*="code" i]',
            'input[type="checkbox"][aria-label*="code" i]',
            '[data-testid*="code" i]',
        ],
        "function_calling": [
            'button[aria-label*="function" i]',
            '[role="switch"][aria-label*="function" i]',
            'input[type="checkbox"][aria-label*="function" i]',
            '[data-testid*="function" i]',
        ],
        "grounding_google_search": [
            'button[aria-label*="search" i]',
            '[role="switch"][aria-label*="search" i]',
            'input[type="checkbox"][aria-label*="search" i]',
            '[data-testid*="search" i]',
        ],
        "grounding_google_maps": [
            'button[aria-label*="maps" i]',
            '[role="switch"][aria-label*="maps" i]',
            'input[type="checkbox"][aria-label*="maps" i]',
            '[data-testid*="maps" i]',
        ],
        "url_context": [
            'button[aria-label*="url" i]',
            '[role="switch"][aria-label*="url" i]',
            'input[type="checkbox"][aria-label*="url" i]',
            '[data-testid*="url" i]',
        ],
        "media_resolution": [
            'button[aria-label*="media" i]',
            '[role="combobox"][aria-label*="media" i]',
            '[data-testid*="media" i]',
        ],
        "safety_settings": [
            'button[aria-label*="safety" i]',
            '[role="combobox"][aria-label*="safety" i]',
            '[data-testid*="safety" i]',
        ],
        "stop_sequence": [
            'input[aria-label*="stop" i]',
            'textarea[aria-label*="stop" i]',
            'input[placeholder*="stop" i]',
            '[data-testid*="stop" i]',
        ],
        "output_length": [
            'input[aria-label*="output" i]',
            '[role="slider"][aria-label*="output" i]',
            'input[type="number"][aria-label*="output" i]',
            '[data-testid*="output" i]',
        ],
    }
    
    # Helper to inspect a locator and return detailed info
    async def inspect_element(locator, selector: str, category: str, index: int = 0):
        """Inspect a single element and return detailed attribute info."""
        try:
            el = locator.nth(index) if await locator.count() > index else locator.first
            info = {
                "selector": selector,
                "category": category,
                "index": index,
                "tag": await el.evaluate("e => e.tagName.toLowerCase()"),
                "role": await el.get_attribute("role"),
                "aria_label": await el.get_attribute("aria-label"),
                "aria_labelledby": await el.get_attribute("aria-labelledby"),
                "aria_describedby": await el.get_attribute("aria-describedby"),
                "aria_expanded": await el.get_attribute("aria-expanded"),
                "aria_checked": await el.get_attribute("aria-checked"),
                "aria_disabled": await el.get_attribute("aria-disabled"),
                "placeholder": await el.get_attribute("placeholder"),
                "value": await el.get_attribute("value"),
                "disabled": await el.get_attribute("disabled"),
                "type": await el.get_attribute("type"),
                "name": await el.get_attribute("name"),
                "data_attributes": {},
                "visible_text": "",
                "is_visible": await el.is_visible(timeout=1000),
                "is_enabled": await el.is_enabled(timeout=1000),
            }
            
            # Collect all data-* attributes
            data_attrs = await el.evaluate("""e => {
                const attrs = {};
                for (const attr of e.attributes) {
                    if (attr.name.startsWith('data-')) {
                        attrs[attr.name] = attr.value;
                    }
                }
                return attrs;
            }""")
            info["data_attributes"] = data_attrs
            
            # Get visible text
            try:
                text = await el.inner_text(timeout=500)
                if text and text.strip():
                    info["visible_text"] = text.strip()[:500]
            except Exception:
                pass
            
            # Get nearby label text (parent or preceding sibling with label-like text)
            try:
                label_text = await el.evaluate("""e => {
                    // Look for associated label
                    const id = e.getAttribute('id');
                    if (id) {
                        const label = document.querySelector(`label[for="${id}"]`);
                        if (label) return label.innerText.trim();
                    }
                    // Look for aria-labelledby
                    const labelledBy = e.getAttribute('aria-labelledby');
                    if (labelledBy) {
                        const label = document.getElementById(labelledBy);
                        if (label) return label.innerText.trim();
                    }
                    // Look at parent for label-like text
                    const parent = e.parentElement;
                    if (parent) {
                        const label = parent.querySelector('label, [role="label"]');
                        if (label) return label.innerText.trim();
                    }
                    return "";
                }""")
                if label_text:
                    info["associated_label"] = label_text[:200]
            except Exception:
                pass
            
            # For combobox/switch/checkbox, get selected/checked state
            if info["role"] in ("combobox", "listbox") or info["aria_expanded"] is not None:
                try:
                    options = await el.locator('[role="option"]').all()
                    if options:
                        selected = []
                        for opt in options[:10]:
                            aria_sel = await opt.get_attribute("aria-selected")
                            text = await opt.inner_text()
                            if aria_sel == "true":
                                selected.append(text.strip()[:100])
                        if selected:
                            info["selected_options"] = selected
                except Exception:
                    pass
            
            return info
        except Exception as e:
            return {"selector": selector, "category": category, "index": index, "error": str(e)}
    
    # Inspect each category
    report = {
        "timestamp": datetime.now().isoformat(),
        "url": page.url,
        "title": await page.title(),
        "settings": {}
    }
    
    print_step("Inspecting Run Settings controls (read-only)...")
    
    for category, selectors in settings_categories.items():
        print_step(f"  Scanning {category}...")
        report["settings"][category] = []
        
        for sel in selectors:
            try:
                locator = page.locator(sel)
                count = await locator.count()
                if count > 0:
                    for i in range(min(count, 5)):
                        info = await inspect_element(locator, sel, category, i)
                        report["settings"][category].append(info)
            except Exception:
                pass
    
    # Also do a broader sweep for common settings-related elements
    print_step("  Scanning for additional settings controls...")
    
    # Generic sweep for buttons with aria-label containing settings keywords
    settings_keywords = [
        "model", "system", "thinking", "structured", "code", "function",
        "search", "maps", "url", "media", "safety", "stop", "output", "length"
    ]
    
    for keyword in settings_keywords:
        try:
            # Buttons
            locator = page.locator(f'button[aria-label*="{keyword}" i]')
            count = await locator.count()
            if count > 0:
                for i in range(min(count, 3)):
                    info = await inspect_element(locator, f'button[aria-label*="{keyword}" i]', f"button_{keyword}", i)
                    report["settings"].setdefault(f"button_{keyword}", []).append(info)
        except Exception:
            pass
        
        try:
            # Switches
            locator = page.locator(f'[role="switch"][aria-label*="{keyword}" i]')
            count = await locator.count()
            if count > 0:
                for i in range(min(count, 3)):
                    info = await inspect_element(locator, f'[role="switch"][aria-label*="{keyword}" i]', f"switch_{keyword}", i)
                    report["settings"].setdefault(f"switch_{keyword}", []).append(info)
        except Exception:
            pass
        
        try:
            # Checkboxes
            locator = page.locator(f'input[type="checkbox"][aria-label*="{keyword}" i]')
            count = await locator.count()
            if count > 0:
                for i in range(min(count, 3)):
                    info = await inspect_element(locator, f'input[type="checkbox"][aria-label*="{keyword}" i]', f"checkbox_{keyword}", i)
                    report["settings"].setdefault(f"checkbox_{keyword}", []).append(info)
        except Exception:
            pass
        
        try:
            # Comboboxes
            locator = page.locator(f'[role="combobox"][aria-label*="{keyword}" i]')
            count = await locator.count()
            if count > 0:
                for i in range(min(count, 3)):
                    info = await inspect_element(locator, f'[role="combobox"][aria-label*="{keyword}" i]', f"combobox_{keyword}", i)
                    report["settings"].setdefault(f"combobox_{keyword}", []).append(info)
        except Exception:
            pass
    
    # Print summary
    print_step("\n  === SETTINGS INSPECTION SUMMARY ===")
    total_found = 0
    for category, elements in report["settings"].items():
        valid = [e for e in elements if "error" not in e]
        if valid:
            total_found += len(valid)
            print_step(f"    {category}: {len(valid)} candidate(s)")
            for e in valid[:2]:
                attrs = []
                if e.get("aria_label"): attrs.append(f'aria-label="{e["aria_label"]}"')
                if e.get("role"): attrs.append(f'role="{e["role"]}"')
                if e.get("tag"): attrs.append(f'tag={e["tag"]}')
                if e.get("visible_text"): attrs.append(f'text="{e["visible_text"][:40]}"')
                if e.get("associated_label"): attrs.append(f'label="{e["associated_label"][:40]}"')
                print(f"      [{e['index']}] {' '.join(attrs)} (selector: {e.get('selector', 'N/A')})")
    
    print_step(f"  Total candidates found: {total_found}")
    
    # Save JSON report
    config.ARTIFACTS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = config.ARTIFACTS_DIR / f"{timestamp}_inspect_settings_attached.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print_step(f"\n  Settings inspection report saved to: {report_path}", "OK")
    
    # Don't close the browser - user owns it
    print_step("Leaving Chrome running (inspect-settings-attached mode does not close browser).")
    return True


async def run_inspect_settings_structure_attached(playwright, cdp_url: str = "http://127.0.0.1:9222"):
    """Mode: inspect-settings-structure-attached - Connect via CDP, find AI Studio tab, structural inspection of visible Run Settings panel."""
    print_header("MODE: INSPECT-SETTINGS-STRUCTURE-ATTACHED - Structural Run Settings DOM Inspection via CDP")
    
    print_step(f"Connecting to CDP endpoint: {cdp_url}")
    
    browser = None
    try:
        browser = await playwright.chromium.connect_over_cdp(cdp_url)
        print_result("CDP Connection", "PASS")
    except Exception as e:
        print_step(f"CDP connection failed: {e}", "FAIL")
        print_result("CDP Connection", "FAIL")
        return False
    
    # Find AI Studio tab
    aistudio_page = None
    try:
        contexts = browser.contexts
        if not contexts:
            print_step("No existing browser contexts found", "FAIL")
            return False
        
        context = contexts[0]
        print_result("Existing Browser Context", "PASS")
        
        for page in context.pages:
            try:
                url = page.url
                if "aistudio.google.com" in url:
                    aistudio_page = page
                    title = await page.title()
                    print_step(f"AI Studio tab found: {title[:80]}")
                    print_step(f"URL: {url}")
                    break
            except Exception:
                continue
        
        if not aistudio_page:
            print_step("No AI Studio tab found among open pages", "FAIL")
            return False
        
        print_result("AI Studio Tab", "PASS")
        
    except Exception as e:
        print_step(f"Failed to find AI Studio tab: {e}", "FAIL")
        return False
    
    page = aistudio_page
    
    # Known visible labels in Run Settings panel (from visual inspection)
    known_labels = [
        "Run settings",
        "System instructions",
        "Structured outputs",
        "Code execution",
        "Function calling",
        "Grounding with Google Search",
        "Grounding with Google Maps",
        "URL context",
        "Media resolution",
        "Safety settings",
        "Stop sequence",
        "Output length",
        "Thinking level",
        "Gemini",
    ]
    
    # Helper to inspect element with full structural context
    async def inspect_element_full(locator, index: int = 0):
        """Inspect a single element with full structural context."""
        try:
            el = locator.nth(index) if await locator.count() > index else locator.first
            
            # Get all attributes via evaluate
            info = await el.evaluate("""e => {
                const attrs = {};
                for (const attr of e.attributes) {
                    attrs[attr.name] = attr.value;
                }
                return {
                    tag: e.tagName.toLowerCase(),
                    attributes: attrs,
                    text: e.innerText ? e.innerText.trim().substring(0, 500) : "",
                };
            }""")
            
            # Get parent
            parent_info = await el.evaluate("""e => {
                const p = e.parentElement;
                if (!p) return null;
                const attrs = {};
                for (const attr of p.attributes) {
                    attrs[attr.name] = attr.value;
                }
                return {
                    tag: p.tagName.toLowerCase(),
                    attributes: attrs,
                    text: p.innerText ? p.innerText.trim().substring(0, 300) : "",
                };
            }""")
            
            # Get grandparent
            grandparent_info = await el.evaluate("""e => {
                const p = e.parentElement;
                if (!p) return null;
                const gp = p.parentElement;
                if (!gp) return null;
                const attrs = {};
                for (const attr of gp.attributes) {
                    attrs[attr.name] = attr.value;
                }
                return {
                    tag: gp.tagName.toLowerCase(),
                    attributes: attrs,
                    text: gp.innerText ? gp.innerText.trim().substring(0, 300) : "",
                };
            }""")
            
            # Get siblings
            siblings_info = await el.evaluate("""e => {
                const siblings = [];
                let sibling = e.previousElementSibling;
                while (sibling && siblings.length < 5) {
                    const attrs = {};
                    for (const attr of sibling.attributes) {
                        attrs[attr.name] = attr.value;
                    }
                    siblings.push({
                        tag: sibling.tagName.toLowerCase(),
                        attributes: attrs,
                        text: sibling.innerText ? sibling.innerText.trim().substring(0, 200) : "",
                        position: "previous"
                    });
                    sibling = sibling.previousElementSibling;
                }
                sibling = e.nextElementSibling;
                while (sibling && siblings.length < 10) {
                    const attrs = {};
                    for (const attr of sibling.attributes) {
                        attrs[attr.name] = attr.value;
                    }
                    siblings.push({
                        tag: sibling.tagName.toLowerCase(),
                        attributes: attrs,
                        text: sibling.innerText ? sibling.innerText.trim().substring(0, 200) : "",
                        position: "next"
                    });
                    sibling = sibling.nextElementSibling;
                }
                return siblings;
            }""")
            
            # Find nearby interactive descendants/descendants in container
            interactive_descendants = await el.evaluate("""e => {
                const container = e.closest('[role="region"], [role="group"], [role="panel"], section, div, form, aside, nav') || e.parentElement;
                if (!container) return [];
                
                const interactiveSelectors = [
                    'button', 'input', 'textarea', 'select',
                    '[role="button"]', '[role="combobox"]', '[role="switch"]', '[role="slider"]',
                    '[role="checkbox"]', '[role="radio"]', '[role="menuitem"]', '[role="tab"]',
                    '[aria-expanded]', '[aria-checked]', '[aria-selected]',
                    '[contenteditable="true"]'
                ];
                
                const results = [];
                for (const sel of interactiveSelectors) {
                    const elements = container.querySelectorAll(sel);
                    for (const el of elements) {
                        if (results.length >= 20) break;
                        const attrs = {};
                        for (const attr of el.attributes) {
                            attrs[attr.name] = attr.value;
                        }
                        results.push({
                            tag: el.tagName.toLowerCase(),
                            attributes: attrs,
                            text: el.innerText ? el.innerText.trim().substring(0, 200) : "",
                        });
                    }
                }
                return results;
            }""")
            
            return {
                "element": info,
                "parent": parent_info,
                "grandparent": grandparent_info,
                "siblings": siblings_info,
                "interactive_descendants": interactive_descendants,
            }
        except Exception as e:
            return {"error": str(e)}
    
    # Helper to inspect any visible interactive element
    async def inspect_interactive_element(locator, index: int = 0):
        """Inspect a single interactive element."""
        try:
            el = locator.nth(index) if await locator.count() > index else locator.first
            info = await el.evaluate("""e => {
                const attrs = {};
                for (const attr of e.attributes) {
                    attrs[attr.name] = attr.value;
                }
                return {
                    tag: e.tagName.toLowerCase(),
                    attributes: attrs,
                    text: e.innerText ? e.innerText.trim().substring(0, 500) : "",
                    is_visible: e.offsetWidth > 0 && e.offsetHeight > 0,
                };
            }""")
            return info
        except Exception as e:
            return {"error": str(e)}
    
    report = {
        "timestamp": datetime.now().isoformat(),
        "url": page.url,
        "title": await page.title(),
        "settings_structure": [],
        "all_interactive_in_sidebar": [],
    }
    
    print_step("Inspecting Run Settings panel structure (read-only)...")
    
    # 1. For each known label, find elements containing that text and inspect structure
    for label_text in known_labels:
        print_step(f"  Scanning for label: '{label_text}'...")
        try:
            # Find elements containing this text (case-insensitive)
            # Use XPath for text search
            xpath = f"//*[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{label_text.lower()}')]"
            # Also check for elements with aria-label or aria-labelledby containing the text
            locator = page.locator(f"xpath={xpath}").first
            
            # Also try text-based locator
            text_locator = page.locator(f"text={label_text}").first
            
            # Try both approaches
            for loc in [locator, text_locator]:
                try:
                    count = await loc.count()
                    if count > 0:
                        for i in range(min(count, 3)):
                            structural_info = await inspect_element_full(loc, i)
                            structural_info["matched_label"] = label_text
                            structural_info["match_method"] = "xpath" if loc == locator else "text"
                            report["settings_structure"].append(structural_info)
                except Exception:
                    pass
        except Exception:
            pass
    
    # 2. Also search for elements with aria-label containing these keywords
    for label_text in known_labels:
        keywords = label_text.lower().split()
        for keyword in keywords:
            if len(keyword) < 3:
                continue
            try:
                locator = page.locator(f'[aria-label*="{keyword}" i]').first
                count = await locator.count()
                if count > 0:
                    for i in range(min(count, 2)):
                        structural_info = await inspect_element_full(locator, i)
                        structural_info["matched_label"] = label_text
                        structural_info["match_method"] = f"aria-label:{keyword}"
                        report["settings_structure"].append(structural_info)
            except Exception:
                pass
    
    # 3. Collect ALL visible interactive elements in likely settings panel areas
    print_step("  Scanning all visible interactive elements in settings panel...")
    
    # Look for sidebar/panel containers that might contain settings
    sidebar_selectors = [
        'aside',
        '[role="complementary"]',
        '[role="panel"]',
        '[role="region"]',
        'div[class*="sidebar" i]',
        'div[class*="panel" i]',
        'div[class*="settings" i]',
        'div[class*="drawer" i]',
    ]
    
    for sidebar_sel in sidebar_selectors:
        try:
            sidebar = page.locator(sidebar_sel).first
            if await sidebar.is_visible(timeout=1000):
                # Find all interactive elements within
                interactive_selectors = [
                    'button',
                    'input',
                    'textarea',
                    'select',
                    '[role="button"]',
                    '[role="combobox"]',
                    '[role="switch"]',
                    '[role="slider"]',
                    '[role="checkbox"]',
                    '[role="radio"]',
                    '[role="menuitem"]',
                    '[role="tab"]',
                    '[aria-expanded]',
                    '[aria-checked]',
                    '[aria-selected]',
                    '[contenteditable="true"]',
                ]
                
                for int_sel in interactive_selectors:
                    try:
                        locator = sidebar.locator(int_sel)
                        count = await locator.count()
                        if count > 0:
                            for i in range(min(count, 30)):
                                info = await inspect_interactive_element(locator, i)
                                info["container_selector"] = sidebar_sel
                                info["interactive_selector"] = int_sel
                                report["all_interactive_in_sidebar"].append(info)
                    except Exception:
                        pass
        except Exception:
            pass
    
    # Also do a page-wide scan for interactive elements if no sidebar found
    if not report["all_interactive_in_sidebar"]:
        print_step("  No sidebar found, doing page-wide interactive scan...")
        interactive_selectors = [
            'button',
            'input',
            'textarea',
            'select',
            '[role="button"]',
            '[role="combobox"]',
            '[role="switch"]',
            '[role="slider"]',
            '[role="checkbox"]',
            '[role="radio"]',
            '[aria-expanded]',
            '[aria-checked]',
        ]
        
        for int_sel in interactive_selectors:
            try:
                locator = page.locator(int_sel)
                count = await locator.count()
                if count > 0:
                    for i in range(min(count, 50)):
                        info = await inspect_interactive_element(locator, i)
                        info["interactive_selector"] = int_sel
                        report["all_interactive_in_sidebar"].append(info)
            except Exception:
                pass
    
    # Print summary
    print_step("\n  === SETTINGS STRUCTURE INSPECTION SUMMARY ===")
    print_step(f"  Label-matched structures: {len(report['settings_structure'])}")
    for item in report["settings_structure"][:10]:
        if "error" not in item:
            el = item.get("element", {})
            tag = el.get("tag", "?")
            text = el.get("text", "")[:60]
            method = item.get("match_method", "?")
            label = item.get("matched_label", "?")
            print_step(f"    [{method}] '{label}' -> <{tag}> '{text}'")
    
    print_step(f"  All interactive elements collected: {len(report['all_interactive_in_sidebar'])}")
    # Deduplicate by tag+attributes for summary
    seen = set()
    for item in report["all_interactive_in_sidebar"]:
        if "error" not in item:
            key = f"{item.get('tag','?')}:{item.get('attributes',{}).get('aria-label','')}:{item.get('attributes',{}).get('role','')}"
            if key not in seen:
                seen.add(key)
    print_step(f"  Unique interactive controls: {len(seen)}")
    for key in list(seen)[:15]:
        print(f"    {key}")
    
    # Save JSON report
    config.ARTIFACTS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = config.ARTIFACTS_DIR / f"{timestamp}_inspect_settings_structure_attached.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print_step(f"\n  Settings structure report saved to: {report_path}", "OK")
    
    # Don't close the browser - user owns it
    print_step("Leaving Chrome running (inspect-settings-structure-attached mode does not close browser).")
    return True


async def run_discover_settings_panel_attached(playwright, cdp_url: str = "http://127.0.0.1:9222"):
    """Mode: discover-settings-panel-attached - Connect via CDP, find AI Studio tab, locate Run Settings toggle, click it, DOM diff to identify panel."""
    print_header("MODE: DISCOVER-SETTINGS-PANEL-ATTACHED - DOM Diff Discovery of Run Settings Panel via CDP")
    
    print_step(f"Connecting to CDP endpoint: {cdp_url}")
    
    browser = None
    try:
        browser = await playwright.chromium.connect_over_cdp(cdp_url)
        print_result("CDP Connection", "PASS")
    except Exception as e:
        print_step(f"CDP connection failed: {e}", "FAIL")
        print_result("CDP Connection", "FAIL")
        return False
    
    # Find AI Studio tab
    aistudio_page = None
    try:
        contexts = browser.contexts
        if not contexts:
            print_step("No existing browser contexts found", "FAIL")
            return False
        
        context = contexts[0]
        print_result("Existing Browser Context", "PASS")
        
        for page in context.pages:
            try:
                url = page.url
                if "aistudio.google.com" in url:
                    aistudio_page = page
                    title = await page.title()
                    print_step(f"AI Studio tab found: {title[:80]}")
                    print_step(f"URL: {url}")
                    break
            except Exception:
                continue
        
        if not aistudio_page:
            print_step("No AI Studio tab found among open pages", "FAIL")
            return False
        
        print_result("AI Studio Tab", "PASS")
        
    except Exception as e:
        print_step(f"Failed to find AI Studio tab: {e}", "FAIL")
        return False
    
    page = aistudio_page
    
    # Known settings labels to look for in candidate containers
    known_labels = [
        "System instructions",
        "Thinking level",
        "Structured outputs",
        "Code execution",
        "Function calling",
        "Media resolution",
        "Output length",
        "Grounding with Google Search",
        "Grounding with Google Maps",
        "URL context",
        "Safety settings",
        "Stop sequence",
    ]
    
    # Helper to capture lightweight DOM snapshot of visible elements
    async def capture_dom_snapshot():
        """Capture a lightweight snapshot of visible elements on the page."""
        return await page.evaluate("""() => {
            const knownLabels = [
                "System instructions",
                "Thinking level", 
                "Structured outputs",
                "Code execution",
                "Function calling",
                "Media resolution",
                "Output length",
                "Grounding with Google Search",
                "Grounding with Google Maps",
                "URL context",
                "Safety settings",
                "Stop sequence"
            ];
            
            function isTooltipInfrastructure(el) {
                // Exclude Angular CDK tooltip infrastructure
                if (el.closest('.cdk-describedby-message-container')) return true;
                if (el.classList.contains('cdk-visually-hidden')) return true;
                if (el.getAttribute('role') === 'tooltip') return true;
                return false;
            }
            
            function getBBox(el) {
                try {
                    const rect = el.getBoundingClientRect();
                    return {
                        x: rect.x,
                        y: rect.y,
                        width: rect.width,
                        height: rect.height,
                        visible: rect.width > 0 && rect.height > 0
                    };
                } catch {
                    return null;
                }
            }
            
            function getAttrs(el) {
                const attrs = {};
                for (const attr of el.attributes) {
                    attrs[attr.name] = attr.value;
                }
                return attrs;
            }
            
            const elements = [];
            const walker = document.createTreeWalker(
                document.body,
                NodeFilter.SHOW_ELEMENT,
                null,
                false
            );
            
            let node;
            while ((node = walker.nextNode()) && elements.length < 2000) {
                if (isTooltipInfrastructure(node)) {
                    continue;
                }
                const style = window.getComputedStyle(node);
                if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") {
                    continue;
                }
                const bbox = getBBox(node);
                if (!bbox || (!bbox.visible && node.tagName !== "SCRIPT" && node.tagName !== "STYLE")) {
                    continue;
                }
                
                const attrs = getAttrs(node);
                const text = node.innerText ? node.innerText.trim().substring(0, 200) : "";
                
                // Check which known labels this element or its descendants contain
                const containedLabels = [];
                for (const label of knownLabels) {
                    if (text.toLowerCase().includes(label.toLowerCase())) {
                        containedLabels.push(label);
                    }
                }
                
                elements.push({
                    tag: node.tagName.toLowerCase(),
                    class: attrs.class || "",
                    role: attrs.role || "",
                    aria_label: attrs["aria-label"] || "",
                    aria_expanded: attrs["aria-expanded"] || "",
                    aria_controls: attrs["aria-controls"] || "",
                    id: attrs.id || "",
                    data_testid: attrs["data-testid"] || "",
                    text: text,
                    bbox: bbox,
                    contained_labels: containedLabels,
                    attributes: attrs
                });
            }
            return elements;
        }""")
    
    # Helper to count visible known settings labels in a snapshot
    def count_known_settings_visible(snapshot):
        """Count how many known settings labels are visible in the snapshot.
        Excludes tooltip infrastructure elements.
        """
        visible_labels = set()
        for el in snapshot:
            if el.get('bbox', {}).get('visible', False):
                for label in el.get('contained_labels', []):
                    visible_labels.add(label)
        return len(visible_labels), list(visible_labels)
    
    # Helper to find candidate panel containers from symmetric diff
    def find_candidate_panels(before, after, direction):
        """Compare before/after snapshots to find changed containers.
        direction: 'opening', 'closing', or 'unknown'
        """
        before_map = {}
        for el in before:
            key = f"{el['tag']}:{el.get('id','')}:{el.get('class','')[:50]}:{el.get('role','')}"
            before_map[key] = el
        
        after_map = {}
        for el in after:
            key = f"{el['tag']}:{el.get('id','')}:{el.get('class','')[:50]}:{el.get('role','')}"
            after_map[key] = el
        
        candidates = []
        
        if direction == 'opening':
            # Look for elements that appeared or expanded (after vs before)
            for el in after:
                key = f"{el['tag']}:{el.get('id','')}:{el.get('class','')[:50]}:{el.get('role','')}"
                
                if key not in before_map:
                    # New element appeared
                    if el['bbox'] and el['bbox']['visible'] and el['bbox']['width'] > 50 and el['bbox']['height'] > 50:
                        candidates.append({"type": "new_element", "element": el})
                else:
                    before_el = before_map[key]
                    before_bbox = before_el.get('bbox')
                    after_bbox = el.get('bbox')
                    
                    if before_bbox and after_bbox:
                        was_visible = before_bbox['visible']
                        now_visible = after_bbox['visible']
                        size_before = before_bbox['width'] * before_bbox['height']
                        size_after = after_bbox['width'] * after_bbox['height']
                        
                        if (not was_visible and now_visible and size_after > 2500) or \
                           (size_after > size_before * 3 and size_after > 2500):
                            candidates.append({
                                "type": "expanded_element",
                                "element": el,
                                "before_bbox": before_bbox,
                                "after_bbox": after_bbox
                            })
        
        elif direction == 'closing':
            # Look for elements that disappeared or collapsed (before vs after)
            for el in before:
                key = f"{el['tag']}:{el.get('id','')}:{el.get('class','')[:50]}:{el.get('role','')}"
                
                if key not in after_map:
                    # Element disappeared
                    if el['bbox'] and el['bbox']['visible'] and el['bbox']['width'] > 50 and el['bbox']['height'] > 50:
                        candidates.append({"type": "disappeared_element", "element": el})
                else:
                    after_el = after_map[key]
                    before_bbox = el.get('bbox')
                    after_bbox = after_el.get('bbox')
                    
                    if before_bbox and after_bbox:
                        was_visible = before_bbox['visible']
                        now_visible = after_bbox['visible']
                        size_before = before_bbox['width'] * before_bbox['height']
                        size_after = after_bbox['width'] * after_bbox['height']
                        
                        if (was_visible and not now_visible and size_before > 2500) or \
                           (size_before > size_after * 3 and size_before > 2500):
                            candidates.append({
                                "type": "collapsed_element",
                                "element": el,
                                "before_bbox": before_bbox,
                                "after_bbox": after_bbox
                            })
        
        else:  # direction == 'unknown'
            # Perform both comparisons
            # Opening check
            for el in after:
                key = f"{el['tag']}:{el.get('id','')}:{el.get('class','')[:50]}:{el.get('role','')}"
                if key not in before_map:
                    if el['bbox'] and el['bbox']['visible'] and el['bbox']['width'] > 50 and el['bbox']['height'] > 50:
                        candidates.append({"type": "new_element", "element": el})
                else:
                    before_el = before_map[key]
                    before_bbox = before_el.get('bbox')
                    after_bbox = el.get('bbox')
                    if before_bbox and after_bbox:
                        was_visible = before_bbox['visible']
                        now_visible = after_bbox['visible']
                        size_before = before_bbox['width'] * before_bbox['height']
                        size_after = after_bbox['width'] * after_bbox['height']
                        if (not was_visible and now_visible and size_after > 2500) or \
                           (size_after > size_before * 3 and size_after > 2500):
                            candidates.append({
                                "type": "expanded_element",
                                "element": el,
                                "before_bbox": before_bbox,
                                "after_bbox": after_bbox
                            })
            
            # Closing check
            for el in before:
                key = f"{el['tag']}:{el.get('id','')}:{el.get('class','')[:50]}:{el.get('role','')}"
                if key not in after_map:
                    if el['bbox'] and el['bbox']['visible'] and el['bbox']['width'] > 50 and el['bbox']['height'] > 50:
                        candidates.append({"type": "disappeared_element", "element": el})
                else:
                    after_el = after_map[key]
                    before_bbox = el.get('bbox')
                    after_bbox = after_el.get('bbox')
                    if before_bbox and after_bbox:
                        was_visible = before_bbox['visible']
                        now_visible = after_bbox['visible']
                        size_before = before_bbox['width'] * before_bbox['height']
                        size_after = after_bbox['width'] * after_bbox['height']
                        if (was_visible and not now_visible and size_before > 2500) or \
                           (size_before > size_after * 3 and size_before > 2500):
                            candidates.append({
                                "type": "collapsed_element",
                                "element": el,
                                "before_bbox": before_bbox,
                                "after_bbox": after_bbox
                            })
        
        return candidates

    # 1. Locate the Run Settings toggle
    print_step("Locating Run Settings toggle button...")
    toggle_selector = 'button[aria-label="Toggle run settings panel"]'
    toggle = page.locator(toggle_selector).first
    
    try:
        if not await toggle.is_visible(timeout=5000):
            print_step("Toggle button not visible", "FAIL")
            return False
    except Exception:
        print_step("Toggle button not found", "FAIL")
        return False
    
    toggle_info = await toggle.evaluate("""e => {
        const attrs = {};
        for (const attr of e.attributes) {
            attrs[attr.name] = attr.value;
        }
        return {
            tag: e.tagName.toLowerCase(),
            attributes: attrs,
            text: e.innerText ? e.innerText.trim() : "",
            bbox: (() => { try { const r = e.getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height}; } catch { return null; } })()
        };
    }""")
    
    print_step(f"Toggle found: {toggle_info['tag']} aria-label='{toggle_info['attributes'].get('aria-label','')}'")
    
    # 2. Capture baseline BEFORE toggling
    print_step("Capturing baseline DOM snapshot...")
    baseline = await capture_dom_snapshot()
    print_step(f"  Baseline elements captured: {len(baseline)}")
    
    # Record known settings visibility BEFORE
    known_before_count, known_before_labels = count_known_settings_visible(baseline)
    print_step(f"  Known settings visible before: {known_before_count} ({', '.join(known_before_labels) if known_before_labels else 'none'})")
    
    # 3. Click the toggle (discovery click)
    print_step("Clicking Run Settings toggle...")
    try:
        await toggle.click()
        print_result("Toggle Click", "PASS")
    except Exception as e:
        print_step(f"Toggle click failed: {e}", "FAIL")
        return False
    
    # 4. Wait for animation/rendering
    print_step("Waiting for panel animation...")
    await page.wait_for_timeout(1500)
    
    # 5. Capture snapshot AFTER toggling
    print_step("Capturing post-toggle DOM snapshot...")
    after = await capture_dom_snapshot()
    print_step(f"  Post-toggle elements captured: {len(after)}")
    
    # Record known settings visibility AFTER
    known_after_count, known_after_labels = count_known_settings_visible(after)
    print_step(f"  Known settings visible after: {known_after_count} ({', '.join(known_after_labels) if known_after_labels else 'none'})")
    
    # 6. Determine direction from settings visibility change
    delta = known_after_count - known_before_count
    if delta >= 3:
        direction = "opening"
        print_step(f"  Direction: CLOSED -> OPEN (settings visibility increased by {delta})")
    elif delta <= -3:
        direction = "closing"
        print_step(f"  Direction: OPEN -> CLOSED (settings visibility decreased by {abs(delta)})")
    else:
        direction = "unknown"
        print_step(f"  Direction: UNKNOWN (settings visibility change: {delta})")
    
    # Build report once
    report = {
        "timestamp": datetime.now().isoformat(),
        "url": page.url,
        "title": await page.title(),
        "toggle": toggle_info,
        "initial_panel_state": "open" if direction == "closing" else ("closed" if direction == "opening" else "unknown"),
        "known_settings_visible_before": known_before_count,
        "known_settings_labels_before": known_before_labels,
        "known_settings_visible_after": known_after_count,
        "known_settings_labels_after": known_after_labels,
        "direction": direction,
        "candidates": [],
        "panel_restored": False,
    }
    
    # 7. Diff to find candidates (symmetric based on direction)
    print_step(f"Comparing DOM snapshots (direction: {direction})...")
    candidates = find_candidate_panels(baseline, after, direction)
    print_step(f"  Candidate containers found: {len(candidates)}")
    
    # Analyze each candidate
    for i, cand in enumerate(candidates):
        el = cand["element"]
        contained = el.get("contained_labels", [])
        bbox = el.get("bbox", {})
        
        # Get interactive descendants
        selector = f"{el['tag']}[id='{el.get('id','')}']" if el.get('id') else (f".{el.get('class','').split()[0]}" if el.get('class') else el['tag'])
        interactive = await page.evaluate("""(sel) => {
            const root = document.querySelector(sel);
            if (!root) return [];
            const results = [];
            const interactiveSelectors = [
                'button', 'input', 'textarea', 'select',
                '[role="button"]', '[role="combobox"]', '[role="switch"]', '[role="slider"]',
                '[role="checkbox"]', '[role="radio"]', '[contenteditable="true"]'
            ];
            for (const isel of interactiveSelectors) {
                const elements = root.querySelectorAll(isel);
                for (const e of elements) {
                    if (results.length >= 30) break;
                    const attrs = {};
                    for (const attr of e.attributes) {
                        attrs[attr.name] = attr.value;
                    }
                    const style = window.getComputedStyle(e);
                    const visible = style.display !== "none" && style.visibility !== "hidden" && style.opacity !== "0";
                    const rect = e.getBoundingClientRect();
                    results.push({
                        tag: e.tagName.toLowerCase(),
                        attributes: attrs,
                        text: e.innerText ? e.innerText.trim().substring(0, 200) : "",
                        visible: visible && rect.width > 0 && rect.height > 0,
                        bbox: {x: rect.x, y: rect.y, width: rect.width, height: rect.height}
                    });
                }
            }
            return results;
        }""", selector)
        
        cand_report = {
            "candidate_index": i,
            "type": cand["type"],
            "tag": el["tag"],
            "class": el["class"],
            "role": el["role"],
            "id": el.get("id", ""),
            "aria_label": el["aria_label"],
            "aria_expanded": el["aria_expanded"],
            "aria_controls": el["aria_controls"],
            "data_testid": el.get("data_testid", ""),
            "bbox": bbox,
            "contained_settings_labels": contained,
            "interactive_descendants": interactive,
        }
        
        if "before_bbox" in cand:
            cand_report["size_change"] = {
                "before": cand["before_bbox"],
                "after": cand["after_bbox"]
            }
        
        report["candidates"].append(cand_report)
        
        print_step(f"  Candidate {i}: <{el['tag']}> class='{el['class'][:60]}' role='{el['role']}' bbox={bbox.get('width',0)}x{bbox.get('height',0)} visible={bbox.get('visible',False)} labels={contained}")
    
    # Rank candidates by confidence
    def score_candidate(cand):
        score = 0
        labels = cand.get("contained_settings_labels", [])
        score += len(labels) * 10
        bbox = cand.get("bbox", {})
        if bbox.get("visible"):
            score += 5
        if bbox.get("width", 0) > 200 and bbox.get("height", 0) > 200:
            score += 5
        if cand.get("role") in ("dialog", "panel", "region", "complementary"):
            score += 5
        if "settings" in cand.get("class", "").lower() or "panel" in cand.get("class", "").lower():
            score += 3
        if cand.get("aria_controls") or cand.get("id"):
            score += 2
        return score
    
    for cand in report["candidates"]:
        cand["confidence_score"] = score_candidate(cand)
    
    report["candidates"].sort(key=lambda c: c["confidence_score"], reverse=True)
    
    print_step("\n  === CANDIDATE RANKING ===")
    for i, cand in enumerate(report["candidates"][:5]):
        print_step(f"  #{i+1} score={cand['confidence_score']} <{cand['tag']}> class='{cand['class'][:50]}' labels={cand['contained_settings_labels']} bbox={cand['bbox'].get('width',0)}x{cand['bbox'].get('height',0)}")
    
    # Determine strongest candidate
    best = report["candidates"][0] if report["candidates"] else None
    if best:
        report["best_candidate"] = {
            "index": best["candidate_index"],
            "confidence_score": best["confidence_score"],
            "tag": best["tag"],
            "class": best["class"],
            "role": best["role"],
            "id": best["id"],
            "contained_settings_labels": best["contained_settings_labels"],
            "interactive_count": len(best.get("interactive_descendants", [])),
        }
        print_step(f"\n  Best candidate: #{best['candidate_index']+1} score={best['confidence_score']} labels={best['contained_settings_labels']}")
    else:
        print_step("\n  No strong panel candidate found", "WARN")
    
    # 8. Restore original panel state - click toggle exactly once more
    print_step("Restoring original panel state (clicking toggle again)...")
    try:
        await toggle.click()
        await page.wait_for_timeout(800)
        
        # Verify restoration by checking known settings visibility
        after_restore = await capture_dom_snapshot()
        restored_count, restored_labels = count_known_settings_visible(after_restore)
        
        # Compare with original baseline - exact label set equality preferred
        count_match = abs(restored_count - known_before_count) <= 1
        labels_match = set(restored_labels) == set(known_before_labels)
        
        if count_match and labels_match:
            report["panel_restored"] = True
            print_step(f"  Panel restored: settings visible={restored_count} (matches baseline {known_before_count})", "OK")
        else:
            report["panel_restored"] = False
            print_step(f"  Panel restore FAIL: visible={restored_count} labels={restored_labels} vs baseline count={known_before_count} labels={known_before_labels}", "FAIL")
    except Exception as e:
        print_step(f"  Failed to restore panel state: {e}", "FAIL")
        report["panel_restored"] = False
    
    # Save JSON report
    config.ARTIFACTS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = config.ARTIFACTS_DIR / f"{timestamp}_discover_settings_panel_attached.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print_step(f"\n  Discovery report saved to: {report_path}", "OK")
    
    # Don't close the browser - user owns it
    print_step("Leaving Chrome running (discover-settings-panel-attached mode does not close browser).")
    return True


async def main():
    parser = argparse.ArgumentParser(
        description="Playwright AI Studio Feasibility Probe",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python probe.py login
  python probe.py check
  python probe.py inspect
  python probe.py run lecture.pdf
  python probe.py attach
  python probe.py inspect-attached
  python probe.py inspect-settings-attached
  python probe.py inspect-settings-structure-attached
  python probe.py discover-settings-panel-attached
  python probe.py upload-attached lecture.pdf
  python probe.py start-attached
  python probe.py attach --cdp-url http://127.0.0.1:9222
  AI_STUDIO_URL=https://aistudio.google.com/prompts/xxx python probe.py run lecture.pdf
        """
    )
    parser.add_argument("mode", choices=["login", "check", "inspect", "run", "attach", "inspect-attached", "inspect-settings-attached", "inspect-settings-structure-attached", "discover-settings-panel-attached", "upload-attached", "start-attached"],
                        help="Operation mode")
    parser.add_argument("pdf_path", nargs="?", help="Path to PDF file (required for 'run' and 'upload-attached' modes)")
    parser.add_argument("--url", help="Override AI Studio URL", default=None)
    parser.add_argument("--browser", choices=["chromium", "chrome"], default="chromium",
                        help="Browser channel: 'chromium' (bundled) or 'chrome' (system Chrome)")
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222",
                        help="CDP endpoint for attach mode (default: http://127.0.0.1:9222)")
    
    args = parser.parse_args()
    
    if args.mode in ("run", "upload-attached") and not args.pdf_path:
        parser.error(f"{args.mode} mode requires <PDF_PATH>")
    
    if args.url:
        config.AI_STUDIO_URL = args.url
        print_step(f"Using custom AI Studio URL: {config.AI_STUDIO_URL}")
    
    # Apply browser channel: update config and env for profile dir
    os.environ["BROWSER_CHANNEL"] = args.browser
    # Re-import config to pick up new PROFILE_DIR based on channel
    import importlib
    importlib.reload(config)
    
    print_step(f"Browser channel: {args.browser} | Profile: {config.PROFILE_DIR}")
    
    # Ensure artifacts dir exists
    config.ARTIFACTS_DIR.mkdir(exist_ok=True)
    
    async with async_playwright() as playwright:
        try:
            if args.mode == "login":
                await run_login(playwright, args.browser)
            elif args.mode == "check":
                await run_check(playwright, args.browser)
            elif args.mode == "inspect":
                await run_inspect(playwright, args.browser)
            elif args.mode == "run":
                await run_generation(playwright, args.pdf_path, args.browser)
            elif args.mode == "attach":
                await run_attach(playwright, args.cdp_url)
            elif args.mode == "inspect-attached":
                await run_inspect_attached(playwright, args.cdp_url)
            elif args.mode == "inspect-settings-attached":
                await run_inspect_settings_attached(playwright, args.cdp_url)
            elif args.mode == "inspect-settings-structure-attached":
                await run_inspect_settings_structure_attached(playwright, args.cdp_url)
            elif args.mode == "discover-settings-panel-attached":
                await run_discover_settings_panel_attached(playwright, args.cdp_url)
            elif args.mode == "upload-attached":
                await run_upload_attached(playwright, args.pdf_path, args.cdp_url)
            elif args.mode == "start-attached":
                await run_start_attached(playwright, args.cdp_url)
        except KeyboardInterrupt:
            print_step("\nInterrupted by user", "WARN")
        except Exception as e:
            print_step(f"Error: {e}", "FAIL")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())