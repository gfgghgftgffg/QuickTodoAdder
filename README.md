# QuickTodoAdder

Windows local hotkey tool: select text, press a shortcut, let your local model extract todo.txt fields, then append a task that sleek can display.

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
  "include_creation_date": true,
  "include_due_time": true
}
```

Run:

```powershell
python quick_todo_adder.py --config config.json
```

## Usage

Open the same `todo.txt` file in sleek. Select text in any app, then press `ctrl+alt+space`. The tool saves your current clipboard, copies the current selection, waits until the clipboard actually changes, reads the selected text, restores your previous clipboard, asks the model for structured todo.txt fields, and appends one line to the file.

Toggle the listener with `ctrl+alt+shift+t`.

For a direct one-shot test without selecting text:

```powershell
python quick_todo_adder.py --config config.json --text "后天下午三点之前调研一下论文"
```

## todo.txt Mapping

The formatter follows the todo.txt rules from `todotxt/todo.txt`:

- One line is one task.
- Priority, when present, is `(A)` to `(Z)` at the start of the line.
- Creation date, when enabled, is `YYYY-MM-DD` directly after priority or at the start.
- Projects are written as `+Project`.
- Contexts are written as `@Context`.
- Extra metadata is written as `key:value`; keys and values are sanitized to contain no whitespace and no colon.

Example output:

```text
2026-05-11 调研论文 due:2026-05-13 due_time:1500
```

The model returns `description`, `priority`, `dueDate`, `dueTime`, `thresholdDate`, `reminderDate`, `reminderTime`, `projects`, `contexts`, `metadata`, and `checklistItems`; the program validates and converts those fields to todo.txt.
