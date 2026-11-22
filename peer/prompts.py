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

The helpers all work AT THE SAME TIME. Each helper sees only its own part, never the task, the other parts, or the other helpers' answers.
Afterwards, the different results will be combined into one answer.
If the problem is easily splittable, or has different sections, split it into parts that can be indepentently completed by helpers.
For problems such as "compute the first twenty prime numbers", split up the prime numbers based on difficulty of calculating and distribute accordingly.
If you had 3 helpers available, one might get the first 12 as they are the easiest to compute, then another will get 13 to 18, and the final will get 19 to 20
For independent tasks given in one prompt, split them up as well.
For example, "What is the square root of 25, and what day is it?". One model would compute the square root of 25, and another would give the current day. 

You do not need to use all devices available. Use as many as parts the task can be logically split into. 

Do not put terms like 'next', 'previous' or other linking terms. Each model needs to be able to complete its tasks independently of any other tasks being run concurrently. For the primes example, do not return 'put the next 6 primes'. 


Reply with JSON only: {"parts": ["...", "..."]}"""


def planner_user(task: str, helpers: int, attached: list[str] | None = None) -> str:
    text = (f"Task: {task}\n\n"
            f"There are {helpers} helpers available. Use as many or as few of them as the task needs "
            f"(at most {helpers}): one part per independent piece, and just 1 part if the task can't be split.")
    if attached:
        text += ("\n\nEvery helper also receives these attachments: " + ", ".join(attached)
                 + ". Refer to them by name in the parts; don't copy their contents.")
    return text


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
