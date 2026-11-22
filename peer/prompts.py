"""All the instructions we give the AI models, in one place so they're easy to tune.

How the system runs a prompt:
  1. PLAN:    this laptop's model writes one instruction per helper laptop.
  2. RUN:     every instruction goes to a different laptop AT THE SAME TIME.
              Helpers see only their own instruction, never each other's work.
  3. COMBINE: this laptop's model turns the helpers' answers into one final answer.
"""

# ---------------------------------------------------------------- 1. plan
def plan_schema(helpers: int) -> dict:
    """The JSON the planner must return. maxItems stops it from writing more parts than laptops
    (Ollama enforces the schema while the model writes, so it can't loop on endless parts)."""
    return {
        "type": "object",
        "properties": {"parts": {"type": "array", "items": {"type": "string"},
                                 "minItems": 1, "maxItems": helpers}},
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


Each string you put in "parts" is sent directly, word for word, to a helper model as its complete instructions. Write each one as an instruction addressed to that helper. Do not label parts ("Helper 1:", "Part 2:") and do not write notes for anyone else. Write at most one part per helper.

IMPORTANT: You are ONLY the planner. Do NOT solve the task or compute any part of the answer, not even partly or to check your plan. The helpers do all of the work.
Never write answer values (numbers, results, names, facts, translations) in your parts. Only describe what each helper must produce.
Good: "List the prime numbers from 2 to 37." Bad: "List these primes: 2, 3, 5, 7, ..."
Keep each part short: one or two sentences.

Reply with JSON only: {"parts": ["...", "..."]}"""


def planner_user(task: str, helpers: int, attached: list[str] | None = None) -> str:
    text = (f"Task: {task}\n\n"
            f"There are {helpers} helpers available. Use as many or as few of them as the task needs "
            f"(at most {helpers}): one part per independent piece, and just 1 part if the task can't be split.")
    text += "\n\nRemember: only plan the parts. Do not work out any of the answer yourself."
    if attached:
        text += ("\n\nThe user attached these files:\n" + "\n".join(f"- {f}" for f in attached)
                 + "\nYou can't see what is inside them. Every helper receives ALL of these files in full, "
                 "together with its part, so a part can say which file(s) to work on by name "
                 "(e.g. \"Rate Resume.pdf on a scale of 1 to 10\"). Use the type and length to decide how to "
                 "split the work, e.g. one long file per helper.")
    return text


# ---------------------------------------------------------------- 2. run (each helper)
WORKER_SYSTEM = "You are a helper. Do exactly what the instruction asks, nothing more. Reply with only the answer."


# ---------------------------------------------------------------- 3. combine
COMBINE_SYSTEM = """You combine answers from several helpers, who each did one part of a task, into one final answer.

Use ONLY what the helpers wrote. Do NOT add any information of your own.
- Keep every number, rating, score, name and fact exactly as the helper wrote it. Never change, round or re-judge them.
- Do not add new opinions, recommendations, conclusions, summaries, examples or facts that are not in the helpers' answers.
- Do not redo or check the helpers' work.
- You may only: put the answers in a sensible order, join them into one clean answer, fix numbering and formatting, and remove text that is repeated word for word.
- If something the task asked for is missing from the helpers' answers, leave it out. Do not fill it in yourself.

Reply with only the final answer."""


def combine_user(task: str, parts: list[str], answers: list[str], attached: list[str] | None = None) -> str:
    pieces = "\n\n".join(
        f"--- Helper {i + 1} was asked: {p}\nAnswer:\n{a}" for i, (p, a) in enumerate(zip(parts, answers))
    )
    files = ""
    if attached:
        files = ("The helpers read these attached files: " + ", ".join(attached)
                 + ". You don't have the files: rely only on the helpers' answers.\n\n")
    return (f"Task: {task}\n\n{files}{pieces}\n\n"
            "Now combine the helpers' answers into the final answer. Use only what they wrote; add nothing.")
