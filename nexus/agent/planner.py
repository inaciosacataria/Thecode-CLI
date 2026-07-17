def planning_request(request: str) -> str:
    return f"Analyze the repository and produce a numbered implementation plan only. Do not modify files. Task: {request}"

