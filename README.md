# QuickTodoAdder

Windows local hotkey tool: select text, press a shortcut, let your local model extract a clear task description, due date, and optional due time, then append a todo.txt task that sleek can display.

## Setup

Install dependencies:

```powershell
pip install -r requirements.txt
```

Copy the example config:

```powershell
Copy-Item config.example.json config.json
```

Configure your model backend and todo.txt path in `config.json`. This local config uses your sleek file:

```json
"todo_txt": {
  "path": "..\\EXTRA INFO\\todo.txt",
  "timezone": "Asia/Shanghai",
  "include_creation_date": true
}
```

Run:

```powershell
python quick_todo_adder.py --config config.json
```

## Usage

Open the same `todo.txt` file in sleek. Select text in any app, then press `ctrl+alt+space`. The tool saves your current clipboard, copies the current selection, waits until the clipboard actually changes, reads the selected text, restores your previous clipboard, asks the model for a task description, due date, and optional due time, and appends one line to the file.

Toggle the listener with `ctrl+alt+shift+t`.

For a direct one-shot test without selecting text:

```powershell
python quick_todo_adder.py --config config.json --text "后天下午三点之前调研一下论文"
```

## sleek Output

The formatter intentionally keeps output compact:

- Priority, when present, is `(A)` to `(Z)` at the start of the line.
- Creation date, when enabled, is `YYYY-MM-DD`.
- Due date is written as `due:YYYY-MM-DD`.
- Specific due time is written as `due_time:HHMM`.
- No `+project`, `@context`, `reminder_time`, or arbitrary metadata is written.
- If the source includes an exact deadline time, it should be written to `due_time`, not repeated in the description.

Example:

```text
(A) 2026-05-11 确认是否愿意转投拼多多算法实习生相关岗位 due:2026-05-12 due_time:2300
```

sleek supports due date notifications based on `due:YYYY-MM-DD`. `due_time:HHMM` is a compact local extension for preserving exact times without repeating them in the description.
