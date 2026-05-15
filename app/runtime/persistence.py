import json

from sqlmodel import Session, select

from app.database import engine
from app.models import AgentMessageQueue, LogEvent, Message, ToolCall, utc_now
from app.ollama_service import ModelReply


def add_message(run_id: int, sender: str, recipient: str, content: str, message_type: str = "agent") -> None:
    with Session(engine) as session:
        session.add(
            Message(
                run_id=run_id,
                sender=sender,
                recipient=recipient,
                content=content,
                message_type=message_type,
            )
        )
        session.commit()


def enqueue_agent_message(run_id: int, sender: str, recipient: str, content: str, condition: str) -> int:
    visible_content = f"[condition: {condition}]\n{content}"
    with Session(engine) as session:
        queued_message = AgentMessageQueue(
            run_id=run_id,
            sender=sender,
            recipient=recipient,
            content=content,
            condition=condition,
        )
        session.add(queued_message)
        session.flush()
        session.add(
            Message(
                run_id=run_id,
                sender=sender,
                recipient=recipient,
                content=visible_content,
                message_type="agent",
            )
        )
        session.add(
            LogEvent(
                run_id=run_id,
                event="agent_message_enqueued",
                details_json=json.dumps(
                    {
                        "queue_id": queued_message.id,
                        "sender": sender,
                        "recipient": recipient,
                        "condition": condition,
                    }
                ),
            )
        )
        session.commit()
        return int(queued_message.id or 0)


def deliver_agent_messages(run_id: int, recipient: str) -> list[dict]:
    with Session(engine) as session:
        queued_messages = session.exec(
            select(AgentMessageQueue)
            .where(
                AgentMessageQueue.run_id == run_id,
                AgentMessageQueue.recipient == recipient,
                AgentMessageQueue.status == "queued",
            )
            .order_by(AgentMessageQueue.id)
        ).all()

        delivered: list[dict] = []
        for queued_message in queued_messages:
            queued_message.status = "delivered"
            queued_message.delivered_at = utc_now()
            session.add(queued_message)
            delivered.append(
                {
                    "queue_id": queued_message.id,
                    "sender": queued_message.sender,
                    "recipient": queued_message.recipient,
                    "content": queued_message.content,
                    "condition": queued_message.condition,
                }
            )

        if delivered:
            session.add(
                LogEvent(
                    run_id=run_id,
                    event="agent_messages_delivered",
                    details_json=json.dumps(
                        {
                            "recipient": recipient,
                            "message_count": len(delivered),
                            "queue_ids": [message["queue_id"] for message in delivered],
                        }
                    ),
                )
            )
        session.commit()
        return delivered


def add_log(run_id: int, event: str, details: dict, level: str = "info") -> None:
    with Session(engine) as session:
        session.add(LogEvent(run_id=run_id, level=level, event=event, details_json=json.dumps(details)))
        session.commit()


def add_tool_call(run_id: int, agent_name: str, tool_name: str, input_text: str, output_text: str) -> None:
    with Session(engine) as session:
        session.add(
            ToolCall(
                run_id=run_id,
                agent_name=agent_name,
                tool_name=tool_name,
                input_text=input_text,
                output_text=output_text,
            )
        )
        session.commit()


def record_model_log(run_id: int, agent_name: str, reply: ModelReply) -> None:
    add_log(
        run_id,
        "model_response",
        {
            "agent": agent_name,
            "model": reply.model,
            "prompt_tokens": reply.prompt_tokens,
            "output_tokens": reply.output_tokens,
            "total_duration_ms": reply.total_duration_ms,
            "eval_duration_ms": reply.eval_duration_ms,
            "tokens_per_second": reply.tokens_per_second,
            "fallback": reply.fallback,
            "error": reply.error,
        },
        level="warning" if reply.fallback else "info",
    )
