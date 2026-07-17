from nexus.llm.base import Message


def trim_messages(messages: list[Message], max_characters: int) -> list[Message]:
    kept: list[Message] = []
    used = 0
    for message in reversed(messages):
        size = len(message.content)
        if kept and used + size > max_characters:
            break
        kept.append(message)
        used += size
    return list(reversed(kept))

