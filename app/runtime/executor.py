from sqlmodel import Session

from app.database import engine
from app.models import Run, utc_now
from app.runtime.graph import build_demo_graph
from app.runtime.memory import build_conversation_memory_context, update_conversation_memory
from app.runtime.persistence import add_log
from app.runtime.semantic_cache import complete_run_from_cache, find_cached_output, store_cached_output
from app.runtime.templates import load_template, workflow_context, workflow_plan
from app.services.attachments import build_attachment_context


def execute_demo_run(run_id: int, user_message: str, update_memory: bool = True) -> None:
    with Session(engine) as session:
        run = session.get(Run, run_id)
        if not run:
            return
        template_name = run.template_name
        run.status = "running"
        session.add(run)
        session.commit()

    description, graph = load_template(template_name)
    node_names, edges, entry_node = workflow_plan(graph)
    context = workflow_context(template_name, description, node_names, edges)
    attachment_context = build_attachment_context(run_id)
    if attachment_context:
        context = context + "\n\n" + attachment_context
    memory_context = build_conversation_memory_context(run_id)
    add_log(
        run_id,
        "run_started",
        {
            "message": user_message,
            "template_name": template_name,
            "entry_node": entry_node,
            "workflow_nodes": node_names,
        },
    )

    try:
        cache_match = None
        if memory_context.enabled:
            add_log(run_id, "semantic_cache_skipped", {"reason": "conversation_memory_enabled"})
        else:
            cache_match = find_cached_output(run_id, template_name, user_message)
        if cache_match:
            complete_run_from_cache(run_id, user_message, cache_match)
            with Session(engine) as session:
                run = session.get(Run, run_id)
                if run:
                    run.status = "completed"
                    run.final_output = cache_match.final_output
                    run.completed_at = utc_now()
                    session.add(run)
                    session.commit()
            if update_memory:
                update_conversation_memory(run_id)
            add_log(
                run_id,
                "run_completed",
                {
                    "final_length": len(cache_match.final_output),
                    "executed_agents": [],
                    "route_choices": {},
                    "semantic_cache_hit": True,
                    "cache_id": cache_match.cache_id,
                },
            )
            return

        result = build_demo_graph(node_names, edges, entry_node).invoke(
            {
                "run_id": run_id,
                "user_message": user_message,
                "template_name": template_name,
                "workflow_context": context,
                "conversation_memory": memory_context.text,
                "outputs": {},
                "route_choices": {},
            }
        )
        final = result.get("final", "")
        with Session(engine) as session:
            run = session.get(Run, run_id)
            if run:
                run.status = "completed"
                run.final_output = final
                run.completed_at = utc_now()
                session.add(run)
                session.commit()
        store_cached_output(run_id, template_name, user_message, final)
        if update_memory:
            update_conversation_memory(run_id)
        add_log(
            run_id,
            "run_completed",
            {
                "final_length": len(final),
                "executed_agents": list(result.get("outputs", {}).keys()),
                "route_choices": result.get("route_choices", {}),
            },
        )
    except Exception as exc:
        with Session(engine) as session:
            run = session.get(Run, run_id)
            if run:
                run.status = "failed"
                run.error = str(exc)
                run.completed_at = utc_now()
                session.add(run)
                session.commit()
        add_log(run_id, "run_failed", {"error": str(exc)}, level="error")
