"""
Configuration for AI Studio Playwright prototype.
All selectors are candidates to be tested against the live page.
"""

import os
from pathlib import Path

# Base directories
BASE_DIR = Path(__file__).parent
ARTIFACTS_DIR = BASE_DIR / "artifacts"

# Browser channel configuration
BROWSER_CHANNEL = os.environ.get("BROWSER_CHANNEL", "chromium")  # "chromium" or "chrome"
# Profile directory per channel to avoid cross-contamination
PROFILE_DIR = BASE_DIR / f"profile_{BROWSER_CHANNEL}"

# AI Studio URLs
AI_STUDIO_DEFAULT_URL = "https://aistudio.google.com"
AI_STUDIO_URL = os.environ.get("AI_STUDIO_URL", AI_STUDIO_DEFAULT_URL)
GOOGLE_SIGNIN_URL = "https://accounts.google.com"

# Timeouts (milliseconds)
NAVIGATION_TIMEOUT_MS = 30_000
UI_ACTION_TIMEOUT_MS = 15_000
FILE_UPLOAD_TIMEOUT_MS = 20_000
GENERATION_START_TIMEOUT_MS = 30_000   # short: Run -> Stop transition
GENERATION_TIMEOUT_MS = 600_000        # long: Stop -> Run completion
POLL_INTERVAL_MS = 1_000

# Selector candidates for AI Studio controls
# Ordered by preference: accessible role/label first, then fallback selectors
SELECTORS = {
    # File upload mechanisms
    "file_input": [
        "input[type='file']",
        "input[type='file'][accept*='pdf']",
    ],
    "add_media_button": [
        'button[aria-label="Insert media"]',
        'button[aria-label="Add media"]',
        'button[aria-label*="media" i]',
        'button[aria-label*="attach" i]',
        'button[aria-label*="upload" i]',
        '[data-testid*="media" i]',
        '[data-testid*="attach" i]',
    ],
    "file_chooser_trigger": [
        'button[aria-label="Insert media"]',
        'button[aria-label="Add media"]',
        'button[aria-label*="media" i]',
    ],

    # Run/Stop button - transition: Run -> Stop -> Run
    "run_button": [
        'button[aria-label="Run prompt"]',
        'button[aria-label="Run"]',
        'button:has-text("Run")',
        '[role="button"][aria-label*="run" i]',
    ],
    "stop_button": [
        'button[aria-label="Stop"]',
        'button[aria-label="Stop prompt"]',
        'button[aria-label*="stop" i]',
        '[role="button"][aria-label*="stop" i]',
    ],
    "run_or_stop_button": [
        'button[role="button"][aria-label*="run" i]',
        'button[role="button"][aria-label*="stop" i]',
    ],

    # Model response container - last assistant message
    "response_container": [
        'ms-chat-turn[data-role="model"]',
        '[data-turn-role="model"]',
        '.model-response',
        '[data-message-role="model"]',
        'ms-chat-turn:last-child',
        '[data-testid*="model-response" i]',
    ],
    "response_text": [
        "ms-chat-turn[data-role='model'] >> nth=-1",
        "[data-turn-role='model'] >> nth=-1",
        ".model-response >> nth=-1",
    ],

    # Authentication / session indicators
    "user_avatar": [
        'button[aria-label*="account" i]',
        'button[aria-label*="profile" i]',
        '[data-testid*="avatar" i]',
        'img[alt*="avatar" i]',
    ],
    "sign_in_button": [
        'button:has-text("Sign in")',
        'a:has-text("Sign in")',
        '[href*="accounts.google.com"]',
    ],

    # Prompt/input area
    "prompt_textarea": [
        'textarea[aria-label*="prompt" i]',
        'textarea[placeholder*="prompt" i]',
        '[contenteditable="true"][data-placeholder*="prompt" i]',
    ],
}

# Browser launch arguments
BROWSER_ARGS = [
    "--start-maximized",
]

# State tracking for completion detection
class GenerationState:
    IDLE = "idle"           # Run button visible, ready
    GENERATING = "generating"  # Stop button visible, generation in progress
    COMPLETE = "complete"      # Run button returned, generation done
    UNKNOWN = "unknown"