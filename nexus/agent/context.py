from nexus.llm.base import Message


def trim_messages(messages: list[Message], max_characters: int) -> list[Message]:
    system = [message for message in messages if message.role == "system"][:1]
    conversational = [message for message in messages if message.role != "system"]
    system_size = sum(len(message.content) for message in system)
    kept: list[Message] = []
    used = system_size
    for message in reversed(conversational):
        size = len(message.content)
        remaining = max(0, max_characters - used)
        if not kept and size > remaining:
            kept.append(message.model_copy(update={"content": message.content[:remaining]}))
            break
        if kept and used + size > max_characters:
            break
        kept.append(message)
        used += size
    return [*system, *reversed(kept)]
