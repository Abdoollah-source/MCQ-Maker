# MCQ Maker

The two specification documents beside this file remain the product references. Implementation changes are recorded in `Deviations.md`.

## Development

Double-click **Open MCQ Maker.lnk** in this folder to open the preview without a terminal. It launches the isolated `.venv` environment. The user does not need to install dependencies or run commands.

Developer entry point: `.venv\Scripts\pythonw.exe -m mcq_maker`. Visual verification switches: `--theme light`, `--theme dark`, and `--compact-preview`. Dependencies are pinned in `requirements.lock`.

## Module gates

1. App shell — built, user-confirmed, and complete.
2. Template manager — built, user-confirmed, and complete.
3. Paste mode — built, user-confirmed, and complete.
4. Settings — built, user-confirmed, and complete.
5. Folder scan — built, user-confirmed, and complete.
6. History — built, user-confirmed, and complete.
7. Tray — built, user-confirmed, and complete.
8. Clipboard watcher — built, user-confirmed, and complete.
9. Notifications — built, user-confirmed, and complete.
10. Polish — built, user-confirmed, and complete.
11. AI generation — built; awaiting user confirmation.

## Module 1 verification — September 13, 2026

- Six executable checks passed: initial outer dimensions and centering, shell navigation and inactive business actions, responsive navigation, Enter activation, dark-theme state preservation, and close behavior.
- Visually inspected the running Windows app using desktop control: Create exam, Folder scan/report, History, Templates, light appearance, dark appearance, and the compact stacked layout.
- Clicked all navigation destinations and the History return action; verified compact Navigate menu selection and scrolling to the final Save details field.
- Verified keyboard focus and Enter activation, native maximize/restore, close, and launching from the no-terminal shortcut.
- Measured enabled text/status/control-border contrast in both palettes; all checked pairs meet the specification.
- Business functionality is deliberately unavailable. Settings storage, window-position persistence, tray behavior, and final motion belong to later modules.

## Module 2 verification — September 13, 2026

- Twenty-eight executable checks passed: template contract validation, UTF-8/BOM and Windows line-ending handling, line-numbered link warnings, expandable warning rows, preview generation, independent stored copies, rename/default rules, source-change detection, interrupted-update recovery, reversible removal, and all earlier shell checks.
- The manager stores a bundled Standard exam template in `%LOCALAPPDATA%\\MCQ Maker\\templates`; its HTML preview opens independently and uses two sample questions, including a right-to-left Arabic question with ten options.
- The user will perform future desktop interaction checks and report the result before the next module begins.

## Module 3 verification — September 14, 2026

- Thirty-eight executable checks passed. The new checks cover plain JSON, Markdown-fenced and prose-wrapped arrays, strict JSON failures, all structural error reporting, four-option template limits, warnings, safe injection, Unicode Windows filenames, collision copies, output-path limits, successful saving, automatic clearing, Ctrl+Z restoration, the live connection from the template library into the Create exam workflow, and opening the active output folder.
- `jsonschema` is pinned in the project dependency list and lock file. It validates the quiz shape before MCQ Maker accesses the selected template or output folder.
- Manual output currently uses `Documents\\MCQ Maker Exams`. The Settings module will make this path configurable and persist it.

## Module 4 verification — September 14, 2026

- Forty-seven executable checks passed. The settings checks cover defaults, validation, atomic save and backup, corrupt-file recovery, every visible control, settings application to the Create exam page, window-geometry persistence, explicit replacement of a conflicting manual output, prevention of sideways scrolling, readable control and menu-row heights, consistent popup direction, and immediate saved theme changes.
- Settings load from `%LOCALAPPDATA%\\MCQ Maker\\settings.json`; each later successful change first keeps `settings.backup.json` as the previous known-good version. Damaged settings are preserved under a timestamped `settings.corrupt.*.json` filename before defaults are recovered.
- Clipboard watching, tray close behavior, Windows startup registration, and notification delivery are saved now and become active only when their respective modules are built.
- Settings use a 720 × 640 owned window with three single-column sections. Valid changes save immediately; the theme switches while the window remains open, and a full-width layout prevents labels from sliding beneath the left edge.
- Current and future screens use the same shared label, button, panel, and drop-down primitives. Drop-down placement depends only on available screen space, so changing the selected item cannot make a menu jump between opening above and below its field.

## Module 5 verification — September 14, 2026

- Fifty-one executable checks passed. Folder scan checks cover deterministic extension filtering, optional recursion, UTF-8 and UTF-8 BOM input, shared strict validation, Markdown/prose extraction, title-based filenames, collision-safe numbered copies, cancellation, per-file failures, copied reports that exclude question contents, and opening the output folder used by the completed scan.
- Scans run in a single background worker and report progress without blocking navigation. Each supported source file receives an explicit Created, Failed, or Skipped result; failures start expanded with the full reason, while successes start collapsed.
- The page uses the configured default template, output folder, and subfolder preference. A scan keeps its starting template and output destination even if Settings changes while it runs.
- Ready-made manual fixtures are in `manual-test-data\folder-scan`: two valid top-level quizzes, one invalid top-level file, one ignored CSV, and one valid nested quiz.

## Module 6 verification — September 14, 2026

- Fifty-six executable checks passed. History checks cover schema creation and versioning, manual and folder-scan integration, successful and failed attempts, metadata-only storage, deterministic newest-first loading, search, responsive rows, double-click opening, missing-file relocation, showing the containing folder, filtered CSV export, entry removal without deleting the exam, damaged-database handling, and serialized concurrent writes.
- History is stored at `%LOCALAPPDATA%\MCQ Maker\history.sqlite3` with SQLite transactions, a schema version, a write lock, and short-lived connections that are explicitly closed so Windows never keeps the database locked after an operation.
- Every new successful generation records its filename, path, local-display timestamp, template identity, question count, and source mode after the output commit. Folder and background failures store concise reasons without retaining question payloads.

## Module 7 verification — September 14, 2026

- Fifty-nine executable checks passed. Tray checks cover the exact Module 7 menu, the deliberately inactive watcher command, reopening the existing window, close-to-tray behavior, its one-time explanation, fallback when Windows has no usable tray, and the quit request path.
- The tray uses the same answer-sheet icon as the main window. Choosing **Keep running in the system tray** shows it immediately; closing then hides the window while keeping all active work alive.
- **Quit** first stops a folder scan if one is running, waits for folder and template work to become quiet, persists the window position, removes the tray icon, and exits.

## Module 8 verification — September 14, 2026

- Sixty-six executable checks passed. Clipboard checks cover conservative quiz recognition, per-copy debouncing, repeated identical copies, shared template and quiz validation, background saving, newest-item queuing, invalid-input History entries without raw question text, successful History entries, conflict choices, pause and resume, persisted enabling from the tray, and Process Clipboard Now.
- Clipboard watching remains off until explicitly enabled in Settings or from the tray. Ordinary copied text is ignored; recognizable invalid quiz data records a concise failure, while valid data uses the default template and configured output folder.
- The sidebar, compact navigation, tray menu text, tray tooltip, and tray badge all track **Off**, **On**, or **Paused**. Existing filenames follow the saved policy: **Always save a numbered copy** proceeds automatically, while **Ask before replacing** opens the three-choice conflict dialog. `Ctrl+Shift+V` and the tray command open the clipboard text in Create exam for review without enabling automatic watching.
- Successful saves and background errors emit notification requests with no raw quiz content. Module 9 will connect those requests to the final Windows notification service.

## Module 9 verification — September 14, 2026

- Seventy-two executable checks passed. Notification checks cover clipboard success, batch completion, background failure, unexpected watcher shutdown, settings-based suppression, foreground suppression, optional sound, repeated-error throttling and summaries, activation routing, and failure isolation.
- `windows-toasts` 1.3.1 and its WinRT dependencies are pinned in both project dependency files. The adapter supplies the MCQ Maker answer-sheet icon and keeps toast-library details outside generation code.
- Clicking a saved-exam notification restores MCQ Maker and filters History to that file. A batch notification opens the Folder scan report, and a stopped-watcher notification opens Settings.
- Notifications are silent by default. Clipboard and error notifications appear only while MCQ Maker is in the background; folder scans always send one completion notification and never send per-file notifications. Notification delivery never changes the result of generation or saving when Windows rejects it.

## Module 10 verification — September 14, 2026

- Eighty-one executable checks pass across the complete app. Final checks cover the useful example, recoverable Clear, file import and drag-and-drop, 300 ms validation feedback, error emphasis without lost input, real-count progress motion, keyboard shortcuts, Windows startup registration, single-instance restoration, and bounded local diagnostics.
- The Create exam page now supports **Try an example**, the working **Import file** action, dropping one `.json`, `.txt`, or `.md` file onto the editor, **Ctrl+Enter** to generate, and immediate Undo after Clear or a successful save. Ready and saved messages include the question count.
- `Ctrl+O` imports a quiz, `Ctrl+,` opens Settings, `Ctrl+F` opens and focuses History search, and `Ctrl+Shift+V` reviews the current clipboard. Shortcuts appear in the relevant tooltips.
- **Start with Windows** now creates or removes a per-user Windows startup registration. A second launch signals the existing process to restore its window and exits without creating another watcher or tray icon.
- A multi-size Windows icon, PyInstaller onedir build, per-user Inno Setup installer, and third-party notice are produced under `release`. The packaged executable passed a launch-health check.
- Diagnostic logs rotate at 512 KiB with three backups under `%LOCALAPPDATA%\MCQ Maker\logs`; application code never sends quiz or clipboard contents to the log.

The installed Module 10 build was confirmed by the user on September 14, 2026.

## Module 11 verification — September 14, 2026

- Module 11 adds an **AI generation** destination and a separate **AI library** window for multiple Google, OpenAI, Anthropic, and OpenRouter accounts, versioned prompts, and copied reference files.
- API keys are encrypted through Windows DPAPI before they enter `%LOCALAPPDATA%\MCQ Maker\ai\library.json`. Prompt text and reference metadata remain ordinary local data; reference files are copied into the managed `ai\references` folder.
- The batch engine groups lectures by the chosen conversation limit and reference, performs calibration before generation, validates every returned quiz through the existing strict validator, saves immediately through the existing collision-safe template pipeline, records History, and continues after individual failures.
- Rate limits rotate to another saved account for the same provider. Temporary 408 and 5xx service failures wait and retry the same lecture automatically instead of marking it failed; when every account is resting, the worker displays a countdown and resumes automatically.
- A successful provider connection test retrieves and caches the current compatible model list. Google runs use the prompt as a true system instruction, calibrate with the reference first, and default to the verified lower-demand Gemini 3.5 Flash-Lite while allowing the user to choose another reported Gemini model.
- Prompt versions can be deleted except when only one remains. “Use by default” controls only which prompt is preselected for a new run; the per-run selector remains independent.
- The screen shows provider, model, prompt, reference, thinking effort, per-lecture assignment and status, a timestamped readable log, and live totals. It sends one completion notification for the batch.
- Eighty-seven automated checks pass, including secure storage, copied-reference independence, prompt versioning and deletion, model caching, PDF discovery, successful generation, malformed-response continuation, temporary-service retry, key rotation, run gating, and every earlier module regression check.

## Google AI Studio sequential batches

The Google AI Studio page can process a folder of lecture PDFs one at a time. MCQ Maker uses its dedicated, manually authenticated Brave profile and one selected AI Studio tab, navigating that same tab to the new-chat URL before each lecture. Each lecture gets a fresh calibration and conversation; results go through the existing `validate_quiz()` and HTML exam-saving pipeline. The official Gemini API remains available as a separate provider.

Batch progress is stored atomically under `%LOCALAPPDATA%\MCQ Maker\ai_studio\batches`. The manifest stores source fingerprints, prompt/reference identities, model, thinking level, template identity, output destination, status, attempt metadata, and confirmed output metadata; it does not store prompt text, reference contents, model responses, cookies, or tokens. Completed exams are preserved and are skipped on resume while their inputs and output still exist. A changed PDF is queued as a fresh attempt; a missing output or local configuration issue is shown as `Needs attention`.

Pause takes effect between lectures and preserves the current manifest. Stop lets an active lecture finish safely, then preserves the remaining queue. Resume restores the saved settings and progress. Confirmed temporary connection failures and explicit HTTP 429/5xx responses use a maximum of three attempts with capped exponential backoff and jitter. A lecture whose submission outcome is unknown is marked `Interrupted` and is never automatically submitted again; MCQ Maker asks the user to review AI Studio before explicitly retrying it. Output collisions use the existing numbered-copy behavior, so completed exams are not overwritten.

The browser provider depends on Google AI Studio behavior and may need attention if Google changes that behavior; the official Gemini API remains a supported alternative.
