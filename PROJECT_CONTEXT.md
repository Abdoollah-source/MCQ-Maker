# PROJECT_CONTEXT.md — MCQ Maker

> **Purpose:** This document allows any AI assistant (or new developer) with zero prior knowledge to immediately understand and contribute to this codebase at full effectiveness. It is intentionally exhaustive and evaluative, not just descriptive.

---

## 🗺️ Project Overview

### What it does and who it's for

**MCQ Maker** is a Windows-only desktop application built in Python + PySide6 (Qt Widgets). It turns structured quiz JSON into standalone, self-contained HTML exam files. Its primary users are **medical educators and lecturers** who need to generate multiple-choice question (MCQ) exams from either:

1. Manually pasted or imported JSON quiz data, or
2. Lecture PDFs processed automatically through AI (Google Gemini, OpenAI, Anthropic, or OpenRouter).

The target user is non-technical: they paste a quiz, click Generate, and get a polished exam HTML file they can share with students. There is no server, no cloud account required (except optional AI keys), and no install complications — it runs as a silent Windows desktop app with a system tray.

### The core problem it solves

Medical lecturers typically create MCQs in Word documents or receive them from AI in raw JSON. Getting those questions into a browser-ready, printable, interactive exam requires either web development knowledge or clunky manual formatting. MCQ Maker eliminates that gap: paste JSON → validate → inject into an HTML template → save. The app also closes the loop on batch AI generation from lecture PDFs, a workflow that would otherwise require manual API scripting per file.

### Current overall status

**Functionally complete MVP / near-production.** All 11 planned modules have been implemented and a signed-ready (but currently unsigned) PyInstaller + Inno Setup installer exists (`release/MCQ-Maker-Setup-0.1.0.exe`, ~31 MB). The app is stable and fully featured. What remains is primarily: code signing for public distribution, a few known UX rough edges, and no formal user testing beyond the developer's own verification.

### North star goal

A local-first, zero-dependency Windows tool that educators trust: they install it, add a Gemini key, point it at a folder of lectures, and come back to find a set of validated HTML exams. The AI generation batch workflow is the flagship differentiated feature. The app should feel native and trustworthy — not like a Python script in a trench coat.

---

## 📁 Codebase Map

### Directory structure

```
MCQ Maker/                          ← project root
├── mcq_maker/                      ← THE main Python package (all application code lives here)
│   ├── __init__.py                 ← package marker, trivial
│   ├── __main__.py                 ← allows `python -m mcq_maker` to run the app
│   ├── app.py                      ★ ENTRY POINT — wires every service, calls QApplication.exec()
│   ├── shell.py                    ★ MOST CRITICAL — main window, nav, CreatePage, all page orchestration (915 lines)
│   ├── theme.py                    ★ complete palette + QSS stylesheet for light and dark mode
│   ├── components.py               ← shared UI primitives: Dropdown, AppButton, AnimatedProgressBar, label(), button(), panel()
│   │
│   ├── quiz_validation.py          ★ CORE LOGIC — JSON extraction, jsonschema validation, QuizError, QuizValidation
│   ├── exam_generator.py           ★ CORE LOGIC — HTML injection, filename rules, atomic save_exam()
│   ├── template_validation.py      ★ CORE LOGIC — HTML parser contract, TemplateContract, START/END markers
│   ├── template_repository.py      ★ STORAGE — UUID-based template library, atomic writes, catalog.json, recovery
│   │
│   ├── history.py                  ← SQLite history repo; records every generation attempt (metadata only)
│   ├── history_page.py             ← History UI page (search, table, open/export/remove)
│   │
│   ├── folder_scan.py              ← cancellable QRunnable worker: scans folder, processes every quiz file
│   ├── folder_scan_page.py         ← Folder Scan UI page (browse, run, results table, report)
│   │
│   ├── clipboard_watcher.py        ← opt-in automatic clipboard monitoring with debounce and conflict resolution
│   │
│   ├── ai_library.py               ← AI credential store: DPAPI-encrypted keys, prompts, reference files, library.json
│   ├── ai_generation.py            ← cancellable AI batch engine: key rotation, calibration, retries, saving
│   ├── ai_providers.py             ← provider HTTP adapters: Google, OpenAI Responses API, Anthropic, OpenRouter
│   ├── ai_generation_page.py       ← AI Generation UI page (folder, provider/model/prompt/reference pickers, live log)
│   ├── ai_library_dialog.py        ← AI Library dialog: manage API keys, prompts, reference files (tabbed QDialog)
│   │
│   ├── settings.py                 ← versioned settings JSON, atomic save, backup, SettingsStore class
│   ├── settings_dialog.py          ← Settings UI (nonmodal QDialog, live apply, single Done button)
│   ├── template_page.py            ← Templates UI page (import, rename, set default, preview, replace, remove)
│   │
│   ├── notifications.py            ← NotificationService: windows-toasts backend, foreground suppression, error throttling
│   ├── tray.py                     ← TrayController: QSystemTrayIcon, state badge, menu, watcher state sync
│   ├── startup.py                  ← Windows Registry "Start with Windows" (HKCU Run key)
│   ├── single_instance.py          ← QLocalServer enforcement: second launch signals the first and exits
│   ├── diagnostics.py              ← RotatingFileHandler for mcq_maker logger; captures uncaught exceptions
│   │
│   └── assets/
│       ├── standard_exam.html      ★ bundled default exam template (seeded on first launch)
│       ├── app_icon.ico / .png     ← application icon
│       ├── create/folder/history/templates/ai/settings.svg  ← Fluent Icons (nav bar + settings)
│       └── Fluent-Icons-LICENSE.txt
│
├── tests/                          ← pytest test suite (14 files, no GUI tests)
│   ├── test_quiz.py                ← quiz validation: schema, extraction, edge cases
│   ├── test_templates.py           ← template validation and repository
│   ├── test_history.py             ← SQLite history CRUD
│   ├── test_ai_generation.py       ← AI worker logic (mocked HTTP)
│   ├── test_clipboard_watcher.py   ← clipboard worker state machine
│   ├── test_folder_scan.py         ← folder scan worker
│   ├── test_settings.py            ← settings validation and recovery
│   ├── test_notifications.py       ← notification service (mocked backend)
│   ├── test_ai_library.py          ← AI library CRUD
│   ├── test_shell.py               ← minimal shell smoke tests (no full UI)
│   ├── test_tray.py                ← tray controller
│   ├── test_startup.py             ← Windows startup manager (mocked registry)
│   ├── test_single_instance.py     ← single instance enforcement
│   └── test_diagnostics.py        ← logging setup
│
├── tools/                          ← developer utility scripts (not part of the app)
│   ├── discover_google_models.py   ← list available Gemini models for a key
│   ├── smoke_google_generation.py  ← end-to-end AI generation smoke test
│   ├── render_ai_preview.py        ← render a sample exam for visual checking
│   ├── read_ai_failures.py         ← parse failed AI runs from history
│   ├── fetch_icons.py              ← download Fluent Icons SVGs
│   ├── generate_icon.py            ← generate app icon from SVG
│   └── check_pe_imports.py        ← verify frozen build DLL imports
│
├── installer/
│   └── MCQ Maker.iss               ← Inno Setup installer script
│
├── release/                        ← build artifacts (LARGE — mostly ignorable)
│   ├── MCQ-Maker-Setup-0.1.0.exe   ← 31 MB final installer (unsigned)
│   ├── MCQ Maker/                  ← the onedir PyInstaller build (frozen app)
│   ├── module11-*.png              ← screenshot artifacts from Module 11 verification
│   ├── test-result*.txt            ← test run outputs
│   └── [many tmp* dirs]            ← PyInstaller temp artifacts (ignored)
│
├── manual-test-data/folder-scan/   ← sample quiz files for manual folder-scan testing
├── .venv/                          ← development virtual environment
├── .venv-build/                    ← isolated PyInstaller build environment
├── .uv-cache/                      ← uv package manager cache
│
├── pyproject.toml                  ← project metadata, dependencies, build config
├── requirements.lock               ← exact pinned dev+build dependencies (Python 3.14)
├── MCQ Maker.spec                  ← PyInstaller spec file
├── launch_mcq_maker.pyw            ← source-tree launcher (no terminal window)
├── README.md                       ← user-facing README
├── THIRD-PARTY-NOTICES.txt         ← dependency license notices
│
├── Deviations.md                   ★ CRITICAL READING — every deliberate deviation from spec
├── Final Build Decisions.md        ★ CRITICAL READING — canonical design decisions for all subsystems
└── Visual and Interaction Design.md ← full color palette, typography, spacing, interaction spec
```

### Files to understand first (priority order)

1. **`Final Build Decisions.md`** — the authoritative design spec for every subsystem
2. **`Deviations.md`** — what changed from the spec and why; prevents re-litigating settled decisions
3. **`mcq_maker/app.py`** — 91-line startup wiring; shows all services and their dependency order
4. **`mcq_maker/shell.py`** — 915-line main window; everything navigable lives here
5. **`mcq_maker/quiz_validation.py`** — the schema and extraction rules that everything depends on
6. **`mcq_maker/template_validation.py`** — the HTML contract; understand START/END markers
7. **`mcq_maker/exam_generator.py`** — injection + safe atomic file saving
8. **`mcq_maker/template_repository.py`** — how templates are stored, recovered, and versioned
9. **`mcq_maker/ai_providers.py`** — all four provider HTTP adapters in one file
10. **`mcq_maker/ai_generation.py`** — the batch worker with key rotation and retry logic

### Files that are boilerplate/generated (can be ignored)

- `release/` — all PyInstaller build output and temp dirs
- `.venv/`, `.venv-build/`, `.uv-cache/` — virtual environments
- `mcq_maker/__pycache__/`, `tests/__pycache__/` — Python bytecode
- `artifacts/` — AI assistant session artifacts

### Files that are temporary or throwaway

- `release/qt_hello.py` and `qt_hello.spec` — a minimal Qt hello-world used to validate the PyInstaller pipeline before the full app was frozen
- `release/module11-*.png` — verification screenshots from Module 11 development
- `release/test-result*.txt` — snapshot test run output from development
- `release/desktop-error.log`, `release/shell-error.log` — leftover error log stubs

---

## 🏗️ Architecture & Design

### End-to-end system structure

```
User input (paste/file/clipboard/PDF)
        ↓
  quiz_validation.py     ← extract + schema validate → QuizValidation
        ↓
  exam_generator.py      ← inject payload into template → rendered HTML bytes
        ↓
  template_repository    ← read validated template HTML
        ↓
  Filesystem             ← atomic save via tempfile + os.replace/os.link
        ↓
  history.py             ← record attempt in SQLite
        ↓
  notifications.py       ← optional Windows toast
```

The **AI generation path** adds a pre-step:

```
Lecture PDF(s) + Reference file
        ↓
  ai_providers.py        ← calibrate (turn 1) + generate (turn 2) → raw JSON text
        ↓
  [same pipeline from quiz_validation onward]
```

### Data flow: how data moves through the app

1. **Input arrives** as a string (pasted, file-read, clipboard-read, or AI-returned JSON text).
2. **`validate_quiz(text, min_options, max_options)`** in `quiz_validation.py` extracts the JSON array (handling Markdown fences/prose wrapping), validates schema via `jsonschema`, and returns an immutable `QuizValidation(title, questions, payload, warnings)`.
3. **`save_exam(template, quiz, output_folder)`** in `exam_generator.py` calls `inject_quiz_data(template, quiz)` which does a string-offset replacement between `/* MCQ_MAKER_DATA_START */` and `/* MCQ_MAKER_DATA_END */`, serializes the payload with HTML-safe escaping, and writes the HTML file atomically using `tempfile.mkstemp` + `os.link`/`os.replace`.
4. **`HistoryRepository.record_success/failure()`** records metadata (no payloads ever) to SQLite.
5. **`NotificationService.notify()`** sends a Windows toast if the app is backgrounded.

### Key design patterns

| Pattern | Where | Why |
|---|---|---|
| QRunnable + QThreadPool | `clipboard_watcher.py`, `folder_scan.py`, `ai_generation.py`, `ai_library_dialog.py` | All I/O-blocking work runs off the GUI thread; results return via Qt Signals |
| Atomic file writes | `template_repository.py` (`atomic_write`), `exam_generator.py` (`save_exam`) | Prevents partial writes corrupting templates or exams; os.fsync + os.replace |
| Immutable result dataclasses | `QuizValidation`, `TemplateContract`, `ScanResult`, `AIResult`, `HistoryEntry`, `ClipboardJob` | Thread-safe signal payloads; prevents mutation after validation |
| Shared validation pipeline | `quiz_validation.validate_quiz()` called identically from all four intake modes | One source of truth; no mode has its own parser |
| Error hierarchy | `QuizError(ValueError)`, `TemplateError(ValueError)`, `ProviderError(RuntimeError)`, etc. | Catch by type at integration points; user-facing messages are in the exception string |
| Deferred lazy imports | `from .template_page import TemplatePage` inside `__init__`, dialogs imported inside methods | Avoids circular imports; dialog modules import from shell |
| Qt property-based styling | `setProperty('role', 'title')`, `setProperty('nav', True)`, `setProperty('primary', True)` | All visual variants are in `theme.py` QSS, not scattered in widget constructors |
| Recoverable storage | `TemplateRepository._recover_update()`, `SettingsStore.load()` corrupt-file recovery | Every writable file has a recovery path; no data loss on interrupted writes |

### Non-obvious architectural decisions

**HTML injection via string offset, not DOM parsing.** The template contract uses `template.index(START)` and string slicing. This is intentional: it preserves every byte of the template outside the marker bounds (comments, custom attributes, whitespace — everything) without any HTML re-serialization risk. A DOM parser would subtly change the template.

**No SDK dependencies for AI providers.** All four AI providers use Python's stdlib `urllib.request`. No `openai`, `anthropic`, or `google-generativeai` packages. This keeps the installed app small, avoids fast-changing SDK API churn, and makes the provider adapters the single integration boundary. The cost: manual HTTP error handling and base64 file upload.

**DPAPI for API key encryption.** Keys are encrypted with Windows `CryptProtectData` (user-scoped) before being stored in `library.json`. The decrypted key never touches disk. This is Windows-specific by design — managed profiles without a user DPAPI master key fall back to machine-scope DPAPI + LocalAppData ACL.

**Two-turn conversation protocol for AI generation.** Each lecture uses two API turns: Turn 1 sends a reference document with "study this for calibration" instructions, Turn 2 sends the lecture PDF with "generate now" instructions. A fresh conversation starts after any error, because a failed turn could poison context for subsequent lectures.

**`os.link` before `os.replace` for concurrent-safe file naming.** `save_exam` uses `tempfile.mkstemp` to write the content, then `os.link(temporary, target)` which fails atomically if `target` already exists. This prevents two simultaneous generations from overwriting each other. On filesystems that don't support hard links, it falls back to `os.open(O_CREAT | O_EXCL)` as a reservation.

**`platformdirs.user_data_path`** resolves to `%LOCALAPPDATA%\MCQ Maker`. Templates, settings, history, and AI library live here. The output folder defaults to `Documents\MCQ Maker Exams` but is user-configurable. These are intentionally separate locations.

### External services / APIs

| Service | Module | Integration |
|---|---|---|
| Google Gemini | `ai_providers.py` GoogleSession | REST: generativelanguage.googleapis.com/v1beta/models/{model}:generateContent |
| OpenAI Responses API | `ai_providers.py` OpenAIResponsesSession | REST: api.openai.com/v1/responses (stateful, uses previous_response_id) |
| Anthropic | `ai_providers.py` AnthropicSession | REST: api.anthropic.com/v1/messages |
| OpenRouter | `ai_providers.py` OpenRouterSession | REST: openrouter.ai/api/v1/chat/completions (OpenAI-compatible) |
| Windows DPAPI | `ai_library.py` via ctypes | crypt32.dll CryptProtectData/CryptUnprotectData |
| Windows Registry | `startup.py` via winreg | HKCU\Software\Microsoft\Windows\CurrentVersion\Run |
| Windows Notifications | `notifications.py` via windows-toasts | WinRT Windows.UI.Notifications |
| Qt Local Socket IPC | `single_instance.py` | QLocalServer/QLocalSocket — second instance sends b'open' to the first |

---

## ⚙️ Tech Stack

### Dependencies

| Library | Version | Why |
|---|---|---|
| **PySide6-Essentials** | 6.10.2 | Qt Widgets UI toolkit — chosen over tkinter (too basic), wxPython (maintenance risk), Electron (too heavy); Essentials instead of full PySide6 to exclude Qt WebEngine |
| **platformdirs** | 4.11.8 | Cross-platform %LOCALAPPDATA% resolution without hardcoding |
| **jsonschema** | 4.26.0 | Draft202012Validator for quiz structure validation; iter_errors API collects all errors at once |
| **windows-toasts** | 1.3.1 | WinRT-backed Windows 10/11 toast notifications with proper activation callbacks |
| **PyInstaller** | 6.20.0 | --onedir bundling; listed as [build] optional dep, not runtime |

**Runtime stdlib modules heavily relied upon:** `sqlite3`, `json`, `pathlib`, `threading`, `tempfile`, `urllib.request`, `ctypes`, `winreg`, `hashlib`, `re`, `html.parser`, `csv`, `logging`

### Why Python 3.12–3.14

`pyproject.toml` declares `requires-python = ">=3.12,<3.15"`. The lock file comment says Python 3.14 was used for development. Python 3.12+ is required for PEP 701 f-strings with expressions used in several places. The frozen app bundles its own Python so end users don't need Python installed.

### Environment setup (development)

```powershell
# Assumes Python 3.12+ on Windows
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[build]"   # or: uv pip install -e ".[build]"

# Run from source
python -m mcq_maker
# or without a terminal window
python launch_mcq_maker.pyw

# Run tests
pytest tests/

# Build frozen app (in isolated .venv-build)
python -m venv .venv-build
.\.venv-build\Scripts\Activate.ps1
pip install -e ".[build]"
pyinstaller "MCQ Maker.spec"
```

`launch_mcq_maker.pyw` is a 2-line file (`import runpy; runpy.run_module('mcq_maker', run_name='__main__')`) intended to run from the source tree without a terminal window.

---

## 📍 Current State

### What is fully working and solid

- ✅ **Create page (paste mode):** debounced validation, error details, template selection, atomic save, history recording, undo after clear
- ✅ **Folder scan:** cancellable, deterministic, recursive option, per-file results, history recording, batch-complete notification
- ✅ **Template repository:** import, rename, set default, preview, replace (with revision backup), remove (reversible archive), source-change detection, crash recovery via update journal
- ✅ **History:** SQLite WAL-mode, all source modes, CSV export, search, open-in-browser, show-in-folder, locate-file, remove
- ✅ **Settings:** all 10 settings fields, live apply, nonmodal window, atomic save with backup
- ✅ **System tray:** close-to-tray, clipboard watcher toggle, state badge, single one-time explanation
- ✅ **Clipboard watcher:** debounced (450ms), state machine (off/on/paused), conflict resolution, background save, history
- ✅ **Notifications:** foreground suppression (except batch-complete), error throttling (30s), toast activation routing
- ✅ **Single instance:** QLocalServer; second launch focuses existing window
- ✅ **AI generation (Module 11):** all four providers, two-turn calibration, key rotation, rate-limit wait with countdown, DPAPI key encryption, model discovery + caching, reference-per-lecture assignment, live log, cancellation
- ✅ **Responsive layout:** sidebar collapses to compact dropdown at <960px; Create page switches to single-column at <960px; History table collapses at <1000px
- ✅ **Dark mode:** full palette, auto-detect from Windows colorSchemeChanged signal
- ✅ **Bundled installer:** PyInstaller onedir + Inno Setup, per-user install to %LOCALAPPDATA%\Programs\MCQ Maker\

### What is partially working or half-implemented

- ⚠️ **OpenAI, Anthropic, OpenRouter AI providers:** adapters are coded and structurally correct, but they were *not verified end-to-end* during Module 11 development (only Google Gemini was). The Anthropic AnthropicSession._call handles non-PDF reference files by reading them as text — reasonable fallback but untested. OpenAI's OpenAIResponsesSession uses the Responses API with `previous_response_id` stateful chaining — this API is relatively new and may have changed.
- ⚠️ **ai_generation_page.py formatting:** written in a "compressed" style (many statements per line, minimal whitespace). Works but is significantly harder to read than the rest of the codebase.
- ⚠️ **AIGenerationPage template state:** the constructor receives settings but starts with empty template_entries. Templates arrive later via set_template_snapshot() called from MainWindow.update_templates(). Run button stays disabled until templates load — correct behavior, indirect wiring.

### What is broken, stubbed out, or known to be wrong

- ❌ **Backup export/restore:** explicitly deferred. Deviations.md Module 10 confirms conscious omission. Atomic backups exist on disk but there is no UI to export or restore a full app backup archive.
- ❌ **Continuous folder watching:** the spec lists it as "deferred." No QFileSystemWatcher usage. Folder scan is always manual and one-shot.
- ❌ **Rich content in questions:** the quiz schema requires plain-text strings. The HTML template uses textContent (not innerHTML), so HTML in question text renders literally. Rich HTML, formula rendering, and image questions are explicitly deferred.
- ❌ **MODELS dict in ai_providers.py has placeholder/future model names:** gemini-3.5-flash-lite, gpt-5-mini, claude-sonnet-5, gemini-3.8-flash. Google uses live discovery; other providers return these static defaults. Do not treat these as real current model names.
- ❌ **AnthropicSession `output_config: {'effort': self.thinking}`:** this payload field is not a standard Anthropic API field — modeled after OpenAI's reasoning effort and may be silently ignored.

### What is a hack or workaround that needs revisiting

- 🔧 **Dropdown._place_popup** in components.py: a QTimer.singleShot(0, ...) deferred popup repositioning hack to fix Qt's native QComboBox popup placement. Fragile — relies on the popup being visible in the next event loop iteration.
- 🔧 **fluent_icon() in shell.py:** SVG icons loaded and their fill="#212121" is string-replaced. Works because Fluent Icons SVGs use exactly that fill — would break silently for any icon with a different fill.
- 🔧 **ai_generation_page.py compressed style:** working code that violates the project's readability standard.
- 🔧 **Static MODELS for non-Google providers:** placeholder model IDs until live discovery is added.

### Technical debt

- **No automated UI tests:** test_shell.py exists but is minimal. No QApplication-level tests for CreatePage, FolderScanPage, HistoryPage, AIGenerationPage, or TemplatePage.
- **ai_generation_page.py code quality:** worst-formatted file in the project by a large margin. Reformat before touching.
- **No model enumeration for non-Google providers:** model dropdown shows static placeholder names until live discovery is added.
- **revision vs version key mismatch:** template metadata stores `revision` (increments on Replace) but the dict passed to history uses `template_entry.get('version', 1)`. Harmless (defaults to 1) but could confuse future tracking.

---

## 🚧 Active Work

Based on repository evidence, **Module 11 (AI generation) was the most recently completed work.** Screenshots `release/module11-ai-generation.png` and `release/module11-ai-library.png` confirm this was the last module verified.

### Recently touched files (inferred)

- `ai_providers.py` — model names, provider session implementations
- `ai_generation.py` — batch worker retry/rotation logic
- `ai_generation_page.py` — AI UI page
- `ai_library.py` — DPAPI encryption, prompt versioning, reference management
- `ai_library_dialog.py` — management dialog with provider tabs

### Unresolved mid-thought decisions

- Whether to add live model discovery for OpenAI, Anthropic, and OpenRouter (only Google does this currently). The groundwork exists (set_models() in AILibrary, models dict in library.json), but the trigger only fires for Google.
- The MODELS dict placeholder model names suggest this was intentionally left incomplete with the expectation of future updates.

---

## 🔮 Roadmap & Next Steps

### Immediate (priority order)

1. **Code signing** — the installer is unsigned. Any public distribution requires an EV code signing certificate. This is a business/process step, not a code step.
2. **Live model discovery for non-Google providers** — extend the "Test connection" flow in ai_library_dialog.py to call list_models() for OpenAI, Anthropic, and OpenRouter and cache the result the same way Google does.
3. **Reformat ai_generation_page.py** — functionally correct but violates readability standards. Refactor-only, no logic changes.
4. **End-to-end verification of non-Google providers** — OpenAI, Anthropic, and OpenRouter were coded but not tested against live APIs. Need smoke tests analogous to tools/smoke_google_generation.py.
5. **UI test harness** — even basic QTest smoke tests for CreatePage and AIGenerationPage would catch regressions.

### Medium-term

- **Backup export/restore UI** — allow users to migrate template library, AI library, history, and settings to a new machine.
- **Continuous folder watching** — QFileSystemWatcher integration; respond to new quiz files automatically.
- **Rich content / image questions** — schema changes, file management, template updates.
- **Per-batch template selection** — folder scan and AI generation currently always use the default template.

### Known future complexity

- **Public distribution / auto-update:** code signing + distribution channel + update mechanism. None exist yet.
- **Schema versioning for quiz format:** if the JSON schema changes, existing quiz files become incompatible. No migration path today.
- **Template format versioning beyond v1:** mcq-maker-template-version is checked for exactly "1". A v2 format needs careful backward compatibility planning.

---

## ⚠️ Gotchas & Important Context

### Things that will trip you up

**1. `correct` must be `type(correct) is int`, not just truthy.** The validator explicitly rejects booleans (True/False are int subclasses in Python, but `type(True) is int` is False). This is intentional per spec. Do not weaken this check.

**2. The HTML template injection is string-offset-based, not DOM-based.** Do not refactor inject_quiz_data to use an HTML parser or BeautifulSoup. The design relies on preserving every byte outside the markers. See exam_generator.py lines 22–27.

**3. `validate_template` is called three times during generation:** at import, during read_template, and inside inject_quiz_data. Intentional defensive programming — the stored template could be corrupted between import and use.

**4. The template catalog.json stores the default_id.** The settings file does NOT store the default template. There is one source of truth: templates/catalog.json. See Deviations.md Module 2.

**5. `QuizValidation.payload` includes the title object.** payload[0] is {"title": "..."}, payload[1:] are the questions. When injecting, the whole payload (including title) is serialized. The template JavaScript does quizData[0]?.title and quizData.slice(1). Don't strip the title from the payload.

**6. `serialize_payload` escapes `/` as `\/`.** Not standard but valid per the JSON spec. It prevents `</script>` from appearing literally in the injected data. Do not remove this escaping.

**7. `SingleInstance.acquire()` returns True if this IS the primary instance** (continue) and False if another is running (exit). The return semantics are "proceed = True."

**8. The AI generation page requires template state to be pushed to it via `set_template_snapshot`.** AIGenerationPage does not read the template repository directly — it receives a pre-computed snapshot from MainWindow.update_templates. The _ready() check validates self.template_entries.get(self.default_template_id, {}).get('valid', False).

**9. Clipboard watcher ignores startup clipboard content.** The watcher only reacts to QClipboard.dataChanged() signals, not the initial clipboard state. Intentional (see Deviations.md Module 8). "Process Clipboard Now" handles existing content deliberately.

**10. `recognizable_quiz_text()` is a conservative filter.** It requires both a title key AND at least one of question, options, correct. Lets non-quiz clipboard text pass silently. Actual validation runs in the prepare worker.

**11. `Dropdown._place_popup` uses `QTimer.singleShot(0, ...)`** — a deferred popup repositioning hack. If a dropdown popup appears in the wrong position, this is the code to look at.

**12. `ai_providers.py` MODELS dict contains placeholder/future model names.** gemini-3.5-flash-lite is the real Google default; the others (gpt-5-mini, claude-sonnet-5, gemini-3.8-flash) are illustrative. Use live list_models() for actual model selection.

**13. All AI file uploads are base64-encoded inline data.** No server-side file management. Files are base64'd in the request body. The 50 MB limit applies per file. Stateless but limits file sizes.

**14. `history.py` uses RLock for thread safety.** All SQLite reads and writes go through this lock. History is called from multiple background workers concurrently. Do not remove the lock.

**15. `atomic_write` uses tempfile.mkstemp in the same directory as the target.** This ensures the rename is within-filesystem (os.replace is atomic only within the same filesystem). Do not change the temp file location to a different drive.

---

## 💬 Conventions & Style

### Naming conventions

- **Module names:** snake_case.py, matching the feature (e.g., ai_generation_page.py for the AI generation UI page)
- **Classes:** PascalCase — QuizValidation, TemplateRepository, AIGenerationWorker, ClipboardWatcher
- **Exception classes:** named with Error suffix — QuizError, TemplateError, ProviderError, AILibraryError, HistoryError
- **Qt Signals:** lowercase with underscores — history_changed, manage_requested, notification_requested
- **Private methods:** leading underscore — _ready(), _session(), _rate_limited(), _catalog()
- **Worker signals objects:** inner class named *Signals (e.g., AISignals, ScanSignals, _WorkerSignals)
- **Worker runnables:** named *Worker (e.g., AIGenerationWorker, FolderScanWorker, _PrepareWorker)

### Code style preferences

- **Module docstrings:** every module has a one-line docstring describing its responsibility. Follow this pattern.
- **Method docstrings:** used sparingly — mostly on module-level functions in logic-heavy modules. Not in UI code.
- **Type annotations:** used in logic-heavy modules (quiz_validation.py, exam_generator.py, template_validation.py). Not in UI code.
- **`from __future__ import annotations`:** used where type annotations reference forward-declared types.
- **`@dataclass(frozen=True)`:** all result types are frozen dataclasses for thread safety and immutability.
- **Import ordering:** stdlib → third-party → local package, blank-line separated, sorted within groups.
- **Qt signal connections:** always explicit method references. Lambdas only for simple index captures in loops.

### Patterns to keep consistent

1. **All blocking I/O in `QRunnable.run()`**, results returned via Signal.emit().
2. **User-facing error messages go in exception strings.** QuizError("Fix 3 items before generating.") is the text the user sees. Keep messages clear, actionable, and jargon-free.
3. **Validation errors are collected and shown together** — validate_quiz collects all messages then raises one QuizError with '\n'.join(unique). Never show one error at a time when multiple exist.
4. **The `panel()` function from components.py** returns (frame, layout) and is the standard way to create bordered content panels. Use it consistently.
5. **Settings are always passed as plain dict** between modules. SettingsStore is only accessed in app.py and shell.py.
6. **Every storage write uses `atomic_write()`** from template_repository.py. It is a project-wide utility.
7. **`QTimer.singleShot(0, callback)`** for deferred-until-event-loop operations. Established pattern.
8. **All visual styling is in `theme.py`.** There is no inline setStyleSheet anywhere except theme.py itself.
9. **Never log quiz content, clipboard text, or AI responses.** Log filenames, counts, and error summaries only.

---

## 🗂️ Data Storage Locations

All application data is in `%LOCALAPPDATA%\MCQ Maker\` (resolved by platformdirs):

```
%LOCALAPPDATA%\MCQ Maker\
├── settings.json               ← user preferences
├── settings.backup.json        ← last-good settings backup
├── history.sqlite3             ← generation history (WAL mode)
├── templates\
│   ├── catalog.json            ← default_id and seeded flag
│   ├── {uuid}\
│   │   ├── template.html       ← imported template (UTF-8, LF)
│   │   ├── metadata.json       ← all metadata including sha256 and contract
│   │   ├── revisions\          ← backup copies before each Replace
│   │   └── update.json         ← crash-recovery journal (deleted on clean finish)
│   └── .removed\               ← archived (removed) templates
├── ai\
│   ├── library.json            ← keys (DPAPI-encrypted), prompts, references metadata
│   ├── library.backup.json     ← last-good library backup
│   └── references\             ← copied reference files ({uuid}.pdf/.txt/.md)
├── previews\                   ← rendered template preview HTML files
└── logs\
    ├── mcq-maker.log           ← rotating log (512KB x 3 backups)
    ├── mcq-maker.log.1
    └── mcq-maker.log.2
```

Generated exams go to the **output folder** (default: `Documents\MCQ Maker Exams\`) — completely separate from application storage, user-configurable.

---

*Last updated: September 2026. Written by code analysis of the complete mcq_maker/ package and all design documents.*
