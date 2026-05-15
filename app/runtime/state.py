from typing import TypedDict


class DemoState(TypedDict, total=False):
    run_id: int
    user_message: str
    template_name: str
    workflow_context: str
    conversation_memory: str
    outputs: dict[str, str]
    route_choices: dict[str, str]
    final: str
