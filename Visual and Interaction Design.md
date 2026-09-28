## COLOR SYSTEM

Use warm ivory surfaces, dark ink, and a restrained aubergine accent; reserve semantic colors for status rather than decoration. Keep navigation quieter than the working area and use spacing before separators, following the attention hierarchy of [Linear’s March 2026 refresh](https://linear.app/now/behind-the-latest-design-refresh).

**Light palette — default when Windows uses light mode:**

- Window background: `#F5F3EF`
- Main surface, editor, menus: `#FFFFFF`
- Recessed surface, sidebar, table header: `#EEEBE6`
- Decorative separators: `#DDD8D1`
- Interactive control border: `#8B847C`
- Hovered control border: `#635D57`
- Primary action: `#68445F`
- Primary action hover: `#593850`
- Primary action pressed: `#492E42`
- Text on primary action: `#FFFFFF`
- Secondary action: `#FFFFFF`, text `#292724`, border `#8B847C`
- Secondary action hover: `#F0ECE7`
- Secondary action pressed: `#E5DFD8`
- Selected item background: `#EDE3EA`
- Selected item text and indicator: `#593850`
- Keyboard focus ring: `#79506E`
- Primary text: `#292724`
- Secondary text: `#59534D`
- Tertiary text, placeholders, timestamps: `#70685F`
- Disabled background: `#E9E5DF`
- Disabled text and icons: `#827A72`
- Success text/icon: `#276343`; background: `#EDF5EE`
- Warning text/icon: `#805400`; background: `#FFF4DA`
- Error text/icon: `#AA303A`; background: `#FCEEF0`
- Informational feedback: secondary text on recessed surface
- Text selection: `#E2CEE0`, selected text `#292724`
- Links: `#68445F`, underlined
- Popup shadow: `#0000001F`
- Progress track: `#DDD8D1`; progress fill: primary action

Semantic banners use their text/icon color for the leading status symbol and a 3-unit left rule. Decorative separators never serve as the sole boundary of an input.

Require at least **4.5:1 contrast for enabled text** and **3:1 for essential control boundaries, status symbols, and focus indicators**. Windows Contrast themes override the palette with system colors.

## TYPOGRAPHY

Use **Segoe UI Variable** throughout the interface, falling back to **Segoe UI**; this follows [Windows typography guidance](https://learn.microsoft.com/en-us/windows/apps/design/signature-experiences/typography). Use **Consolas** only for raw JSON and precise error locations, with Segoe UI fallback for unsupported characters.

All sizes below are **device-independent pixels at 100% scaling**, not physical screen pixels. Each entry specifies size / line height / weight.

- Native title bar: Windows-managed typography.
- Page title: **24 / 32 / 600**
- Dialog title: **20 / 28 / 600**
- Section heading and quiz preview title: **16 / 24 / 600**
- Body text and question preview: **14 / 22 / 400**
- Field labels: **13 / 20 / 600**
- Buttons and navigation items: **14 / 20 / 600**
- Template options and menu commands: **14 / 20 / 400**
- History title: **14 / 20 / 600**
- Table headings: **12 / 18 / 600**
- Metadata, timestamps, helper text: **12 / 18 / 400**
- Status labels and report counts: **13 / 20 / 600**
- Error and warning explanations: **13 / 20 / 400**
- Raw JSON: **14 / 22 / 400**, Consolas
- Line numbers and shortcut hints: **12 / 18 / 400**, Consolas
- Tooltips: **12 / 18 / 400**
- Empty-state heading: **16 / 24 / 600**
- Empty-state instructions: **14 / 22 / 400**
- Native notifications: Windows-managed typography.

Use sentence case, normal letter spacing, and left alignment for interface text. Avoid all-capital labels, lightweight text, oversized counters, and italic helper text.

Allow Arabic content to determine its own paragraph direction; keep paths and JSON structure left-to-right. Respect Windows text scaling, expand control heights, and wrap labels rather than clipping them.

## LAYOUT AND SPACING

**Window**

- First launch: **1120 × 780**, including native window frame.
- Center within the current monitor’s usable work area; reduce either dimension when necessary to leave 24 units clear on each side.
- Preferred minimum: **800 × 600**; smaller available work areas take precedence.
- Restore the last window size and position, correcting positions that fall outside connected monitors.
- Preserve native title bar, resizing, Snap behavior, system menu, and caption buttons.
- Use opaque surfaces; no custom glass or wallpaper-dependent backgrounds.

**Structure**

- Left navigation: **176 units wide**, recessed surface.
- Navigation order: **Create exam**, **Folder scan**, **History**, **Templates**.
- Sidebar bottom: **Clipboard watcher: Off/On/Paused** control, followed by **Settings**.
- Main content: **24-unit outer padding**, with a **24-unit gap** below the page heading.
- Navigation rows: **40 units high**, 12-unit horizontal padding, 8-unit icon-to-label gap, 4-unit spacing between rows.
- Active navigation: selected background, semibold label, and a 3-unit leading indicator.
- At window widths below 960, replace the sidebar with a labeled **Navigate** menu in the header; never substitute an unexplained icon rail.

**Create exam**

- Header: page title on the left; **Import file** secondary action on the right.
- Primary area: editor on the left; **288-unit “Save details” panel** on the right; 20-unit gutter.
- Editor receives all remaining width and height, with a minimum height of 280.
- Editor toolbar: **Paste from clipboard** on the left, **Clear** on the right.
- Save details order: template selector, output folder, quiz title, question count, proposed filename.
- Validation summary sits below the editor; detailed errors expand beneath it.
- Bottom action row: 56-unit minimum height; status on the left, **Generate exam** on the right.
- Below 960 units, stack Save details below the editor and retain the action row at the bottom.
- Do not display history alongside the editor; it has its own page.

**Spacing rules**

- Base unit: **4**
- Icon-to-text gap: **8**
- Label-to-control gap: **8**
- Related controls: **12**
- Field groups: **20**
- Section separation: **24**
- Major section separation: **32**
- Input horizontal padding: **12**
- Editor padding: **16**
- Panel padding: **20**
- Dialog padding: **24**
- Button horizontal padding: **16**; primary button: **20**
- Standard control height: **36**; primary action: **40**
- Icon-only hit area: **32 × 32 minimum**

Keep headings and actions stationary while results update. Scroll long content inside its own area; at enlarged text sizes, allow the full page to scroll instead of hiding content.

## COMPONENT DESIGN

**Shared rules**

Use 1-unit control borders and 6-unit corner radii. Panels use 8-unit radii; menus use 8; table rows remain square. Only floating menus receive an app-defined shadow: **0 horizontal offset, 4 vertical offset, 16 blur, 0 spread**, using the popup-shadow token.

Keyboard focus adds a **2-unit ring with a 2-unit gap**, without shifting layout. Hover never changes size. Pressed states last only while pressed. Disabled controls use the disabled tokens, retain legible labels, and have no hover response.

Use the standard arrow cursor on controls, an I-beam in editable text, and a hand only for actual links.

**Paste box**

- White main surface, 8-unit radius, 1-unit interactive border; no shadow.
- Persistent external label: **Questions**.
- Empty placeholder: **“Paste your quiz JSON here.”**
- Helper below: **“You can also import a .json, .txt, or .md file.”**
- Hover: stronger border only.
- Active: focus ring; normal text selection and caret.
- Error: error-colored border plus the separate focus ring when focused; retain all input.
- Valid input does not turn the entire editor green.
- During saving, the editor becomes read-only with its existing colors and a visible **“Saving this version…”** label.
- Unavailable editor: disabled surface and explicit reason outside it.
- Enable soft wrapping; show line numbers only after a syntax error, with the affected line marked.
- A supported file dragged over the editor produces a selected-background overlay reading **“Drop to review this file”**; no bouncing outline.

**Run button**

- Visible label: **Generate exam**.
- Minimum **156 × 40**, 6-unit radius, primary fill, white text; no shadow.
- Hover and pressed states use the primary action variants.
- Keyboard focus uses the shared focus ring.
- Disabled until blocking errors are resolved; adjacent status explains why.
- During saving: label **Saving…**, repeated activation disabled, width unchanged.
- After saving: return to **Generate exam**; show success in the status area rather than changing the button into a green badge.
- No play icon; the verb describes the result.

**Template selector**

- Labeled **Template**, full panel width, 36-unit minimum height, 6-unit radius; secondary-action styling.
- Right-aligned 16-unit chevron; selected template name on the left.
- Hover: stronger border and secondary hover surface.
- Open/active: focus ring; selected menu row has a checkmark and selected background.
- Menu matches control width, with a maximum height of 320 and scrolling.
- Options are 36 units high; long names wrap and increase row height.
- **Manage templates…** is the final menu command, separated by an 8-unit gap and one divider.
- Disabled while the current save uses its selection.
- An invalid selected template shows **“Template needs attention”** with an error icon and **Review template** action.

**History log rows**

- Flat list/table, 64-unit minimum rows, 12-unit vertical and 16-unit horizontal padding; no radius or shadow.
- Columns: **Exam**, **Created**, **Template**, **Questions**, **Status**.
- Exam column grows; the remaining starting widths are 152, 160, 88, and 112 units.
- Show title above filename in the Exam column.
- Hover: secondary hover surface across the row.
- Selected: selected background plus a leading indicator; keyboard-focused row also receives an inset focus outline.
- Single click selects; double-click or Enter opens.
- Pressed: secondary pressed surface until release.
- Missing files remain selectable with a broken-link icon and **File missing** label; Open is disabled, while **Locate file** remains available.
- Below 1000 units, replace the table with two-line rows showing title, date, template, question count, and status in a wrapping metadata line.
- Open, Show in folder, and Remove from history appear in a toolbar for the selected item and in its context menu.

**Processing report panel**

- Main surface, decorative border, 8-unit radius, 20-unit padding; no shadow.
- Header: **Processing report**, followed by **“17 created · 2 skipped · 1 failed”**.
- While running, show the current filename, completed/total count, a 4-unit progress bar, and **Cancel**.
- Rows have 12-unit padding and an explicit status icon and word.
- Hover/pressed/selected states apply only to actionable rows and follow history-row styling.
- Expanding an error reveals its explanation and available action; it never opens a modal automatically.
- Disabled actions retain their labels with a nearby reason.
- Footer: **Open output folder**, **Copy report**, **Close**.
- Failure details remain expanded after completion; successful details start collapsed.

**Settings window**

- Separate, owned, nonmodal window: **720 × 640**, clamped to the work area; native frame and shadow.
- Use a single scrolling column with 24-unit padding and sections: **Files**, **Clipboard and startup**, **Appearance and notifications**.
- Group spacing: 32; setting-row minimum height: 56.
- Controls follow shared hover, focus, pressed, and disabled rules.
- Checkboxes are 18 × 18 within a minimum 32-unit hit area; show a checkmark when selected.
- Appearance choices use a three-option radio group: **System**, **Light**, **Dark**.
- Changes apply immediately after validation; footer contains **Done**.
- Invalid paths remain visible with an inline error; do not display “Saved” until accepted.
- Dependent disabled options retain explanatory text, such as **“Turn on notifications to enable sounds.”**
- Separate settings and familiar native window behavior follow the platform-convention emphasis in [Raycast’s 2026 redesign](https://www.raycast.com/blog/a-technical-deep-dive-into-the-new-raycast).

**Toast notification appearance**

- Use the native Windows notification shell; Windows owns its radius, shadow, typography, hover, pressed, disabled, dismissal, and entrance behavior.
- Supply the app icon and concise text; no hero image, custom background, or colored frame.
- Success title: **Exam saved**
- Body: **“Cardiac arrhythmias · 24 questions”**
- Actions: **Open exam**, **Show in folder**
- Batch title: **Folder scan complete**
- Body: **“17 created · 2 skipped · 1 failed”**
- Action: **View report**
- Clicking the notification body opens the corresponding result in MCQ Maker.
- Omit unavailable actions rather than displaying dead buttons.
- No sound by default; no toast when equivalent feedback is already visible in the foreground. Follow [Windows notification guidance](https://learn.microsoft.com/en-us/windows/apps/design/shell/tiles-and-notifications/toast-ux-guidance).

**System tray icon**

- A monochrome exam-sheet outline with three answer circles; the middle circle is filled.
- Draw distinct 16-, 20-, 24-, and 32-pixel versions; do not shrink a detailed logo.
- Use a dark glyph on a light taskbar and a light glyph on a dark taskbar.
- Watcher active: small check badge; paused: two-bar badge; off: unbadged sheet.
- Tooltip states **“MCQ Maker — Clipboard watcher on/off/paused”**.
- No shadow, background tile, pulsation, or app-defined hover effect.
- Windows handles hover and pressed appearance; left click restores the app, right click opens its menu.
- An unavailable watcher is communicated through menu text and tooltip, not by making the tray icon disappear.

## ICONOGRAPHY

Use **Microsoft Fluent UI System Icons, Regular**, distributed under the [MIT license](https://github.com/microsoft/fluentui-system-icons). Preserve the supplied stroke weights and geometry.

- Navigation icons: **20 units**
- Inline status and control icons: **16 units**
- Empty-state icon: **32 units**, maximum one
- Use regular variants throughout; use filled variants only for compact state badges where the outline would become unreadable.
- Match icon color to its associated text; semantic icons use semantic colors.

Use icons for navigation, clipboard actions, file/folder actions, menu chevrons, status, and settings.

Deliberately omit icons from **Generate exam**, ordinary field labels, report counts, every history filename, and explanatory paragraphs. Do not use emoji, medical crosses, hearts, stethoscopes, or decorative sparkles.

Every icon-only button has an accessible name and a tooltip after **500 ms**. Tooltips supplement visible labels; they never contain essential instructions.

## ANIMATION AND MOTION

Only these app-defined animations are approved:

- **Hover feedback:** pointer enters or leaves a control; interpolate fill and border colors only; **80 ms**, `cubic-bezier(0.2, 0, 0, 1)`.
- **Progress updates:** completed-work count increases; progress fill advances to the actual reported value; **100 ms**, linear; never animate guessed progress.
- **Unknown-duration work:** after **400 ms** without completion, show a 16-unit progress ring; rotate **360° every 1000 ms**, linear, until completion.

Keyboard focus, errors, selection, report expansion, navigation, and success messages update immediately. No page slides, success morphs, pulsing buttons, animated counters, skeleton shimmer, or automatic scrolling.

Honor Windows reduced-motion preferences: disable color transitions and animated progress; show a static progress symbol with changing status text. Native window and notification animations remain under Windows control.

## STATES AND FEEDBACK

- **Idle:** neutral document icon and **“Paste questions to begin.”** Generate exam is disabled; template and destination remain visible.
- **Checking:** **“Checking questions…”** replaces the previous validation summary; show it after a 300 ms input debounce, with no premature success indicator.
- **Ready:** check-circle icon and **“Ready · 24 questions”**; Generate exam becomes available. Ready means structurally valid, not factually verified.
- **Processing:** progress symbol, explicit stage such as **“Saving exam…”**, and real file counts where available; disable only controls that would alter the active operation.
- **Success:** check-circle icon, **“Saved Cardiac_arrhythmias_mcq.html”**, and **Open exam / Show in folder** actions; retain until the next operation or explicit dismissal.
- **Error:** error-circle icon, a concrete statement, and a correction action: **“Question 7 has no correct answer. Review question 7.”** Never clear input or rely on a toast alone.
- **Warning:** triangle icon and **“2 warnings — review recommended”**; details expand on activation, and generation remains available.
- **Disabled:** disabled styling plus visible explanation, such as **“Choose an output folder to continue.”** Do not make users discover the reason by hovering.
- **Cancelled:** neutral stop icon and **“Stopped after 8 of 20 files. 8 exams were saved.”**
- **Watcher:** persistent text **On**, **Paused**, or **Off**, with a matching symbol; enabling it never hides the main window automatically.

Use icon, wording, and placement together; never color alone.

Tab follows visual reading order. Enter activates focused controls; **Ctrl+Enter** generates from the editor. **Ctrl+Shift+V** performs Process Clipboard Now within the app, **Ctrl+O** imports a file, **Ctrl+,** opens Settings, and **Ctrl+F** searches History. Show shortcuts in tooltips and menus; keyboard access remains a convenience rather than a prerequisite, consistent with [Notion’s desktop shortcuts](https://www.notion.com/help/notion-for-desktop).

Errors do not steal keyboard focus while typing. After an explicit failed Generate attempt, focus the error summary; selecting an error moves to the relevant input location.

## DARK MODE

**Supported at launch**, with **System** selected initially.

Use the same dimensions, typography, hierarchy, and states in both themes. Reader-specific theme control in [Zotero 8](https://www.zotero.org/blog/zotero-8/) reinforces keeping application appearance separate from document appearance: switching MCQ Maker’s theme does not change the selected HTML template or exported exam.

**Dark palette:**

- Window background: `#201E20`
- Main surface, editor, menus: `#292629`
- Recessed surface and sidebar: `#242124`
- Decorative separators: `#433D43`
- Interactive control border: `#817681`
- Hovered control border: `#A99AA7`
- Primary action: `#D4AECA`
- Primary action hover: `#E2C1D9`
- Primary action pressed: `#C296B6`
- Text on primary action: `#2D2029`
- Secondary action: `#302C30`, text `#F1ECEF`, border `#817681`
- Secondary action hover: `#3B353A`
- Secondary action pressed: `#463D44`
- Selected background: `#473440`
- Selected text and indicator: `#EBC7E1`
- Keyboard focus ring: `#EBC7E1`
- Primary text: `#F1ECEF`
- Secondary text: `#C5BCC2`
- Tertiary text: `#AEA3AB`
- Disabled background: `#343034`
- Disabled text and icons: `#958B93`
- Success text/icon: `#9AD4AD`; background: `#24372B`
- Warning text/icon: `#F0CA7D`; background: `#3C3220`
- Error text/icon: `#FFACB5`; background: `#402A30`
- Informational feedback: secondary text on recessed surface
- Text selection: `#64485D`, selected text `#FFFFFF`
- Links: `#EBC7E1`, underlined
- Popup shadow: `#00000066`
- Progress track: `#433D43`; progress fill: primary action

Use semantic color names for every component. Theme changes are immediate and preserve focus, scroll position, and content; no white flash, crossfade, inverted images, or changes to status meanings.

## CHARACTER WITHOUT NOISE

- **Aubergine has a job:** it marks the primary action, current navigation location, selection, and keyboard focus; it never colors entire headers or every icon.
- **The sheet motif stays small:** the application icon contains three answer circles, while empty states use one matching sheet outline; no illustrations fill the working area.
- **The first empty editor is useful:** show **“Paste your quiz JSON here.”** and the secondary action **Try an example**; that action loads a clearly labeled sample into the editor without generating a file.
- **History starts honestly:** **“Your saved exams will appear here.”** followed by **“Create an exam to keep its file location and details here.”** Provide **Create exam**.
- **Success describes the work:** **“Saved. 24 questions, ready to open.”** No congratulations, confetti, streaks, or celebratory sounds.
- **A duplicate name is explained:** **“A file with this name already exists. This copy will be saved as Cardiology_2_mcq.html.”**
- **Clear is recoverable:** after clearing, show **“Questions cleared.”** with **Undo**; keep the last cleared input available until new input replaces it or the app closes.
- **Watcher activation is explicit:** **“Clipboard watcher is on. Valid quiz text will be saved automatically.”** Include **Pause** in the same message.
- **Missing files are treated as routine:** **“This exam has moved or been deleted.”** Provide **Locate file** and **Remove from history**.
- **Batch completion names the remaining work:** **“17 exams saved. 2 files need attention.”** Open the failed rows without shifting focus away from the completion controls.
- **Long titles retain their meaning:** wrap quiz titles; shorten paths in the middle, preserving the drive and filename, with the full path available for copying.
- **Daily use needs no ceremony:** reopen Create exam with the selected template and destination restored, an empty editor, and the watcher’s actual state visible.
