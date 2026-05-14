# QuickTodoAdder

Windows local hotkey tool for maintaining a `todo.txt` file used by [sleek](https://github.com/ransome1/sleek). Select any message, email, webpage text, or meeting note, press a shortcut, and turn it into a sleek todo automatically. Your local model extracts a clear task description, due date, and optional due time, then appends a sleek-compatible todo.txt task.

sleek is an open-source todo manager based on the `todo.txt` syntax. QuickTodoAdder does not replace sleek's UI; it writes tasks into the same `todo.txt` file, and sleek provides the visual task management experience.

## Setup

Install dependencies:

```powershell
pip install -r requirements.txt
```

Copy the example config:

```powershell
Copy-Item config.example.json config.json
```

Configure your model backend and todo.txt path in `config.json`. The `todo_txt.path` value should point to the same `todo.txt` file opened by sleek:

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

The program stays in the Windows notification area. Use the tray menu to pause listening, open `todo.txt`, open the config, open the project folder, or exit.

## Usage

Open the same `todo.txt` file in sleek. Select content from WeChat, email, a browser page, a document, or any other app, then press `ctrl+alt+space`. The tool auto asks the model for a task description, due date, and optional due time, and appends one sleek-compatible todo.txt line to the file.

Toggle the listener with `ctrl+alt+shift+t`.

For a direct one-shot test without selecting text:

```powershell
python quick_todo_adder.py --config config.json --text "Date with Alice by 3:00 PM the day after tomorrow."
```

## sleek Output

The model response is parsed and then formatted as a compact todo.txt line that matches the syntax used by sleek:

- Priority, when present, is `(A)` to `(Z)` at the start of the line.
- Creation date, when enabled, is `YYYY-MM-DD`.
- Due date is written as `due:YYYY-MM-DD`.
- Specific due time is written as `due_time:HHMM`.
- No `+project`, `@context`, `reminder_time`, or arbitrary metadata is written.
- If the source includes an exact deadline time, it should be written to `due_time`, not repeated in the description.

Example:

```text
(A) 2026-05-11 Buy some fruits. due:2026-05-12 due_time:2300
```

sleek supports due date notifications based on `due:YYYY-MM-DD`. `due_time:HHMM` is a compact local extension for preserving exact times without repeating them in the description.
