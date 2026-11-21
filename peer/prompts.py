"""All the instructions we give the AI models, in one place so they're easy to tune.

How the system runs a prompt:
  1. PLAN:    this laptop's model writes one instruction per helper laptop.
  2. RUN:     every instruction goes to a different laptop AT THE SAME TIME.
              Helpers see only their own instruction, never each other's work.
  3. COMBINE: this laptop's model turns the helpers' answers into one final answer.
"""

# ---------------------------------------------------------------- 1. plan
PLAN_SCHEMA = {
    "type": "object",
    "properties": {"parts": {"type": "array", "items": {"type": "string"}}},
    "required": ["parts"],
}

PLANNER_SYSTEM = """You split a task into parts for helper computers.

The helpers all work AT THE SAME TIME. Each helper sees only its own part, never the task, the other parts, or the other helpers' answers. Afterwards, their answers are combined into the final answer.

Write the parts so that:
- Each part produces a different piece of the final answer, not a step of the work. Good: "items 1 to 10" and "items 11 to 20". Bad: "find a method", then "use the method".
- Each part is complete on its own: include every detail the helper needs, because it can't see the task.
- Each part is easy to do directly. Prefer concrete ranges, e.g. "all prime numbers between 30 and 70" rather than "the 11th to 20th prime numbers".
- Parts don't overlap, and together they cover the whole task.
- Use only as many parts as make sense. A short task, or one that can't be split, gets 1 part (the task itself).

Reply with JSON only: {"parts": ["...", "..."]}"""


def planner_user(task: str, helpers: int) -> str:
    return f"Task: {task}\n\nThere are {helpers} helpers, so use at most {helpers} parts."


# ---------------------------------------------------------------- 2. run (each helper)
WORKER_SYSTEM = "You are a helper. Do exactly what the instruction asks, nothing more. Reply with only the answer."


# ---------------------------------------------------------------- 3. combine
COMBINE_SYSTEM = """You write the final answer to a task, using answers from several helpers who each did one part of it.
Put their answers together in order as one clean answer: fix the numbering, remove duplicates, correct anything that is clearly wrong, and fill in anything missing, so the answer fully matches the task.
Reply with only the final answer."""


def combine_user(task: str, parts: list[str], answers: list[str]) -> str:
    pieces = "\n\n".join(
        f"--- Helper {i + 1} was asked: {p}\nAnswer:\n{a}" for i, (p, a) in enumerate(zip(parts, answers))
    )
    return f"Task: {task}\n\n{pieces}\n\nNow write the final answer to the task."
