# MCQ MAKER — FINAL BUILD DECISIONS

## APP IDENTITY

**MCQ Maker**, built with **Python and PySide6 Qt Widgets**, because its integrated desktop controls, clipboard events, drag-and-drop, and tray support provide a maintainable Windows application with fewer integration points.

## INJECTION METHOD

Use exact JavaScript comment markers inside one inline script:

```javascript
/* MCQ_MAKER_DATA_START */
const quizData = [];
/* MCQ_MAKER_DATA_END */
```

Require exactly one occurrence of each marker, in the correct order, within the same script element. The enclosed region must contain only the `const quizData` declaration with a JSON array and optional whitespace.

Replace the enclosed region using string offsets, preserving both markers and the rest of the template. Reject incompatible templates without guessing or automatic marker insertion.

Serialize validated data with `json.dumps(ensure_ascii=False, allow_nan=False)`. Escape `<`, `>`, `&`, U+2028, and U+2029 as Unicode escapes; also escape `/` to prevent quiz text from reproducing the marker strings.

## TEMPLATE STORAGE

Store application data under **`%LOCALAPPDATA%\MCQ Maker\`**, resolved using `platformdirs.user_data_path("MCQ Maker", appauthor=False, roaming=False)`. [Path API](https://platformdirs.readthedocs.io/en/latest/api.html)

Store each template at:

```text
%LOCALAPPDATA%\MCQ Maker\templates\<uuid>\template.html
%LOCALAPPDATA%\MCQ Maker\templates\<uuid>\metadata.json
```

Import an independent copy. Read UTF-8 with optional BOM; reject undecodable files. Require an HTML document, UTF-8 charset declaration, supported template version, and the exact injection contract.

Validate and sample-render before completing import. Reject invalid imports with specific correction instructions; preserve the original file and any previously valid stored version.

Generated exams belong in the selected output folder, outside application storage.

## WORKFLOW MODES

- **Paste:** Debounced validation shows the title, question count, template, and proposed filename before Generate; preserve the input until explicitly cleared.
- **Process Clipboard Now:** Read clipboard text once into the same preview and validation flow without enabling continuous monitoring.
- **File Import / Drag-and-Drop:** Accept `.json`, `.txt`, and `.md` files through a picker or Qt file drops; preview single files and route multiple files or folders into batch processing.
- **Folder Scan:** Process supported files in deterministic order through a cancellable worker queue, with recursion off by default and an individual result for every file.
- **Clipboard Watcher:** Explicitly enabled monitoring uses [`QClipboard.dataChanged()`](https://doc.qt.io/qtforpython-6/PySide6/QtGui/QClipboard.html), brief debouncing, and content hashes to queue valid quizzes for automatic saving without modal dialogs.

All modes share one extraction, validation, rendering, and saving pipeline. Continuous folder watching is deferred.

## JSON VALIDATION

Apply these checks in order:

1. Enforce a **5 MiB input limit**; decode files as UTF-8 with optional BOM.
2. Accept a complete JSON document or extract an outer array from Markdown fences or surrounding prose using `JSONDecoder.raw_decode()`; reject multiple quiz candidates as ambiguous.
3. Reject malformed JSON, duplicate object keys, `NaN`, and infinities; never silently repair punctuation, quotes, or answers.
4. Validate structure with [`jsonschema.Draft202012Validator`](https://python-jsonschema.readthedocs.io/en/stable/): a root array containing one title object followed by at least one question.
5. Require a nonblank string `title`; each question requires nonblank `question`, **2–10 nonblank string options**, `correct`, and a string `explanation`.
6. Explicitly require `type(correct) is int` and `0 <= correct < len(options)`; reject booleans, numeric strings, and floating-point indices.
7. Verify the selected template supports the option count; warn about duplicate questions, duplicate options, empty explanations, and ignored extra fields.

Translate errors into question numbers and field names. Show syntax line/column locations and highlight the input where possible. List all structural errors together; errors disable generation, while warnings remain visible and nonblocking.

Unrelated clipboard content is ignored. Recognizable but invalid quiz input appears in the activity panel without retaining its raw contents.

## FILE NAMING

Apply these rules in order:

1. Trim the title and normalize Unicode to NFC.
2. Preserve case, Arabic, accented letters, and other valid Unicode.
3. Replace whitespace and Windows-forbidden characters `< > : " / \ | ? *` with underscores.
4. Remove control characters and invisible formatting characters from the filename only.
5. Collapse repeated underscores and trim surrounding underscores, spaces, and periods.
6. Protect Windows device names, including extension and superscript variants, by prefixing `exam_`.
7. If empty, use `untitled_YYYYMMDD_HHMMSS`.
8. Limit the title stem to **60 Unicode characters**, shortening further so the complete absolute path stays within **240 UTF-16 code units**.
9. Append **`_mcq.html`**.

Default conflicts create `Title_2_mcq.html`, `Title_3_mcq.html`, and so on, accounting for the counter in the length budget and treating names case-insensitively.

Manual generation may offer **Replace**, **Save Copy**, or **Cancel**. Batch and watcher modes always create numbered copies without prompting. Final file creation must detect a competing file rather than overwrite it accidentally.

## TEMPLATE MANAGER

Provide **Import, Rename, Set Default, Preview, Re-import, Remove, and Reveal in Explorer**.

Ship a working, self-contained default template and copy it into application storage on first launch. Its renderer supports 2–10 options, plain-text content, Arabic direction detection, and offline operation.

Use UUID directories; display names remain metadata and never determine physical filenames. Store:

- Template ID, display name, and metadata version.
- Original filename and absolute source path.
- Import/update timestamps and source modification time.
- SHA-256 content hash, template format version, and supported option range.

Check encoding, marker integrity, format compatibility, and asset dependencies on import and re-import. Revalidate before generation. Reject dependencies on external files or network resources for the supported self-contained format.

Show source changes as a nonblocking indicator when the manager opens. Re-import is explicit and preserves the previous valid revision. Removing a template never deletes generated exams.

## HISTORY LOG

Use standard-library **`sqlite3`** at:

```text
%LOCALAPPDATA%\MCQ Maker\history.sqlite3
```

Use a versioned schema, transactions, and serialized writes.

Record each generation attempt’s ID, UTC timestamp, title, output filename and absolute path, template ID/name/version/hash, question count, source mode, optional source path, validated-input hash, outcome, and concise error summary. Record success only after the output file commits.

Provide search, filtering, double-click or explicit **Open in Browser**, **Show in Folder**, CSV export, and removal of history entries. Display timestamps in local time. Missing files offer **Locate File** or **Remove Entry**.

Store metadata only; history does not retain question payloads or restore deleted exams.

## SYSTEM TRAY

Use **PySide6 `QSystemTrayIcon`**.

Window close defaults to **Quit**. Users may explicitly enable **Keep running in tray**, with a one-time explanation on first use. If the tray is unavailable, keep the window accessible.

Right-click menu:

- Open MCQ Maker
- Enable / Pause / Resume Clipboard Watcher
- Process Clipboard Now
- Open Output Folder
- Settings
- Quit

Quit stops intake and completes or safely cancels pending work before exiting.

## NOTIFICATIONS

Use **[`windows-toasts`](https://windows-toasts.readthedocs.io/en/latest/)** behind a small notification service, with an installer-configured application identity.

Trigger notifications for:

- A quiz successfully saved by the clipboard watcher.
- A completed batch, including created, skipped, and failed counts.
- A background generation or saving failure.
- An unexpected watcher shutdown requiring attention.

Use in-app banners when foregrounded and toasts when backgrounded. Aggregate repeated errors. Keep details in the activity panel because notifications may be suppressed.

Notification failure must never change a successful save into a failed generation.

## SETTINGS

Store versioned JSON at:

```text
%LOCALAPPDATA%\MCQ Maker\settings.json
```

Validate types and values on load. Save atomically, retain a last-known-good backup, and preserve corrupted files before recovering.

Complete configurable options:

- **Output folder:** defaults to the Windows Documents known folder plus `MCQ Maker Exams`.
- **Default template:** stored by template ID.
- **Open after manual generation:** off by default; background and batch generation never launch individual exams automatically.
- **Manual filename conflicts:** Ask or Save Copy; default Save Copy.
- **Include subfolders in folder scans:** off by default.
- **Clipboard Watcher:** enabled/disabled, off initially; remember explicit user selection.
- **Window close behavior:** Quit or Keep Running in Tray; default Quit.
- **Start with Windows:** off by default.
- **Notifications:** on by default.
- **Notification sounds:** off by default.
- **Theme:** System, Light, or Dark; default System.

History opening remains double-click plus an explicit Open action.

## DEPENDENCY MANAGEMENT

Distribute a **bundled Windows executable**, using **PyInstaller `--onedir` inside a per-user Inno Setup installer**.

Install binaries under `%LOCALAPPDATA%\Programs\MCQ Maker\`. Include Python, PySide6, `jsonschema`, `platformdirs`, `windows-toasts`, required transitive dependencies, icons, and the default template.

Maintain a developer virtual environment, `pyproject.toml`, and a tested dependency lock with exact versions. Dependency installation occurs during development and release builds only.

The installed app requires no Python installation, terminal commands, administrator privileges, or runtime package downloads.

## MISSED CONSIDERATIONS

- Use atomic output commits, bounded retries for temporary file locks, and clear recovery for storage failures.
- Enforce a single running instance; subsequent launches focus the existing window.
- Read Qt clipboard data on the GUI thread; perform parsing and file work in workers and return UI updates through signals.
- Treat imported HTML as executable content; structural validation does not certify template safety.
- Render question content with `textContent`, preserve line breaks, and use `dir="auto"` for Arabic and mixed-language text.
- Preserve medical symbols and comparison operators; rich HTML, formula rendering, and image questions are deferred.
- Keep clipboard processing local and exclude raw exams and clipboard text from diagnostic logs.
- Provide rotating logs and settings/template backup export and import with validated archive paths.
- Test high-DPI displays, keyboard navigation, Arabic filenames, redirected Documents folders, and offline browser operation.
- Preserve user data across upgrades and uninstall by default; include dependency license notices and sign public releases.
- Structural validation does not establish factual or medical correctness; educators review generated exams.

## BUILD ORDER

1. Freeze the quiz schema, template contract, and valid/adversarial test fixtures.
2. Build and test extraction, strict validation, naming, safe injection, and atomic saving.
3. Build the offline default template and verify Unicode, option counts, and browser rendering.
4. Implement application storage, template revisions, settings recovery, and SQLite history.
5. Build the PySide6 paste workflow, preview, template manager, settings, and history interface.
6. Produce an early PyInstaller/Inno Setup build and verify installation on clean Windows 11.
7. Add file import, drag-and-drop, cancellable folder scans, and actionable batch summaries.
8. Add Process Clipboard Now, single-instance enforcement, and the opt-in clipboard watcher.
9. Add tray behavior, notifications, startup integration, logs, and backup export/import.
10. Complete packaged-app reliability, accessibility, offline, upgrade, and beginner usability testing.
