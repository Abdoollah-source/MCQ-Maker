# Build deviations

## Module 1 — App shell

- Install the official `PySide6-Essentials` distribution instead of the `PySide6` meta-package. It provides the required Qt Widgets, SVG, clipboard, and tray APIs under the same `PySide6` imports while omitting unused modules such as Qt WebEngine. This reduces development downloads and the application's dependency footprint.

- The latest user-defined module order replaces the original build order. Only navigation and window interactions work in Module 1; all business controls are visibly unavailable. Settings, persistence, editor behavior, and final animations remain in their assigned modules.
- Dark-mode semantic tokens override the component prose that calls the editor and button text white; literal white would conflict with the specified dark palette.
- Qt Widgets uses its native font metrics and expanding layouts instead of fixed CSS-style line heights, preventing clipping of Arabic glyphs and scaled fonts.
- Keyboard focus initially uses an in-control 2-pixel border rather than a separate outside ring; Qt stylesheet controls cannot draw a reliable outside outline. Navigation preserves its leading selected indicator.
- Window client minimum height is 560 so its native frame fits the preferred 600-unit minimum; available monitor area takes precedence.
- Window position persistence is deferred to the settings module, avoiding a second temporary settings implementation.
- The newer user instructions override the documents for Module 3 auto-clear/undo and Module 8 conflict dialogs. Those changes will be implemented and verified only at their respective gates.

## Module 2 — Template manager

- The HTML contract now defines exact metadata declarations: `mcq-maker-template-version` = `1`, `mcq-maker-min-options`, and `mcq-maker-max-options`. These make the previously unspecified version and capability checks deterministic. Import errors show the required declarations.
- Default template selection is stored in the template library's versioned `catalog.json`. It remains the single source of truth when the Settings interface is added, rather than duplicating the value in two writable files.
- Removing a template moves its owned directory into `templates/.removed/` and removes it from the active library. This reversible archive protects accidental removals; original source files and generated exams are never deleted.
- Replacement keeps previous HTML and metadata in the template's `revisions/` folder and uses an update journal to recover an interrupted write.
- Import-time sample rendering validates data replacement without executing untrusted JavaScript. Browser preview is an explicit user action; static validation cannot certify arbitrary template JavaScript or detect every dynamically constructed network request.
- Linked resources, including CSS `url()` and `@import`, no longer block a template import. The manager records each potential break with a source line number, leaves the copied template usable, and provides Ignore and Recheck actions. Self-contained data URLs and fragment references do not raise a warning.
- A Refresh action and source-change indicator are provided; file work runs in one background queue so the window stays responsive.
- Imported templates are normalized to UTF-8 with LF line endings after decoding. This gives stored copies stable hashes across Windows and other platforms while preserving all document content.
- Link-warning details, actions, and stored-copy information are now contained in a collapsed row beneath the affected template. A red warning triangle provides the persistent at-a-glance signal; expanding only that row reveals the full line-numbered information.

## Module 3 — Paste mode

- The user explicitly requested automatic clearing after a successful manual generation, replacing the original decision document’s preserve-input default. The questions box retains its normal Qt undo command, so Ctrl+Z restores the cleared quiz immediately.
- Until Module 4 provides persistent settings, manual output uses the specified default `Documents\\MCQ Maker Exams` folder without exposing a temporary folder picker.
- The fixed output-folder panel includes an **Open output folder** action now, so each successful manual save has an immediate, discoverable destination. Folder selection remains part of the Settings module.

## Module 4 — Settings

- Preferences whose supporting service arrives later in the requested build order — clipboard watching, tray behavior, Windows startup, and notifications — are stored and shown now, but do not activate until Modules 7–9 supply those services. This avoids presenting a second, temporary settings format or changing system behavior before its feature is verified.
- The first settings preview used a modal two-column form with Save and Cancel buttons. User testing exposed horizontal drift, clipped labels, cramped drop-down rows, and unclear theme feedback. It was replaced with the specified 720 × 640 owned, nonmodal, single-column window; settings now validate, save, and visibly apply as soon as a control changes, with a single Done action.
- The shared focus treatment was completed during Module 4 after user review instead of waiting for Module 10. Active inputs and selectors now combine the specified focus border with a quiet palette-matched surface tint, and open selector menus receive the same border. The surrounding interface stays fully legible rather than dimming whenever focus moves.
- Qt's native selector popup aligns itself around the selected row, which made two-item menus alternate between opening upward and downward. MCQ Maker's shared selector now places the menu below whenever the screen has room and above only near the screen edge. Labels, buttons, panels, and selectors were also moved into one shared component module so later screens inherit the verified behavior without copied implementations.

## Module 5 — Folder scan

- Unsupported files are excluded from the processing queue rather than counted as skipped. The page clearly states the accepted `.json`, `.txt`, and `.md` extensions; Skipped is reserved for supported files left unprocessed by cancellation, which keeps the completion totals meaningful.
- Folder scans use the configured default template rather than adding another template selector to the page. This preserves one authoritative batch choice and makes the active template visible in the selected-folder panel.
- History recording and batch-complete notifications remain deferred to Modules 6 and 9. Their services do not exist yet; the scan exposes a stable per-file result model that those modules can consume without changing the processing pipeline.

## Module 6 — History

- History begins with generations made after Module 6 is installed. Earlier HTML files are not backfilled because their original source mode, template revision, validated-input hash, and question count cannot be reconstructed reliably from arbitrary output files.
- Failed manual saves and failed folder inputs appear alongside successful generations with an explicit Failed status, as required by the final decision to retain generation attempts. Skipped files are omitted because no generation was attempted.
- CSV export follows the current search results. This makes an active search a useful filter while an empty search exports the complete history.

## Module 7 — System tray

- The tray menu exposes **Pause Clipboard Watcher** in its final position but keeps it disabled during Module 7 because the watcher service is not built until Module 8. The remaining clipboard-specific commands from the decision document will arrive with that service rather than presenting actions that cannot yet work.
- The first close-to-tray explanation is stored as an internal Boolean in the existing versioned settings file. It is not shown as a user preference because its only purpose is to prevent the explanatory message from repeating.
- Quit cancels a running folder scan and waits asynchronously for both scan and template workers to become quiet before exiting. This satisfies the safe-shutdown decision without freezing the interface while background work winds down.

## Module 8 — Clipboard watcher

- The latest user feedback establishes that the saved existing-filename policy applies to clipboard saves as well as manual saves. **Always save a numbered copy** proceeds without interruption; **Ask before replacing** restores the window and offers **Replace**, **Save numbered copy**, and **Skip**, with the numbered copy as the safe default.
- Paused is a session state rather than a saved preference. The explicit enabled/disabled choice remains persistent, so a paused watcher starts enabled after the next launch instead of appearing active while silently retaining an old pause.
- Module 8 emits complete notification requests for clipboard saves and failures, but Windows toast delivery remains at the Module 9 gate where the chosen notification library and suppression rules are implemented. In-app status and History remain available meanwhile.
- The watcher does not process clipboard text that was already present when the app starts or when watching is enabled. It reacts to subsequent clipboard changes, preventing an old quiz from being saved unexpectedly; **Process Clipboard Now** remains available for deliberate handling of existing content.
- Content hashes identify History entries and queued work, but they do not suppress a later copy of identical text. The 450 ms debounce collapses repeated Qt signals from one Windows copy operation, while every new clipboard change after that window is treated as an intentional request and can create another numbered exam.

## Module 9 — Notifications

- The current build uses `WindowsToaster('MCQ Maker')` with a whole-notification activation action. Separate **Open exam**, **Show in folder**, and **View report** buttons were omitted because dependable action routing also requires an installed activation handler, not only a shortcut identity. Clicking the notification itself restores the app at the matching History entry or report.
- Repeated identical background errors are throttled for 30 seconds. The next eligible toast reports how many further occurrences were grouped, preventing a failing folder or watcher from flooding Windows Notification Center.
- Foreground suppression uses the actual active-window state rather than mere window visibility. A visible MCQ Maker window behind another program can still notify the user, while the active app relies on its existing in-app status and History feedback.
- The user explicitly requested a completion notification for every finished folder scan even when its report is already visible. Batch completion therefore bypasses foreground suppression, while no per-file notification is emitted.

## Module 10 — Polish and packaging

- The packaged release pins PySide6 Essentials 6.10.2 instead of 6.11.2. It is the mature Qt 6 line for the release toolchain and preserves the same APIs and interface behavior.
- The PyInstaller specification explicitly excludes an unrelated Poppler `icuuc.dll` and `icudt78.dll` found on the development host's tool PATH. Qt uses Windows' built-in ICU; bundling Poppler's same-named DLL caused a hidden startup failure before application code could run. A minimal frozen Qt check verified the exclusion before the full app was rebuilt.
- Release freezing uses the existing official Python 3.12 installation in an isolated `.venv-build` environment. Python remains bundled for the user; this choice avoids depending on a developer-only managed runtime and changes no application behavior.
- Hover colors change immediately instead of interpolating for 80 ms. Qt Widgets stylesheets do not animate palette properties; replacing the shared native controls with custom-painted controls solely for this effect would add inconsistent input behavior. Folder progress still animates actual completed work for 100 ms and respects Windows reduced-motion settings.
- The source-tree shortcut remains available for development, but the user-facing release is now the specified PyInstaller onedir application inside a per-user Inno Setup installer. This also gives Windows a stable executable and shortcut identity.
- The startup setting uses the current user's standard Windows `Run` entry instead of a Startup-folder shortcut. It requires no administrator access, is removed when the setting is turned off or the app is uninstalled, and points directly to the bundled executable after installation.
- Backup export and restore are deferred from the research document's missed-considerations list. They were not part of the ten user-approved modules, and adding a late restore path without its own recovery and migration review creates more risk to templates and History than it removes. Existing atomic settings backups, template revisions, reversible template removal, and the SQLite database remain independently copyable under `%LOCALAPPDATA%\MCQ Maker`.
- This local 0.1.0 installer is unsigned. Code signing is required before any public distribution, but signing needs a publisher certificate and was not necessary for the user's local build.

## Module 11 — AI generation

- Provider calls use each provider's official HTTPS API directly through Python's standard library. This keeps the installed application independent of four large, fast-changing SDK dependency trees while preserving the documented request formats and clean provider adapter boundary. Google models are discovered from the configured account, and new runs default to `gemini-3.5-flash-lite` because it completed the full calibration and PDF generation workflow reliably during verification; every other compatible discovered model remains selectable.
- API keys use current-user Windows DPAPI encryption first. Managed Windows profiles that do not expose a user DPAPI master key fall back to machine-scope DPAPI combined with the current user's LocalAppData file permissions; the key is never stored as plain text.
- The Run button requires a reference for every lecture instead of merely one lecture. Starting a batch with silent, unassigned files would leave ambiguous pending work, so MCQ Maker requires a complete plan and still supports a different reference on every row.
- Generated question text and raw provider replies are not copied into the live log. The log records the response arrival, validation result, filenames, counts, concise calibration confirmation, waits, and failures so it remains useful without duplicating medical content or potentially sensitive lecture output on screen.
- A provider error or invalid response starts a fresh calibrated conversation before the next lecture. This costs one additional calibration call after a failure but prevents a malformed or partially processed lecture from affecting later work.
- Lecture and reference files use the providers' 50 MB direct-upload path. Larger files fail individually with a clear reason rather than introducing temporary remote-file lifecycle state in this first AI module.
- Google 408 and 5xx responses are treated as temporary availability problems and retried with a visible countdown. The first user run showed that a successful key test does not guarantee immediate inference capacity: Gemini 3.8, 3.7, and 3.5 Flash accepted calibration but returned 503 under load, while the same complete synthetic flow succeeded on Gemini 3.5 Flash-Lite.
- The Google model list is discovered from the configured account after a connection test and cached locally. Endpoints clearly meant for images, audio, live sessions, embeddings, transcription, robotics, computer use, or custom tools are excluded because they cannot perform this PDF-to-text workflow; known retired entries are also excluded even when Google's listing endpoint still returns them.
- “Active prompt” is presented as “Use by default” and explicitly described as the initial per-run selection. Prompt versions can now be deleted, with the last remaining prompt protected so every generation run always has instructions.
