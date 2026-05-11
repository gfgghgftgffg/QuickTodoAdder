# -*- coding: utf-8 -*-
"""Capture selected text, analyze it locally, and add it to Microsoft To Do."""

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

import msal
import requests


GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
ALLOWED_IMPORTANCE = {"low", "normal", "high"}
DATETIME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")


class Config:
    DEFAULTS: dict[str, Any] = {
        "hotkeys": {
            "trigger": "ctrl+alt+space",
            "toggle": "ctrl+alt+shift+t",
        },
        "capture": {
            "copy_delay": 0.15,
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
        "microsoft": {
            "client_id": "PASTE_YOUR_AZURE_APP_CLIENT_ID_HERE",
            "tenant_id": "common",
            "scopes": ["Tasks.ReadWrite"],
            "task_list_id": "",
            "task_list_name": "",
            "create_list_if_missing": False,
            "timezone": "Asia/Shanghai",
        },
        "task_defaults": {
            "importance": "normal",
            "append_source_text": True,
            "max_title_length": 180,
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
    title: str
    body: str
    due_date_time: str | None
    reminder_date_time: str | None
    start_date_time: str | None
    importance: str
    categories: list[str]
    checklist_items: list[str]


class TaskParser:
    def __init__(self, config: Config) -> None:
        self.default_importance = str(config.get("task_defaults", "importance", default="normal")).lower()
        if self.default_importance not in ALLOWED_IMPORTANCE:
            self.default_importance = "normal"
        self.max_title_length = int(config.get("task_defaults", "max_title_length", default=180))

    def parse(self, raw_text: str) -> ParsedTask:
        data = self._load_json(raw_text)
        title = self._clean_string(data.get("title"))
        if not title:
            raise ValueError("Model returned an empty title.")
        if len(title) > self.max_title_length:
            title = title[: self.max_title_length].rstrip()

        importance = self._clean_string(data.get("importance")).lower() or self.default_importance
        if importance not in ALLOWED_IMPORTANCE:
            importance = self.default_importance

        return ParsedTask(
            title=title,
            body=self._clean_string(data.get("body")),
            due_date_time=self._clean_datetime(data.get("dueDateTime")),
            reminder_date_time=self._clean_datetime(data.get("reminderDateTime")),
            start_date_time=self._clean_datetime(data.get("startDateTime")),
            importance=importance,
            categories=self._clean_string_list(data.get("categories")),
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

    def _clean_datetime(self, value: Any) -> str | None:
        text = self._clean_string(value)
        if not text or text.lower() == "null":
            return None
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return f"{text}T09:00:00"
        if DATETIME_PATTERN.fullmatch(text):
            return text
        raise ValueError(f"Invalid datetime from model: {text}")


class MicrosoftTodoClient:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.session = requests.Session()
        self.client_id = str(config.get("microsoft", "client_id", default="")).strip()
        self.tenant_id = str(config.get("microsoft", "tenant_id", default="common")).strip() or "common"
        self.scopes = config.get("microsoft", "scopes", default=["Tasks.ReadWrite"]) or ["Tasks.ReadWrite"]
        self.cache_path = config.base_dir / ".msal_token_cache.json"
        self._task_list_id: str | None = str(config.get("microsoft", "task_list_id", default="")).strip() or None
        self._access_token: str | None = None

    def create_task(self, task: ParsedTask, source_text: str, timezone_name: str) -> dict[str, Any]:
        list_id = self._task_list_id or self._resolve_task_list_id()
        payload = self._build_task_payload(task, source_text, timezone_name)
        response = self._request("POST", f"/me/todo/lists/{list_id}/tasks", json=payload)
        created = response.json()
        for item in task.checklist_items:
            self._request(
                "POST",
                f"/me/todo/lists/{list_id}/tasks/{created['id']}/checklistItems",
                json={"displayName": item},
            )
        return created

    def _build_task_payload(self, task: ParsedTask, source_text: str, timezone_name: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "title": task.title,
            "importance": task.importance,
            "status": "notStarted",
        }
        body = task.body
        if self.config.get("task_defaults", "append_source_text", default=True):
            source_block = f"Source text:\n{source_text.strip()}"
            body = f"{body}\n\n{source_block}".strip() if body else source_block
        if body:
            payload["body"] = {"contentType": "text", "content": body}
        if task.categories:
            payload["categories"] = task.categories
        if task.due_date_time:
            payload["dueDateTime"] = {"dateTime": task.due_date_time, "timeZone": timezone_name}
        if task.start_date_time:
            payload["startDateTime"] = {"dateTime": task.start_date_time, "timeZone": timezone_name}
        if task.reminder_date_time:
            payload["isReminderOn"] = True
            payload["reminderDateTime"] = {"dateTime": task.reminder_date_time, "timeZone": timezone_name}
        return payload

    def _resolve_task_list_id(self) -> str:
        desired_name = str(self.config.get("microsoft", "task_list_name", default="")).strip()
        response = self._request("GET", "/me/todo/lists")
        lists = response.json().get("value", [])
        if not isinstance(lists, list) or not lists:
            raise RuntimeError("No Microsoft To Do task lists were found.")

        if desired_name:
            for item in lists:
                if str(item.get("displayName", "")).casefold() == desired_name.casefold():
                    self._task_list_id = item["id"]
                    return self._task_list_id
            if self.config.get("microsoft", "create_list_if_missing", default=False):
                created = self._request("POST", "/me/todo/lists", json={"displayName": desired_name}).json()
                self._task_list_id = created["id"]
                return self._task_list_id
            raise RuntimeError(f"Microsoft To Do list not found: {desired_name}")

        for item in lists:
            if item.get("wellknownListName") == "defaultList":
                self._task_list_id = item["id"]
                return self._task_list_id
        self._task_list_id = lists[0]["id"]
        return self._task_list_id

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        token = self._get_access_token()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        headers["Content-Type"] = "application/json"
        response = self.session.request(method, f"{GRAPH_BASE_URL}{path}", headers=headers, timeout=30, **kwargs)
        if response.status_code == 401:
            self._access_token = None
            token = self._get_access_token(force_interactive=True)
            headers["Authorization"] = f"Bearer {token}"
            response = self.session.request(method, f"{GRAPH_BASE_URL}{path}", headers=headers, timeout=30, **kwargs)
        response.raise_for_status()
        return response

    def _get_access_token(self, force_interactive: bool = False) -> str:
        if self._access_token and not force_interactive:
            return self._access_token
        if not self.client_id or self.client_id == "PASTE_YOUR_AZURE_APP_CLIENT_ID_HERE":
            raise RuntimeError("Please set microsoft.client_id in config.json before signing in.")

        cache = msal.SerializableTokenCache()
        if self.cache_path.exists():
            cache.deserialize(self.cache_path.read_text(encoding="utf-8"))

        app = msal.PublicClientApplication(
            client_id=self.client_id,
            authority=f"https://login.microsoftonline.com/{self.tenant_id}",
            token_cache=cache,
        )

        result: dict[str, Any] | None = None
        accounts = app.get_accounts()
        if accounts and not force_interactive:
            result = app.acquire_token_silent(self.scopes, account=accounts[0])

        if not result:
            flow = app.initiate_device_flow(scopes=self.scopes)
            if "user_code" not in flow:
                raise RuntimeError(f"Failed to create device flow: {flow}")
            print(flow["message"], flush=True)
            result = app.acquire_token_by_device_flow(flow)

        if cache.has_state_changed:
            self.cache_path.write_text(cache.serialize(), encoding="utf-8")

        if not result or "access_token" not in result:
            raise RuntimeError(f"Microsoft login failed: {result}")
        self._access_token = result["access_token"]
        return self._access_token


class QuickTodoAdderApp:
    def __init__(self, config_path: str) -> None:
        self.config = Config(config_path)
        timezone_name = str(self.config.get("microsoft", "timezone", default="Asia/Shanghai"))
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
        self.todo_client = MicrosoftTodoClient(self.config)
        self.enabled = True
        self.busy = False
        self.keyboard = None
        self.pyautogui = None
        self.pyperclip = None

    def run(self) -> None:
        self._load_runtime_dependencies()
        trigger = self.config.get("hotkeys", "trigger")
        toggle = self.config.get("hotkeys", "toggle")
        self.log("QuickTodoAdder started.")
        self.log(f"Config file: {self.config.path}")
        self.log(f"Trigger hotkey: {trigger}")
        self.log(f"Toggle hotkey: {toggle}")
        self.log(f"Model endpoint: {self.model_client.url}")
        self.log("Press Ctrl+C to exit.")
        self.keyboard.add_hotkey(trigger, self.on_trigger)
        self.keyboard.add_hotkey(toggle, self.toggle)
        try:
            self.keyboard.wait()
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
            current_time = datetime.now(self.timezone).strftime("%Y-%m-%dT%H:%M:%S")
            self.log(f"Captured {len(selected_text)} chars. Asking model...")
            raw = self.model_client.analyze(selected_text, current_time, self.timezone_name)
            task = self.parser.parse(raw)
            self.log(f"Creating To Do task: {task.title}")
            created = self.todo_client.create_task(task, selected_text, self.timezone_name)
            self.log(f"Created task: {created.get('title', task.title)}")
        except Exception as exc:
            self.log(f"Error: {exc}")
        finally:
            self.busy = False

    def capture_selected_text(self) -> str:
        restore_clipboard = bool(self.config.get("capture", "restore_clipboard", default=True))
        delay = float(self.config.get("capture", "copy_delay", default=0.15))
        previous = self.pyperclip.paste()
        sentinel = f"__QUICK_TODO_ADDER_COPY_SENTINEL_{uuid.uuid4()}__"
        self.pyperclip.copy(sentinel)
        self.pyautogui.hotkey("ctrl", "c")
        time.sleep(delay)
        captured = self.pyperclip.paste()
        if restore_clipboard:
            self.pyperclip.copy(previous)
        if captured == sentinel:
            return ""
        return captured.strip()

    def toggle(self) -> None:
        self.enabled = not self.enabled
        self.log(f"QuickTodoAdder {'enabled' if self.enabled else 'disabled'}.")

    def _load_runtime_dependencies(self) -> None:
        try:
            import keyboard
            import pyautogui
            import pyperclip
        except ModuleNotFoundError as exc:
            raise RuntimeError(f"Missing dependency: {exc.name}. Run: pip install -r requirements.txt") from exc
        self.keyboard = keyboard
        self.pyautogui = pyautogui
        self.pyperclip = pyperclip

    def log(self, message: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze selected text and add it to Microsoft To Do.")
    parser.add_argument("--config", default="config.json", help="Path to the runtime config JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        QuickTodoAdderApp(args.config).run()
    except Exception as exc:
        print(f"Fatal: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
