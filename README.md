# QuickTodoAdder

Windows local hotkey tool: select text, press a shortcut, let your local model extract task fields, then create a Microsoft To Do task through Microsoft Graph.

## Setup

1. Install dependencies:

```powershell
pip install -r requirements.txt
```

2. Copy the example config:

```powershell
Copy-Item config.example.json config.json
```

3. Register a Microsoft Entra app for a public/native client and put its client ID into `microsoft.client_id`.

Required delegated permission: `Tasks.ReadWrite`.

4. Configure your model backend in `config.json`.

The shape follows your Dota2 translator project:

- Ollama: `POST /api/generate` with `model`, `prompt`, `stream: false`, `think`, and `options`.
- llama.cpp: `POST /completion` by default, or set `api_format` to `chat_completions`.

5. Run:

```powershell
python quick_todo_adder.py --config config.json
```

The first Microsoft To Do write will print a device-login URL and code. Sign in once; tokens are cached in `.msal_token_cache.json`.

## Usage

Select any text in any app, then press `ctrl+alt+space`. The tool copies only the current selection, restores your previous clipboard, sends current local time plus the selected text to the model, validates the returned JSON, and creates a task.

Toggle the listener with `ctrl+alt+shift+t`.

## Microsoft To Do Fields

The tool sends these Graph fields when available:

- `title`: required task title.
- `body`: notes, plus the original selected text when `append_source_text` is true.
- `dueDateTime`, `reminderDateTime`, `startDateTime`: `{ dateTime, timeZone }`.
- `importance`: `low`, `normal`, or `high`.
- `categories`: only when the model explicitly returns labels.
- `checklistItems`: created after the task when the model returns subtasks.

By default, `task_list_id` and `task_list_name` are empty, so the default To Do list is used. Set `task_list_name` to target a named list, or set `task_list_id` directly.
