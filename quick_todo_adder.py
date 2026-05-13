# -*- coding: utf-8 -*-
"""Capture selected text, analyze it locally, and add it to a todo.txt file."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests


DATETIME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")
CHINESE_WEEKDAYS = {
    "Monday": "星期一",
    "Tuesday": "星期二",
    "Wednesday": "星期三",
    "Thursday": "星期四",
    "Friday": "星期五",
    "Saturday": "星期六",
    "Sunday": "星期日",
}


class Config:
    DEFAULTS: dict[str, Any] = {
        "hotkeys": {
            "trigger": "ctrl+alt+space",
            "toggle": "ctrl+alt+shift+t",
        },
        "capture": {
            "copy_delay": 0.15,
            "copy_timeout": 1.0,
            "restore_clipboard": True,
        },
        "backend": {
            "provider": "llamacpp",
        },
        "ollama": {
            "host": "127.0.0.1",
            "port": 11434,
            "endpoint": "/api/generate",
            "model": "gemma4:e4b",
            "timeout": 60,
            "think": False,
            "options": {"temperature": 0.1},
        },
        "llamacpp": {
            "host": "127.0.0.1",
            "port": 8080,
            "endpoint": "/completion",
            "api_format": "completion",
            "model": "",
            "timeout": 60,
            "think": False,
            "headers": {},
            "options": {"temperature": 0.1, "n_predict": 512},
        },
        "prompt": {
            "file": "prompts/todo_extractor_prompt.txt",
            "user_text_placeholder": "{{text}}",
            "current_time_placeholder": "{{current_time}}",
            "timezone_placeholder": "{{timezone}}",
        },
        "todo_txt": {
            "path": r"..\EXTRA INFO\todo.txt",
            "timezone": "Asia/Shanghai",
            "include_creation_date": True,
        },
        "task_defaults": {
            "priority": "",
            "max_description_length": 180,
        },
    }

    def __init__(self, path: str) -> None:
        self.path = Path(path).resolve()
        self.base_dir = self.path.parent
        self.data = json.loads(json.dumps(self.DEFAULTS))
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.save()
            return
        with self.path.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        if not isinstance(loaded, dict):
            raise ValueError(f"Config must be a JSON object: {self.path}")
        self._deep_merge(self.data, loaded)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as handle:
            json.dump(self.data, handle, ensure_ascii=False, indent=2)

    def get(self, *keys: str, default: Any = None) -> Any:
        value: Any = self.data
        for key in keys:
            if not isinstance(value, dict) or key not in value:
                return default
            value = value[key]
        return value

    def resolve_path(self, value: str) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path
        return (self.base_dir / path).resolve()

    def _deep_merge(self, target: dict[str, Any], source: dict[str, Any]) -> None:
        for key, value in source.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                self._deep_merge(target[key], value)
            else:
                target[key] = value


class PromptTemplate:
    def __init__(self, file_path: Path, text_placeholder: str, time_placeholder: str, timezone_placeholder: str) -> None:
        self.file_path = file_path
        self.text_placeholder = text_placeholder
        self.time_placeholder = time_placeholder
        self.timezone_placeholder = timezone_placeholder
        self.template = self.file_path.read_text(encoding="utf-8").strip()

    def render(self, text: str, current_time: str, timezone_name: str) -> str:
        prompt = self.template.replace(self.text_placeholder, text)
        prompt = prompt.replace(self.time_placeholder, current_time)
        prompt = prompt.replace(self.timezone_placeholder, timezone_name)
        return prompt


class LocalModelClient:
    def __init__(self, config: Config, prompt_template: PromptTemplate) -> None:
        self.config = config
        self.prompt_template = prompt_template
        self.session = requests.Session()
        self.provider = str(config.get("backend", "provider", default="llamacpp")).lower()
        self.section = "llamacpp" if self.provider == "llamacpp" else "ollama"
        self.url = self._build_url(
            str(config.get(self.section, "host")),
            int(config.get(self.section, "port")),
            str(config.get(self.section, "endpoint")),
        )

    def analyze(self, selected_text: str, current_time: str, timezone_name: str) -> str:
        prompt = self.prompt_template.render(selected_text, current_time, timezone_name)
        if self.provider == "llamacpp":
            return self._call_llamacpp(prompt)
        return self._call_ollama(prompt)

    def _call_ollama(self, prompt: str) -> str:
        payload: dict[str, Any] = {
            "model": self.config.get("ollama", "model", default=""),
            "prompt": prompt,
            "stream": False,
            "think": bool(self.config.get("ollama", "think", default=False)),
        }
        options = self.config.get("ollama", "options", default={}) or {}
        if options:
            payload["options"] = options
        response = self.session.post(
            self.url,
            json=payload,
            timeout=float(self.config.get("ollama", "timeout", default=60)),
        )
        response.raise_for_status()
        return self._extract_text(response.json())

    def _call_llamacpp(self, prompt: str) -> str:
        api_format = str(self.config.get("llamacpp", "api_format", default="completion")).lower()
        if api_format == "chat_completions":
            payload: dict[str, Any] = {
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "think": bool(self.config.get("llamacpp", "think", default=False)),
            }
        else:
            payload = {
                "prompt": prompt,
                "stream": False,
                "think": bool(self.config.get("llamacpp", "think", default=False)),
            }

        model = str(self.config.get("llamacpp", "model", default=""))
        if model:
            payload["model"] = model
            if not payload["think"] and "qwen3.6" in model.lower():
                payload["chat_template_kwargs"] = {"enable_thinking": False}

        payload.update(self.config.get("llamacpp", "options", default={}) or {})
        response = self.session.post(
            self.url,
            json=payload,
            headers=self.config.get("llamacpp", "headers", default={}) or {},
            timeout=float(self.config.get("llamacpp", "timeout", default=60)),
        )
        response.raise_for_status()
        return self._extract_text(response.json())

    def _extract_text(self, data: dict[str, Any]) -> str:
        for key in ("response", "content", "completion"):
            value = data.get(key)
            if isinstance(value, str):
                return value.strip()
        message = data.get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return message["content"].strip()
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                if isinstance(first.get("text"), str):
                    return first["text"].strip()
                choice_message = first.get("message")
                if isinstance(choice_message, dict) and isinstance(choice_message.get("content"), str):
                    return choice_message["content"].strip()
        raise ValueError("Unsupported model response format.")

    def _build_url(self, host: str, port: int, endpoint: str) -> str:
        endpoint = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        return f"http://{host}:{port}{endpoint}"


@dataclass
class ParsedTask:
    description: str
    priority: str | None
    due_date: str | None
    due_time: str | None
    checklist_items: list[str]


class TaskParser:
    def __init__(self, config: Config) -> None:
        self.default_priority = self._clean_priority(config.get("task_defaults", "priority", default=""))
        self.max_description_length = int(config.get("task_defaults", "max_description_length", default=180))

    def parse(self, raw_text: str) -> ParsedTask:
        data = self._load_json(raw_text)
        description = self._clean_string(data.get("description", data.get("title")))
        if not description:
            raise ValueError("Model returned an empty description.")
        if len(description) > self.max_description_length:
            description = description[: self.max_description_length].rstrip()

        return ParsedTask(
            description=description,
            priority=self._clean_priority(data.get("priority")) or self.default_priority,
            due_date=self._clean_date(data.get("dueDate")),
            due_time=self._clean_time(data.get("dueTime")),
            checklist_items=self._clean_string_list(data.get("checklistItems")),
        )

    def _load_json(self, raw_text: str) -> dict[str, Any]:
        text = raw_text.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, flags=re.DOTALL)
            if not match:
                raise
            loaded = json.loads(match.group(0))
        if not isinstance(loaded, dict):
            raise ValueError("Model response must be a JSON object.")
        return loaded

    def _clean_string(self, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    def _clean_string_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        cleaned: list[str] = []
        for item in value:
            text = self._clean_string(item)
            if text:
                cleaned.append(text[:120])
        return cleaned

    def _clean_priority(self, value: Any) -> str | None:
        text = self._clean_string(value).upper()
        if not text or text == "NULL":
            return None
        if re.fullmatch(r"[A-Z]", text):
            return text
        if re.fullmatch(r"\([A-Z]\)", text):
            return text[1]
        return None

    def _clean_date(self, value: Any) -> str | None:
        text = self._clean_string(value)
        if not text or text.lower() == "null":
            return None
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return text
        if DATETIME_PATTERN.fullmatch(text):
            return text.split("T", 1)[0]
        raise ValueError(f"Invalid date from model: {text}")

    def _clean_time(self, value: Any) -> str | None:
        text = self._clean_string(value)
        if not text or text.lower() == "null":
            return None
        if re.fullmatch(r"\d{2}:\d{2}", text):
            return text.replace(":", "")
        if re.fullmatch(r"\d{4}", text):
            return text
        if DATETIME_PATTERN.fullmatch(text):
            return text.split("T", 1)[1][:5].replace(":", "")
        raise ValueError(f"Invalid time from model: {text}")

class TodoTxtClient:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.path = config.resolve_path(str(config.get("todo_txt", "path", default="todo.txt")))
        self.include_creation_date = bool(config.get("todo_txt", "include_creation_date", default=True))

    def create_task(self, task: ParsedTask, timezone: ZoneInfo) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = [self._format_line(task, timezone)]
        for item in task.checklist_items:
            subtask = ParsedTask(
                description=f"{task.description} - {item}",
                priority=task.priority,
                due_date=task.due_date,
                due_time=task.due_time,
                checklist_items=[],
            )
            lines.append(self._format_line(subtask, timezone))
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            for line in lines:
                handle.write(f"{line}\n")
        return {"title": task.description, "path": str(self.path), "lines": lines}

    def _format_line(self, task: ParsedTask, timezone: ZoneInfo) -> str:
        parts: list[str] = []
        if task.priority:
            parts.append(f"({task.priority})")
        if self.include_creation_date:
            parts.append(datetime.now(timezone).strftime("%Y-%m-%d"))
        parts.append(self._single_line(task.description))
        if task.due_date:
            parts.append(f"due:{task.due_date}")
            if task.due_time:
                parts.append(f"due_time:{task.due_time}")
        return " ".join(part for part in parts if part)

    def _single_line(self, value: str) -> str:
        return re.sub(r"\s+", " ", value).strip()


class QuickTodoAdderApp:
    def __init__(self, config_path: str) -> None:
        self.config = Config(config_path)
        timezone_name = str(self.config.get("todo_txt", "timezone", default="Asia/Shanghai"))
        self.timezone_name = timezone_name
        self.timezone = ZoneInfo(timezone_name)
        prompt_template = PromptTemplate(
            self.config.resolve_path(str(self.config.get("prompt", "file"))),
            str(self.config.get("prompt", "user_text_placeholder", default="{{text}}")),
            str(self.config.get("prompt", "current_time_placeholder", default="{{current_time}}")),
            str(self.config.get("prompt", "timezone_placeholder", default="{{timezone}}")),
        )
        self.model_client = LocalModelClient(self.config, prompt_template)
        self.parser = TaskParser(self.config)
        self.todo_client = TodoTxtClient(self.config)
        self.enabled = True
        self.busy = False
        self.keyboard = None
        self.pyautogui = None
        self.pyperclip = None
        self.tray_icon = None
        self.tray = None
        self.tray_menu_item = None
        self.tray_image = None

    def run(self) -> None:
        self._load_runtime_dependencies()
        trigger = self.config.get("hotkeys", "trigger")
        toggle = self.config.get("hotkeys", "toggle")
        self.log("QuickTodoAdder started.")
        self.log(f"Config file: {self.config.path}")
        self.log(f"Trigger hotkey: {trigger}")
        self.log(f"Toggle hotkey: {toggle}")
        self.log(f"Model endpoint: {self.model_client.url}")
        self.log(f"todo.txt file: {self.todo_client.path}")
        self.log("Tray icon started. Use the tray menu to pause or exit.")
        self.keyboard.add_hotkey(trigger, self.on_trigger)
        self.keyboard.add_hotkey(toggle, self.toggle)
        try:
            self._run_tray()
        except KeyboardInterrupt:
            self.log("Stopping QuickTodoAdder.")
        finally:
            self.keyboard.unhook_all()

    def on_trigger(self) -> None:
        if not self.enabled or self.busy:
            return
        threading.Thread(target=self.process_selection, daemon=True).start()

    def process_selection(self) -> None:
        self.busy = True
        try:
            selected_text = self.capture_selected_text()
            if not selected_text:
                self.log("No selected text captured.")
                return
            current_time = self.current_time_for_prompt()
            self.log(f"Captured {len(selected_text)} chars. Asking model...")
            raw = self.model_client.analyze(selected_text, current_time, self.timezone_name)
            task = self.parser.parse(raw)
            self._enrich_task_from_source(task, selected_text)
            self.log(f"Adding todo.txt task: {task.description}")
            created = self.todo_client.create_task(task, self.timezone)
            self.log(f"Added task to {created.get('path')}: {created.get('title', task.description)}")
        except Exception as exc:
            self.log(f"Error: {exc}")
        finally:
            self.busy = False

    def capture_selected_text(self) -> str:
        restore_clipboard = bool(self.config.get("capture", "restore_clipboard", default=True))
        delay = float(self.config.get("capture", "copy_delay", default=0.15))
        timeout = float(self.config.get("capture", "copy_timeout", default=1.0))
        previous = self.pyperclip.paste()
        sentinel = f"__QUICK_TODO_ADDER_COPY_SENTINEL_{uuid.uuid4()}__"
        self.pyperclip.copy(sentinel)
        self.pyautogui.hotkey("ctrl", "c")
        if delay > 0:
            time.sleep(delay)
        deadline = time.monotonic() + max(timeout, 0.1)
        captured = sentinel
        while time.monotonic() < deadline:
            time.sleep(0.02)
            captured = self.pyperclip.paste()
            if captured != sentinel:
                break
        if restore_clipboard:
            try:
                self.pyperclip.copy(previous)
            except Exception:
                self.log("Warning: failed to restore previous clipboard.")
        if captured == sentinel:
            return ""
        return captured.strip()

    def _enrich_task_from_source(self, task: ParsedTask, source_text: str) -> None:
        match = re.search(r"(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}):(\d{2})(?::\d{2})?)?", source_text)
        if not match:
            return
        date_part = match.group(1)
        hour = match.group(2)
        minute = match.group(3)
        if not task.due_date:
            task.due_date = date_part
        if hour and minute:
            task.due_time = f"{hour}{minute}"

    def current_time_for_prompt(self) -> str:
        now = datetime.now(self.timezone)
        weekday = now.strftime("%A")
        chinese_weekday = CHINESE_WEEKDAYS.get(weekday, weekday)
        return f"{now.strftime('%Y-%m-%dT%H:%M:%S')} ({weekday}, {chinese_weekday})"

    def toggle(self) -> None:
        self.enabled = not self.enabled
        self.log(f"QuickTodoAdder {'enabled' if self.enabled else 'disabled'}.")
        self._refresh_tray_menu()

    def open_todo_file(self) -> None:
        self._open_path(self.todo_client.path)

    def open_config_file(self) -> None:
        self._open_path(self.config.path)

    def open_project_folder(self) -> None:
        self._open_path(self.config.base_dir)

    def quit(self) -> None:
        self.log("Exiting QuickTodoAdder.")
        if self.keyboard:
            self.keyboard.unhook_all()
        if self.tray_icon:
            self.tray_icon.stop()

    def _run_tray(self) -> None:
        if not self.tray or not self.tray_image:
            raise RuntimeError("Missing tray dependency. Run: pip install -r requirements.txt")
        self.tray_icon = self.tray.Icon(
            "QuickTodoAdder",
            self.tray_image,
            "QuickTodoAdder",
            self._build_tray_menu(),
        )
        self.tray_icon.run()

    def _build_tray_menu(self):
        status_label = "Pause listening" if self.enabled else "Enable listening"
        return self.tray.Menu(
            self.tray.MenuItem(status_label, lambda icon, item: self.toggle(), default=True),
            self.tray.MenuItem("Open todo.txt", lambda icon, item: self.open_todo_file()),
            self.tray.MenuItem("Open config", lambda icon, item: self.open_config_file()),
            self.tray.MenuItem("Open project folder", lambda icon, item: self.open_project_folder()),
            self.tray.Menu.SEPARATOR,
            self.tray.MenuItem("Exit", lambda icon, item: self.quit()),
        )

    def _refresh_tray_menu(self) -> None:
        if self.tray_icon:
            self.tray_icon.menu = self._build_tray_menu()
            self.tray_icon.title = f"QuickTodoAdder ({'enabled' if self.enabled else 'paused'})"
            self.tray_icon.update_menu()

    def _open_path(self, path: Path) -> None:
        try:
            os.startfile(str(path))
        except OSError as exc:
            self.log(f"Failed to open {path}: {exc}")

    def _load_runtime_dependencies(self) -> None:
        try:
            import keyboard
            import pyautogui
            import pyperclip
            import pystray
            from PIL import Image, ImageDraw
        except ModuleNotFoundError as exc:
            raise RuntimeError(f"Missing dependency: {exc.name}. Run: pip install -r requirements.txt") from exc
        self.keyboard = keyboard
        self.pyautogui = pyautogui
        self.pyperclip = pyperclip
        self.tray = pystray
        self.tray_image = self._create_tray_image(Image, ImageDraw)

    def _create_tray_image(self, image_module: Any, draw_module: Any):
        image = image_module.new("RGBA", (64, 64), (18, 24, 38, 255))
        draw = draw_module.Draw(image)
        draw.rounded_rectangle((10, 8, 54, 56), radius=8, fill=(245, 247, 250, 255))
        draw.rectangle((18, 20, 46, 24), fill=(40, 116, 240, 255))
        draw.rectangle((18, 31, 42, 35), fill=(40, 116, 240, 255))
        draw.rectangle((18, 42, 36, 46), fill=(40, 116, 240, 255))
        return image

    def log(self, message: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze selected text and add it to a todo.txt file.")
    parser.add_argument("--config", default="config.json", help="Path to the runtime config JSON.")
    parser.add_argument("--text", help="Analyze this text once instead of starting the hotkey listener.")
    parser.add_argument("--print-raw", action="store_true", help="Print the raw model response in one-shot mode.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        app = QuickTodoAdderApp(args.config)
        if args.text:
            current_time = app.current_time_for_prompt()
            raw = app.model_client.analyze(args.text, current_time, app.timezone_name)
            if args.print_raw:
                print(raw, flush=True)
            task = app.parser.parse(raw)
            app._enrich_task_from_source(task, args.text)
            created = app.todo_client.create_task(task, app.timezone)
            print(f"Added task to {created['path']}: {created['title']}", flush=True)
            return 0
        app.run()
    except Exception as exc:
        print(f"Fatal: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
