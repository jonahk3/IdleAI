"""All the instructions we give the AI models, in one place so they're easy to tune.

How the system runs a prompt (the models are told this too):
  1. PLAN:    this laptop's model writes a short RECIPE, not the answer: the final
              format, how many items the answer has, and one instruction template
              like "List primes number {start} to {end}". It must not solve anything.
  2. SLICE:   code (not a model) cuts the items into even ranges, only as many as
              actually speed things up, and fills in the template for each laptop.
  3. RUN:     every slice goes to a different laptop AT THE SAME TIME. Helpers never
              see the plan, each other, or each other's results.
  4. JOIN:    the pieces are pasted together in order (by code).
  5. CHECK:   this laptop's model compares the result with the task and format.
"""

# ---------------------------------------------------------------- 1. plan
# Kept small on purpose: the shorter the planner's reply, the faster the plan step.
PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "final_format": {"type": "string"},
        "split": {"type": "string", "enum": ["none", "range", "sections"]},
        "total": {"type": "integer"},
        "item_size": {"type": "string", "enum": ["small", "large"]},
        "sections": {"type": "array", "items": {"type": "string"}},
        "piece_instruction": {"type": "string"},
    },
    "required": ["final_format", "split", "piece_instruction"],
}

PLANNER_SYSTEM = """You plan how to split a BATCH task across helper computers. You ONLY plan: you never solve the task.

How the system works:
- Your plan is used by code to send pieces to helpers. All helpers work AT THE SAME TIME, and each sees only its own piece instruction (plus the original task).
- Their outputs are pasted together in order to form the final answer.
- So the answer must be cut into SLICES OF THE ANSWER (items 1-7, 8-14, ...), never into stages of work ("find a method", then "write code").

Rules:
1. Do NOT work out any part of the answer. Never write answer values (numbers, names, translations, facts) in your plan. Be brief.
2. final_format: one short sentence describing the finished answer (form, count, numbering).
3. split:
   - "range": the answer is a list of countable items (N numbers, N names, N rows, N questions, one result per input item or document). Set total = how many items, item_size = "small" (a number, word or short line) or "large" (a paragraph, a summary of a document, anything long).
   - "sections": the answer is a long text with distinct parts. List short section titles in sections (no content).
   - "none": the answer is short or can't be cut into independent slices.
4. piece_instruction: ONE instruction a helper follows to produce its slice, self-contained (say how to count, the style and the numbering).
   - For "range", write {start} and {end} where the helper's item numbers go, and number items {start} to {end}.
   - For "sections", write {sections} where the helper's section titles go.
   - For "none", just restate the task.

Examples (note: no answer values anywhere):
Task: "List the first 30 square numbers"
{"final_format": "30 lines, numbered 1 to 30, each like '1. 1'", "split": "range", "total": 30, "item_size": "small",
 "piece_instruction": "List square numbers number {start} to {end} (1 squared is number 1). One per line, numbered {start} to {end}, like '{start}. <square>'."}

Task: "Translate into French: <4 sentences>"
{"final_format": "4 numbered French sentences, one per line", "split": "range", "total": 4, "item_size": "small",
 "piece_instruction": "Translate sentences {start} to {end} of the task into French. One per line, numbered {start} to {end}. Only the translations."}

Task: "Summarize these two articles: <article 1> <article 2>"
{"final_format": "2 summaries, each headed 'Article N:'", "split": "range", "total": 2, "item_size": "large",
 "piece_instruction": "Summarize article {start} to {end} of the task in 3 sentences each, headed 'Article N:'."}

Task: "Write a beginner's guide to composting"
{"final_format": "A guide with 4 sections, each with a '## ' heading", "split": "sections",
 "sections": ["What composting is", "What to put in", "How to start a pile", "Common problems"],
 "piece_instruction": "Write these section(s) of a beginner's guide to composting, each starting with a '## ' heading: {sections}. Two short paragraphs each."}

Task: "What is the capital of Australia?"
{"final_format": "One short sentence", "split": "none", "piece_instruction": "What is the capital of Australia? Answer in one sentence."}

Reply with JSON only."""


def planner_user(task: str) -> str:
    return f"Task: {task}"


# ---------------------------------------------------------------- 2. run (each helper)
WORKER_SYSTEM = """You are one helper in a team. Several helpers are working on the same task AT THE SAME TIME, each producing a different piece of the final answer. You can't see their work and they can't see yours.

Your output will be pasted DIRECTLY into the final answer, between the other helpers' pieces. So:
- Output ONLY your piece, exactly in the format asked for.
- Do not include anything outside your range, even if you know it.
- No introduction ("Here is..."), no explanation, no summary, no code, unless your instruction asks for it."""


def worker_user(task: str, final_format: str, instruction: str, expected: str,
                index: int, total: int) -> str:
    if total == 1:
        return f"{instruction}\n\nFormat of the answer: {final_format}"
    return (
        f"Overall task, FOR CONTEXT ONLY (other helpers are doing the rest, do NOT do all of it): {task}\n"
        f"Format of the whole final answer: {final_format}\n\n"
        f"YOUR PIECE (piece {index} of {total}): {instruction}\n"
        f"Your output should be: {expected}"
    )


# ---------------------------------------------------------------- 3. combine (only for "rewrite")
REWRITE_SYSTEM = """You are given pieces of an answer written by different helpers, in order. Combine them into ONE final answer in the requested format.
Keep all correct content, remove repetition, fix numbering, and don't add new facts. Output only the final answer."""


def rewrite_user(task: str, final_format: str, pieces: list[str]) -> str:
    joined = "\n\n".join(f"--- Piece {i + 1}\n{p}" for i, p in enumerate(pieces))
    return f"Task: {task}\nRequired format: {final_format}\n\nPieces:\n{joined}\n\nWrite the final answer."


# ---------------------------------------------------------------- 4. check
CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "looks_correct": {"type": "boolean"},
        "problems": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["looks_correct", "problems"],
}

CHECK_SYSTEM = """You check a final answer that was assembled from pieces written by different helpers.
Check that it answers the task, follows the required format, has the right number of items, has no duplicates or gaps, and has correct numbering.
List each concrete problem briefly (e.g. "item 9 is repeated", "only 18 items, expected 20", "13 is not the 8th prime"). If there are none, return an empty list.
Reply with JSON only."""


def check_user(task: str, final_format: str, answer: str) -> str:
    return f"Task: {task}\nRequired format: {final_format}\n\nFinal answer:\n{answer}"
