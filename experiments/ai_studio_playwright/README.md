# Playwright AI Studio Feasibility Probe

Disposable prototype to answer 8 feasibility questions for AI Studio automation.
**Does not integrate with MCQ Maker.** Runs entirely inside `experiments/ai_studio_playwright/`.

## Setup

```powershell
cd experiments/ai_studio_playwright
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
```

## Usage

```powershell
# 1. First run: manual Google sign-in (session saved to profile_chromium/)
python probe.py login

# 2. Verify session persists across launches
python probe.py check

# 3. Inspect live AI Studio DOM for selectors (keep browser open, use DevTools)
python probe.py inspect

# 4. Full generation test: upload PDF, run, extract response
python probe.py run path\to\lecture.pdf

# Optional: override AI Studio URL (e.g., saved prompt URL)
# PowerShell syntax:
$env:AI_STUDIO_URL = "https://aistudio.google.com/prompts/xxx"; python probe.py run lecture.pdf
# Or use the --url option (recommended):
python probe.py run lecture.pdf --url https://aistudio.google.com/prompts/xxx

# --- System Google Chrome Channel (if bundled Chromium blocked) ---
# Requires: playwright install chrome
# Uses separate profile_chrome/ directory (no cross-contamination)

# 1. Manual Google sign-in with system Chrome
python probe.py login --browser chrome

# 2. Verify Chrome session persists
python probe.py check --browser chrome

# 3. Inspect with Chrome
python probe.py inspect --browser chrome

# 4. Full generation test with Chrome
python probe.py run path\to\lecture.pdf --browser chrome

# --- Attach to Already-Running Chrome (CDP) ---
# Precondition: Start Chrome manually with remote debugging:
#   chrome.exe --remote-debugging-port=9222 --user-data-dir="C:\path\to\profile"
# Then sign into Google, open AI Studio, then run:

# Attach to running Chrome, list tabs, detect AI Studio
python probe.py attach

# Read-only DOM inspection of already-open AI Studio tab via CDP
python probe.py inspect-attached

# Read-only inspection of Run Settings panel controls via CDP
python probe.py inspect-settings-attached

# Structural read-only inspection of visible Run Settings panel via CDP
python probe.py inspect-settings-structure-attached

# DOM diff discovery of Run Settings panel via toggle click
python probe.py discover-settings-panel-attached

# Upload PDF to already-open AI Studio tab via CDP (no Run click)
python probe.py upload-attached path\to\lecture.pdf

# Click Run and detect generation start via CDP (no completion wait)
python probe.py start-attached

# Custom CDP endpoint
python probe.py attach --cdp-url http://127.0.0.1:9222
python probe.py inspect-attached --cdp-url http://127.0.0.1:9222
python probe.py inspect-settings-attached --cdp-url http://127.0.0.1:9222
python probe.py inspect-settings-structure-attached --cdp-url http://127.0.0.1:9222
python probe.py discover-settings-panel-attached --cdp-url http://127.0.0.1:9222
python probe.py upload-attached path\to\lecture.pdf --cdp-url http://127.0.0.1:9222
python probe.py start-attached --cdp-url http://127.0.0.1:9222
```

## Modes

| Mode | Purpose |
|------|---------|
| `login` | Open persistent browser, manual sign-in, session saved to `profile_chromium/` or `profile_chrome/` |
| `check` | Reopen profile, verify auth persists, confirm AI Studio loads |
| `inspect` | Open AI Studio, auto-discover accessible selectors, keep open for DevTools |
| `run` | Upload PDF → click Run → wait for completion → extract response → save to `artifacts/` |
| `attach` | Connect to running Chrome via CDP (port 9222), list pages, detect AI Studio tab |
| `inspect-attached` | Connect via CDP, find AI Studio tab, read-only DOM inspection, save JSON report to `artifacts/` |
| `inspect-settings-attached` | Connect via CDP, find AI Studio tab, read-only Run Settings controls inspection, save JSON report to `artifacts/` |
| `inspect-settings-structure-attached` | Connect via CDP, structural inspection of visible Run Settings panel (labels, containers, interactive controls), save JSON report to `artifacts/` |
| `discover-settings-panel-attached` | Connect via CDP, click Run Settings toggle, DOM diff to identify panel, restore state, save JSON report to `artifacts/` |
| `upload-attached` | Connect via CDP, upload PDF to file input, verify acknowledgment, save JSON report to `artifacts/` |
| `start-attached` | Connect via CDP, click Run, detect generation start (≤30s), save JSON report to `artifacts/` |

## Feasibility Report Template

After running all modes, fill in results:

### Login Persistence
- [ ] **PASS** — Manual sign-in once, session persists across `check` runs
- [ ] **PARTIAL** — Session works but requires occasional re-auth
- [ ] **FAIL** — Google blocks automated Chromium or session never persists

**Notes:** ____________________________________________________________

### AI Studio Navigation
- [ ] **PASS** — Loads successfully in persistent profile
- [ ] **FAIL** — Blocked, redirected, or broken

**Notes:** ____________________________________________________________

### PDF Upload
- [ ] **PASS** — Mechanism 1 (direct `input[type="file"]`) works
- [ ] **PASS** — Mechanism 2 (`expect_file_chooser` + Add Media) works
- [ ] **FAIL** — Neither mechanism works

**Working mechanism:** ________________________________________________

**Selector used:** ____________________________________________________

### Run Control
- [ ] **PASS** — Run button found and clicked via accessible selector
- [ ] **FAIL** — Run button not found or click fails

**Selector used:** ____________________________________________________

### Completion Detection
- [ ] **PASS** — Detects Run → Stop → Run transition reliably
- [ ] **PARTIAL** — Works but timing issues or false positives
- [ ] **FAIL** — Cannot detect generation state changes

**Method:** _________________________________________________________

**Selector used for state:** _________________________________________

### Response Extraction
- [ ] **PASS** — Final model response extracted via accessible selector
- [ ] **PARTIAL** — Extracted but includes extra UI chrome
- [ ] **FAIL** — Cannot locate response container

**Selector used:** ____________________________________________________

**Sample output saved to:** `artifacts\<timestamp>_response.txt` (raw text) and `artifacts\<timestamp>_meta.json` (metadata)

---

## Discovered Selectors (fill after `inspect` mode)

| Control | Selector | Method |
|---------|----------|--------|
| Add Media Button | | `aria-label` / role |
| File Input (direct) | | `input[type="file"]` |
| Run Button | | `aria-label` / role |
| Stop Button | | `aria-label` / role |
| Response Container | | `data-role` / class |

---

## Known Issues / Fragile Points

- ____________________________________________________________
- ____________________________________________________________
- ____________________________________________________________

## Google Blocking Behavior

- [ ] No blocking observed
- [ ] Sign-in blocked for automated Chromium
- [ ] AI Studio access blocked after sign-in
- [ ] Other: ________________________________________________

## Next Step Recommendation

- [ ] Proceed with Playwright automation (bundled Chromium works)
- [ ] Test system Chrome channel (`playwright install chrome`)
- [ ] Abandon Playwright approach; use Extension + Clipboard only
- [ ] Other: ________________________________________________

---

## Directory Structure

```
experiments/ai_studio_playwright/
├── README.md               # This file
├── config.py               # Selectors, timeouts, URLs
├── probe.py                # Main CLI (6 modes)
├── requirements.txt        # playwright
├── profile_chromium/       # Persistent bundled Chromium profile (gitignored)
├── profile_chrome/         # Persistent system Chrome profile (gitignored)
└── artifacts/              # Extracted responses + inspection reports (gitignored)
```

## Important Notes

- **Headed mode only** — browser window always visible
- **Dedicated profiles** — `profile_chromium/` for bundled, `profile_chrome/` for system Chrome; never uses your normal Chrome profile
- **Bundled Chromium** — `playwright install chromium` (default)
- **System Chrome** — `playwright install chrome` (if bundled blocked)
- **No workarounds** — if Google blocks automated browser, document it
- **Accessible selectors preferred** — `get_by_role`, `get_by_label`, `aria-label`
- **No MCQ Maker integration** — this is a standalone feasibility test