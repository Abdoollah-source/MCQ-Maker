# Human-assisted AI Studio feasibility runner

This experiment prepares one calibration turn and one lecture turn in a single
Google AI Studio conversation. Playwright configures and observes the page, but
the user clicks **Run** for both turns.

The saved MCQ Maker prompt is inserted in the normal prompt area beside the
reference attachment. The experiment explicitly clears AI Studio's separate
System Instructions field first.

On a live failure it saves only privacy-safe structural diagnostics in
`human_assisted_artifacts/`: status codes, generic gRPC error code/message,
request field layout, WAA proof presence/length, and automation state. Prompt
text, file identifiers, cookies, tokens, and model response content are omitted.

It reuses MCQ Maker's dedicated Brave profile, production browser lifecycle,
central selectors, quiz validator, and HTML generator. It does not modify normal
application History. Output is isolated in `human_assisted_output/`.

From the `MCQ Maker` project directory:

```powershell
.venv\Scripts\python.exe experiments\ai_studio_playwright\human_assisted.py
```

Defaults use the established test materials:

- `Pompts/PROMPT(MCQ-MAKER).txt` as System Instructions.
- `References/refrence.txt` as the calibration reference.
- `References/1- Lecture-1.pdf` as the lecture.

Press `Ctrl+C` to cancel. On failure, Brave remains open for inspection until the
runner is stopped. The runner closes only the dedicated Brave process it started.
It never clicks Run, automates Google credentials, or copies cookies.
