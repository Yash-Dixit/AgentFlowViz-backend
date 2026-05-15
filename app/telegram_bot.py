import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlmodel import Session, select

from app.config import Settings, get_settings
from app.database import engine
from app.message_formatting import format_telegram_html, split_message
from app.models import Run, WorkflowTemplate
from app.runtime import execute_demo_run
from app.runtime.memory import update_conversation_memory
from app.runtime.persistence import add_log
from app.services.attachments import save_file_bytes_for_run


@dataclass(frozen=True)
class TelegramAttachment:
    file_id: str
    filename: str
    mime_type: str
    file_size: int = 0


class TelegramBotRunner:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._offset = 0
        self._poll_timeout_seconds = 5
        self._last_error = ""
        self._selected_template_by_user: dict[int, str] = {}
        self._memory_threads: list[threading.Thread] = []
        self._diagnostics: dict[str, Any] = {
            "bot_username": "",
            "bot_id": None,
            "polls": 0,
            "updates_received": 0,
            "last_poll_at": "",
            "last_poll_update_count": 0,
            "last_update_at": "",
            "last_update_id": None,
            "last_update_type": "",
            "last_action": "",
            "last_message_user_id": None,
            "last_message_chat_id": None,
            "last_message_text_preview": "",
            "last_callback_data": "",
            "last_run_id": None,
            "last_update_error": "",
            "last_outgoing_chat_id": None,
            "last_outgoing_status": "",
            "last_send_error": "",
            "polling_prepared_at": "",
        }

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def status(self) -> dict[str, Any]:
        return {
            "configured": bool(self.settings.telegram_bot_token),
            "running": self.is_running,
            "allowed_user_ids": sorted(self.settings.allowed_telegram_users),
            "setup_mode": not bool(self.settings.allowed_telegram_users),
            "last_error": self._last_error,
            "diagnostics": dict(self._diagnostics),
        }

    def start(self) -> dict[str, Any]:
        if not self.settings.telegram_bot_token:
            return {"started": False, "reason": "TELEGRAM_BOT_TOKEN is not configured."}
        if self._thread and not self._thread.is_alive():
            self._thread = None
        if self.is_running:
            return {"started": False, "reason": "Telegram bot is already running."}

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, name="telegram-bot", daemon=True)
        self._thread.start()
        return {"started": True}

    def stop(self) -> dict[str, Any]:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=self._poll_timeout_seconds + 5)
            if not self._thread.is_alive():
                self._thread = None
        return {"stopped": not self.is_running, "running": self.is_running}

    def _api_url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/{method}"

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                with httpx.Client(timeout=self._poll_timeout_seconds + 5) as client:
                    self._prepare_polling(client)
                    while not self._stop_event.is_set():
                        self._diagnostics["polls"] += 1
                        self._diagnostics["last_poll_at"] = self._utc_now()
                        response = client.get(
                            self._api_url("getUpdates"),
                            params={"timeout": self._poll_timeout_seconds, "offset": self._offset},
                        )
                        self._raise_for_status(response)
                        payload = response.json()
                        updates = payload.get("result", [])
                        self._diagnostics["last_poll_update_count"] = len(updates)
                        for update in updates:
                            update_id = update.get("update_id")
                            if isinstance(update_id, int):
                                self._offset = max(self._offset, update_id + 1)
                            self._diagnostics["updates_received"] += 1
                            try:
                                self._handle_update(client, update)
                            except Exception as exc:
                                self._record_update_error(exc, "update_failed")
                        if not updates:
                            self._last_error = ""
            except Exception as exc:
                self._last_error = self._safe_error(exc)
                time.sleep(5)

    def _handle_update(self, client: httpx.Client, update: dict[str, Any]) -> None:
        self._diagnostics["last_update_at"] = self._utc_now()
        self._diagnostics["last_update_id"] = update.get("update_id")
        callback_query = update.get("callback_query")
        if callback_query:
            self._diagnostics["last_update_type"] = "callback_query"
            self._handle_callback_query(client, callback_query)
            return

        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        user = message.get("from") or {}
        chat_id = chat.get("id")
        user_id = user.get("id")
        text = (message.get("text") or "").strip()
        caption = (message.get("caption") or "").strip()
        attachment = self._attachment_from_message(message)
        self._diagnostics.update(
            {
                "last_update_type": "message",
                "last_message_user_id": user_id,
                "last_message_chat_id": chat_id,
                "last_message_text_preview": (text or caption)[:160],
            }
        )

        if not chat_id or not user_id or (not text and not caption and not attachment):
            self._diagnostics["last_action"] = "ignored_missing_chat_user_or_text"
            return

        if text.startswith("/whoami"):
            self._send_message(
                client,
                chat_id,
                f"Your Telegram user ID is {user_id}. Add this to TELEGRAM_ALLOWED_USER_IDS in the backend .env.",
            )
            self._diagnostics["last_action"] = "sent_whoami"
            return

        if text.startswith("/start"):
            self._send_start_message(client, chat_id, user_id)
            self._diagnostics["last_action"] = "sent_start"
            return

        if text.startswith("/help"):
            self._send_help(client, chat_id)
            self._diagnostics["last_action"] = "sent_help"
            return

        allowed_users = self.settings.allowed_telegram_users
        if not allowed_users:
            self._send_message(
                client,
                chat_id,
                "Setup mode: workflows are disabled until TELEGRAM_ALLOWED_USER_IDS is configured. Send /whoami to get your ID.",
            )
            self._diagnostics["last_action"] = "blocked_setup_mode"
            return

        if user_id not in allowed_users:
            self._send_message(client, chat_id, f"Not authorized. Your Telegram user ID is {user_id}.")
            self._diagnostics["last_action"] = "blocked_unauthorized_user"
            return

        if text.startswith("/templates"):
            self._send_template_picker(client, chat_id)
            self._diagnostics["last_action"] = "sent_template_picker"
            return

        if text.startswith("/runs"):
            self._send_recent_runs(client, chat_id)
            self._diagnostics["last_action"] = "sent_recent_runs"
            return

        if text.startswith("/status"):
            self._send_latest_run_status(client, chat_id)
            self._diagnostics["last_action"] = "sent_latest_status"
            return

        if text.startswith("/cancel"):
            self._selected_template_by_user.pop(user_id, None)
            default_template = self._default_template_name()
            default_label = self._template_label(default_template) if default_template else "the first available workflow"
            self._send_message(client, chat_id, f"Cleared your selected workflow. The next prompt will use {default_label}.")
            self._diagnostics["last_action"] = "cleared_selected_workflow"
            return

        selected_template = self._selected_template_by_user.get(user_id)
        if selected_template and not self._template_exists(selected_template):
            self._selected_template_by_user.pop(user_id, None)
            selected_template = None

        template_name = self._template_for_message(selected_template, bool(attachment))
        if not template_name:
            self._send_message(client, chat_id, "No workflow templates are configured yet. Create one in Streamlit first.")
            self._diagnostics["last_action"] = "blocked_no_workflow_templates"
            return

        try:
            prompt = text or caption or self._default_attachment_prompt(attachment)
            run_id = self._create_run(prompt, template_name, user_id, chat_id)
            if attachment:
                self._store_telegram_attachment(client, run_id, attachment, prompt)
            self._diagnostics["last_run_id"] = run_id
            self._diagnostics["last_action"] = "run_created"
            self._diagnostics["last_update_error"] = ""
            self._execute_run_with_typing(client, chat_id, run_id, prompt)
            final_output = self._get_run_output(run_id)
            self._send_message(
                client,
                chat_id,
                final_output
                or "I finished the request, but I could not produce a useful answer. Please try rephrasing it.",
            )
            self._defer_memory_update(run_id)
        except Exception as exc:
            self._record_update_error(exc, "run_failed")
            try:
                self._send_message(
                    client,
                    chat_id,
                    "I hit a backend issue while starting that workflow. Please try again in a moment.",
                )
            except Exception:
                return

    def _execute_run_with_typing(self, client: httpx.Client, chat_id: int, run_id: int, text: str) -> None:
        stop_typing = threading.Event()
        typing_thread = threading.Thread(
            target=self._typing_loop,
            args=(client, chat_id, stop_typing),
            name=f"telegram-typing-{run_id}",
            daemon=True,
        )
        typing_thread.start()
        try:
            execute_demo_run(run_id, text, update_memory=False)
        finally:
            stop_typing.set()
            typing_thread.join(timeout=1)

    def _defer_memory_update(self, run_id: int) -> None:
        thread = threading.Thread(
            target=self._update_memory_safely,
            args=(run_id,),
            name=f"telegram-memory-{run_id}",
            daemon=True,
        )
        self._memory_threads.append(thread)
        thread.start()

    def _update_memory_safely(self, run_id: int) -> None:
        try:
            update_conversation_memory(run_id)
        except Exception as exc:
            add_log(run_id, "conversation_memory_update_failed", {"error": self._safe_error(exc)}, level="warning")

    def _wait_for_memory_updates(self) -> None:
        for thread in list(self._memory_threads):
            thread.join(timeout=10)
            if not thread.is_alive():
                self._memory_threads.remove(thread)

    def _typing_loop(self, client: httpx.Client, chat_id: int, stop_typing: threading.Event) -> None:
        while not stop_typing.is_set():
            try:
                self._send_chat_action(client, chat_id, "typing")
            except Exception:
                return
            stop_typing.wait(4)

    def _handle_callback_query(self, client: httpx.Client, callback_query: dict[str, Any]) -> None:
        user = callback_query.get("from") or {}
        message = callback_query.get("message") or {}
        chat = message.get("chat") or {}
        user_id = user.get("id")
        chat_id = chat.get("id")
        data = callback_query.get("data") or ""
        callback_query_id = callback_query.get("id")
        self._diagnostics.update(
            {
                "last_callback_data": data,
                "last_message_user_id": user_id,
                "last_message_chat_id": chat_id,
            }
        )

        if callback_query_id:
            self._answer_callback_query(client, callback_query_id)
        if not user_id or not chat_id:
            self._diagnostics["last_action"] = "ignored_callback_missing_user_or_chat"
            return

        allowed_users = self.settings.allowed_telegram_users
        if allowed_users and user_id not in allowed_users:
            self._send_message(client, chat_id, f"Not authorized. Your Telegram user ID is {user_id}.")
            self._diagnostics["last_action"] = "blocked_unauthorized_callback"
            return

        if data == "help":
            self._send_help(client, chat_id)
            self._diagnostics["last_action"] = "callback_help"
            return
        if data == "templates":
            self._send_template_picker(client, chat_id)
            self._diagnostics["last_action"] = "callback_templates"
            return
        if data == "runs":
            self._send_recent_runs(client, chat_id)
            self._diagnostics["last_action"] = "callback_runs"
            return
        if data == "status":
            self._send_latest_run_status(client, chat_id)
            self._diagnostics["last_action"] = "callback_status"
            return
        if data.startswith("template:"):
            template_name = data.split(":", 1)[1]
            if not self._template_exists(template_name):
                self._send_message(client, chat_id, "That workflow template is not available anymore.")
                self._diagnostics["last_action"] = "callback_template_missing"
                return
            self._selected_template_by_user[user_id] = template_name
            self._send_message(
                client,
                chat_id,
                f"Selected {self._template_label(template_name)}. Send your prompt and I will start the agents.",
            )
            self._diagnostics["last_action"] = "callback_template_selected"

    def _attachment_from_message(self, message: dict[str, Any]) -> TelegramAttachment | None:
        document = message.get("document")
        if document:
            filename = document.get("file_name") or f"telegram-document-{document.get('file_unique_id', 'upload')}"
            return TelegramAttachment(
                file_id=document.get("file_id", ""),
                filename=filename,
                mime_type=document.get("mime_type") or "application/octet-stream",
                file_size=int(document.get("file_size") or 0),
            )

        photos = message.get("photo") or []
        if photos:
            photo = photos[-1]
            filename = f"telegram-photo-{photo.get('file_unique_id', 'upload')}.jpg"
            return TelegramAttachment(
                file_id=photo.get("file_id", ""),
                filename=filename,
                mime_type="image/jpeg",
                file_size=int(photo.get("file_size") or 0),
            )
        return None

    def _template_for_message(self, selected_template: str | None, has_attachment: bool) -> str:
        if has_attachment and self._template_exists("image_document_router"):
            return "image_document_router"
        if selected_template and self._template_exists(selected_template):
            return selected_template
        return self._default_template_name()

    def _default_attachment_prompt(self, attachment: TelegramAttachment | None) -> str:
        if attachment and attachment.mime_type.startswith("image/"):
            return "Analyze the uploaded image and summarize the useful findings."
        return "Analyze the uploaded document and summarize the useful findings."

    def _store_telegram_attachment(
        self,
        client: httpx.Client,
        run_id: int,
        attachment: TelegramAttachment,
        caption: str,
    ) -> None:
        payload = self._download_telegram_file(client, attachment.file_id)
        with Session(engine) as session:
            save_file_bytes_for_run(
                session=session,
                run_id=run_id,
                source_channel="telegram",
                caption=caption,
                filename=attachment.filename,
                mime_type=attachment.mime_type,
                data=payload,
            )

    def _download_telegram_file(self, client: httpx.Client, file_id: str) -> bytes:
        response = client.get(self._api_url("getFile"), params={"file_id": file_id})
        self._raise_for_status(response)
        file_payload = response.json().get("result") or {}
        file_path = file_payload.get("file_path")
        if not file_path:
            raise RuntimeError("Telegram did not return a downloadable file path.")

        download_url = f"https://api.telegram.org/file/bot{self.settings.telegram_bot_token}/{file_path}"
        download_response = client.get(download_url)
        self._raise_for_status(download_response)
        return download_response.content

    def _create_run(self, text: str, template_name: str, user_id: int, chat_id: int) -> int:
        with Session(engine) as session:
            run = Run(
                template_name=template_name,
                source_channel="telegram",
                telegram_user_id=user_id,
                telegram_chat_id=chat_id,
                input_message=text,
                status="queued",
            )
            session.add(run)
            session.commit()
            session.refresh(run)
            return int(run.id)

    def _get_run_output(self, run_id: int) -> str:
        with Session(engine) as session:
            run = session.get(Run, run_id)
            return run.final_output if run else ""

    def _templates(self) -> list[WorkflowTemplate]:
        with Session(engine) as session:
            statement = (
                select(WorkflowTemplate)
                .where(WorkflowTemplate.is_active.is_(True))
                .order_by(WorkflowTemplate.id)
            )
            return list(session.exec(statement).all())

    def _default_template_name(self) -> str:
        templates = self._templates()
        preferred_template = next((template for template in templates if template.name == "smart_task_router"), None)
        if preferred_template:
            return preferred_template.name
        return templates[0].name if templates else ""

    def _template_exists(self, template_name: str) -> bool:
        with Session(engine) as session:
            template = session.exec(
                select(WorkflowTemplate).where(
                    WorkflowTemplate.name == template_name,
                    WorkflowTemplate.is_active.is_(True),
                )
            ).first()
            return bool(template)

    def _template_label(self, template_name: str) -> str:
        labels = {
            "smart_task_router": "Smart Task Router",
            "image_document_router": "Image and Document Router",
            "research_to_report": "Research Report",
            "support_triage": "Support Triage",
            "orchestrated_task_router": "Orchestrated Router",
        }
        return labels.get(template_name, template_name.replace("_", " ").title())

    def _send_start_message(self, client: httpx.Client, chat_id: int, user_id: int) -> None:
        if not self.settings.allowed_telegram_users:
            self._send_message(
                client,
                chat_id,
                (
                    "AgentFlowViz is online, but setup mode is active.\n"
                    "Send /whoami, add your ID to TELEGRAM_ALLOWED_USER_IDS, then restart the backend."
                ),
            )
            return

        templates = self._templates()
        if templates:
            template_names = ", ".join(self._template_label(template.name) for template in templates)
            message = f"AgentFlowViz is online.\nChoose {template_names}, then send me a prompt."
        else:
            message = "AgentFlowViz is online, but no active workflows are available. Enable one in Streamlit first."
        self._send_message(client, chat_id, message, reply_markup=self._main_keyboard())

    def _send_help(self, client: httpx.Client, chat_id: int) -> None:
        self._send_message(
            client,
            chat_id,
            (
                "AgentFlowViz commands:\n"
                "/start - show workflow buttons\n"
                "/templates - choose a workflow\n"
                "/runs - show recent runs\n"
                "/status - show latest run status\n"
                "/whoami - show your Telegram user ID\n"
                "/cancel - clear selected workflow\n\n"
                "After choosing a workflow, send any normal prompt to launch the agents."
            ),
            reply_markup=self._main_keyboard(),
        )

    def _send_template_picker(self, client: httpx.Client, chat_id: int) -> None:
        templates = self._templates()
        if not templates:
            self._send_message(client, chat_id, "No workflow templates are configured yet.")
            return

        keyboard = [
            [{"text": self._template_label(template.name), "callback_data": f"template:{template.name}"}]
            for template in templates
        ]
        self._send_message(
            client,
            chat_id,
            "Choose a workflow template:",
            reply_markup={"inline_keyboard": keyboard},
        )

    def _send_recent_runs(self, client: httpx.Client, chat_id: int) -> None:
        with Session(engine) as session:
            runs = list(session.exec(select(Run).order_by(Run.id.desc()).limit(5)).all())

        if not runs:
            self._send_message(client, chat_id, "No runs yet. Choose a workflow and send a prompt.")
            return

        lines = ["Recent AgentFlowViz runs:"]
        for run in runs:
            lines.append(f"#{run.id} - {run.template_name} - {run.status}")
        self._send_message(client, chat_id, "\n".join(lines), reply_markup=self._main_keyboard())

    def _send_latest_run_status(self, client: httpx.Client, chat_id: int) -> None:
        with Session(engine) as session:
            run = session.exec(select(Run).order_by(Run.id.desc())).first()

        if not run:
            self._send_message(client, chat_id, "No runs yet.")
            return

        self._send_message(
            client,
            chat_id,
            f"Latest run #{run.id}\nWorkflow: {run.template_name}\nStatus: {run.status}",
            reply_markup=self._main_keyboard(),
        )

    def _main_keyboard(self) -> dict[str, Any]:
        template_buttons = [
            {"text": self._template_label(template.name), "callback_data": f"template:{template.name}"}
            for template in self._templates()
        ]
        template_rows = [template_buttons[index : index + 2] for index in range(0, len(template_buttons), 2)]
        template_rows.append(
            [
                {"text": "Recent Runs", "callback_data": "runs"},
                {"text": "Help", "callback_data": "help"},
            ]
        )
        return {"inline_keyboard": template_rows}

    def _answer_callback_query(self, client: httpx.Client, callback_query_id: str) -> None:
        response = client.post(self._api_url("answerCallbackQuery"), json={"callback_query_id": callback_query_id})
        self._raise_for_status(response)

    def _send_chat_action(self, client: httpx.Client, chat_id: int, action: str = "typing") -> None:
        response = client.post(self._api_url("sendChatAction"), json={"chat_id": chat_id, "action": action})
        self._raise_for_status(response)

    def _send_message(
        self,
        client: httpx.Client,
        chat_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        for chunk in split_message(text):
            payload: dict[str, Any] = {
                "chat_id": chat_id,
                "text": format_telegram_html(chunk),
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            if reply_markup:
                payload["reply_markup"] = reply_markup
            response = client.post(
                self._api_url("sendMessage"),
                json=payload,
            )
            try:
                self._raise_for_status(response)
                self._diagnostics["last_outgoing_chat_id"] = chat_id
                self._diagnostics["last_outgoing_status"] = "sent"
                self._diagnostics["last_send_error"] = ""
            except Exception as exc:
                safe_error = self._safe_error(exc)
                self._last_error = safe_error
                self._diagnostics["last_outgoing_chat_id"] = chat_id
                self._diagnostics["last_outgoing_status"] = "failed"
                self._diagnostics["last_send_error"] = safe_error
                raise

    def _prepare_polling(self, client: httpx.Client) -> None:
        response = client.post(self._api_url("deleteWebhook"), json={"drop_pending_updates": False})
        self._raise_for_status(response)
        self._refresh_bot_identity(client)
        self._diagnostics["polling_prepared_at"] = self._utc_now()

    def _refresh_bot_identity(self, client: httpx.Client) -> None:
        response = client.get(self._api_url("getMe"))
        self._raise_for_status(response)
        payload = response.json()
        identity = payload.get("result") or {}
        self._diagnostics["bot_username"] = identity.get("username", "")
        self._diagnostics["bot_id"] = identity.get("id")

    def _raise_for_status(self, response: httpx.Response) -> None:
        try:
            response.raise_for_status()
        except Exception as exc:
            raise RuntimeError(self._safe_error(exc)) from None

    def _safe_error(self, exc: Exception) -> str:
        message = str(exc)
        if self.settings.telegram_bot_token:
            message = message.replace(self.settings.telegram_bot_token, "<redacted>")
        return message

    def _record_update_error(self, exc: Exception, action: str) -> None:
        safe_error = self._safe_error(exc)
        self._last_error = safe_error
        self._diagnostics["last_action"] = action
        self._diagnostics["last_update_error"] = safe_error

    def _utc_now(self) -> str:
        return datetime.now(UTC).isoformat(timespec="seconds")


telegram_bot_runner = TelegramBotRunner()
