import json
import re
from models import ASSISTANT_SYSTEM_PROMPT, ask_model, ask_cloud_model
from task_management import maybe_handle_task_message
import account_store

ASSISTANT_OUTPUT_TOKEN_LIMIT = 8192
MAX_HISTORY_MESSAGES = 12
MAX_HISTORY_MESSAGE_CHARS = 4000
MAX_HISTORY_TOTAL_CHARS = 12000
MAX_MEMORY_CONTEXT_CHARS = 8000
MEMORY_ACTION_CONFIRMATIONS = {
    "Stored new memory",
    "Updated existing memory",
    "Memory deleted!",
}
CONTEXTUAL_FOLLOWUP_PATTERN = re.compile(
    r"^(?:why\b|how so\b|what (?:do|did) you mean\b|are you sure\b|"
    r"can you explain\b|explain (?:that|this|it)\b|"
    r"make (?:it|that|this) (?:shorter|briefer|simpler)\b|"
    r"shorter\b|tell me more\b|say more\b|go on\b|elaborate\b|clarify\b|"
    r"change the second example\b|can you fix (?:that|this|it) code\b)",
    re.IGNORECASE,
)


def normalize_conversation_history(history, current_message):
    """Accept only bounded user/assistant text and remove a duplicated current turn."""
    if not isinstance(history, list):
        return []

    normalized = []
    for item in history:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        text = item.get("text")
        if role not in {"user", "assistant"} or not isinstance(text, str) or not text.strip():
            continue

        if role == "assistant":
            text = text[-MAX_HISTORY_MESSAGE_CHARS:]
        else:
            text = text[:MAX_HISTORY_MESSAGE_CHARS]
        normalized.append({"role": role, "content": text})

    normalized = normalized[-MAX_HISTORY_MESSAGES:]
    if (
        normalized
        and normalized[-1]["role"] == "user"
        and normalized[-1]["content"].strip() == current_message.strip()
    ):
        normalized.pop()

    recent = []
    remaining = MAX_HISTORY_TOTAL_CHARS
    for item in reversed(normalized):
        content = item["content"]
        if len(content) > remaining:
            if recent:
                break
            content = content[-remaining:] if item["role"] == "assistant" else content[:remaining]
        recent.append({"role": item["role"], "content": content})
        remaining -= len(content)
        if remaining <= 0:
            break

    return list(reversed(recent))


def current_memory_context(history, memories):
    """Give chat a server-read memory snapshot after a confirmed memory action."""
    has_memory_action = any(
        item["role"] == "assistant" and item["content"].strip() in MEMORY_ACTION_CONFIRMATIONS
        for item in history
    )
    if not has_memory_action:
        return None

    records = [
        {field: memory.get(field, "") for field in ("person", "object", "time")}
        for memory in memories
        if isinstance(memory, dict)
    ]
    selected = []
    truncated = False
    for record in reversed(records):
        candidate = [record] + selected
        serialized = json.dumps(candidate, ensure_ascii=False)
        if len(serialized) > MAX_MEMORY_CONTEXT_CHARS:
            truncated = True
            break
        selected = candidate

    snapshot = json.dumps(
        {"complete": not truncated, "records": selected},
        ensure_ascii=False,
    )
    return (
        "Server-verified persistent-memory snapshot (JSON): " + snapshot + "\n"
        "This snapshot describes the current saved records, not what conversation history claims. "
        "Use it as the source of truth for whether a fact is currently stored. "
        "If complete is false, the snapshot is partial, so absence from it does not prove a fact is deleted. "
        "Never recreate or restore facts based on conversation history."
    )


def is_contextual_followup(message, history):
    """Route common follow-ups to chat when they refer to the latest assistant turn."""
    if not history or history[-1]["role"] != "assistant":
        return False
    return bool(CONTEXTUAL_FOLLOWUP_PATTERN.match(message.strip()))


def process_message_with_metadata(message, history=None, conversation_id=None, *, user_id=None, max_memories=None):
    print("Thinking...")
    conversation_history = normalize_conversation_history(history, message)
    task_response = maybe_handle_task_message(
        message,
        conversation_history,
        conversation_id,
        user_id=user_id,
    )
    if task_response is not None:
        return task_response

    memories = account_store.list_memories(user_id)

    model_mode = "cloud"

    prompt = ""
    try:
        with open("prompt.txt", "r", encoding="utf-8") as file:
            prompt = file.read()
    except FileNotFoundError as e:
        raise RuntimeError("Prompt file 'prompt.txt' was not found") from e

    prompt = prompt.replace("{{MEMORIES}}", str(memories))
    prompt = prompt.replace("{{MESSAGE}}", message)

    if is_contextual_followup(message, conversation_history):
        # Bare referential follow-ups are often misclassified without their context.
        # They cannot trigger a persistent-memory action, so send them straight to chat.
        ai_data = {"action": "chat", "intent": "", "person": "", "object": "", "time": ""}
    else:
        if model_mode == "local":
            ai_response = ask_model(prompt, "qwen2.5:1.5b")
        elif model_mode == "cloud":
            ai_response = ask_cloud_model(prompt)

        ai_response = ai_response.strip().removeprefix("```json").removesuffix("```").strip()

        try:
            ai_data = json.loads(ai_response)
        except json.JSONDecodeError:
            print("Model returned invalid JSON!")
            raise ValueError("The message could not be classified.")

    mode = ai_data["action"]
    time = ai_data["time"]
    person = ai_data["person"]
    object = ai_data["object"]
    truncated = False

    if mode == "chat":
        completion = ask_cloud_model(
            message,
            system_prompt=ASSISTANT_SYSTEM_PROMPT,
            additional_system_context=current_memory_context(conversation_history, memories),
            conversation_history=conversation_history,
            max_completion_tokens=ASSISTANT_OUTPUT_TOKEN_LIMIT,
            include_metadata=True,
        )
        response = completion["content"]
        truncated = completion["finish_reason"] == "length"

    elif mode == "remember":
        response = account_store.remember(
            user_id,
            person,
            object,
            time,
            max_memories=max_memories,
        )

    elif mode == "ask":
        intent = ai_data["intent"]
        target = ai_data["object"]
        answer = ""
        people = []

        for m in memories:
            if m["object"] == target and (person == "" or m["person"] == person):
                if intent == "who":
                    people.append(m["person"])
                elif intent == "when":
                    answer = m["time"]
                    break

        if intent == "who":
            answer = ", ".join(people)

        completion = ask_cloud_model(
            f"""Answer the user's question using the retrieved memory.
            Keep it brief and factual.
            You MUST include every person listed in the retrieved memory.
            Do not omit any matching person or invent information.
            Treat retrieved memory as the source of truth.
            For a who question, list every person in the retrieved memory.
            Don't claim the information is missing when matching names are present.
            Never invent names or facts.
            Retrieved memory: {answer}
            Question: {message}""",
            system_prompt=ASSISTANT_SYSTEM_PROMPT,
            max_completion_tokens=ASSISTANT_OUTPUT_TOKEN_LIMIT,
            include_metadata=True,
        )
        response = completion["content"]
        truncated = completion["finish_reason"] == "length"

        print(response)

    elif mode == "forget":
        object = ai_data["object"]
        person = ai_data["person"]
        account_store.forget_memory(user_id, person, object)
        response = "Memory deleted!"

    elif mode == "unknown":
        completion = ask_cloud_model(
            f"""The user sent: '{message}'
            That message didn't match any supported memory action.
            Reply briefly saying you can't help with that.""",
            system_prompt=ASSISTANT_SYSTEM_PROMPT,
            max_completion_tokens=ASSISTANT_OUTPUT_TOKEN_LIMIT,
            include_metadata=True,
        )
        response = completion["content"]
        truncated = completion["finish_reason"] == "length"

        print(response)
        
    else:
        response = "I couldn't process that request."

    return {"reply": response, "truncated": truncated}


def process_message(message, history=None, *, user_id=None):
    """Keep the original string-returning helper available to existing callers."""
    return process_message_with_metadata(message, history, user_id=user_id)["reply"]
