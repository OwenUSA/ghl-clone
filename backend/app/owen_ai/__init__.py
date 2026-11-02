"""Owen AI — the owner's own assistant (2026-11-02). Separate from every other agent here.

One person uses it: the owner (`OWEN_AI_OWNER_EMAIL`, an ADMIN). Anyone else gets 404 on every
route, as if the module did not exist. It runs on the owner's Claude PLAN, not an API key: the
Claude Code CLI bundled with the Claude Agent SDK, logged in once in the container (`claude
/login`, saved in the owen_ai_home volume) or by `CLAUDE_CODE_OAUTH_TOKEN`. A plan is one
person's; that is why the page is locked to one person.

How a message flows:

  * The browser posts a message (`api.py`). It is saved QUEUED; the request returns at once.
  * `runner.py` (its own container, `python -m app.owen_ai.runner`) claims it, runs one Claude
    Code turn in the workspace `owen-ai/` (CLAUDE.md, knowledge, skills) and saves the answer
    with every tool it used. A thread keeps its Claude session id, so the next message resumes
    the same conversation.
  * The browser polls the thread until the answer is DONE (the app has no streaming, by design).

What Claude can touch is ONLY the tools in `tools.py` plus Read/Glob/Grep inside the workspace —
no shell, no file writes, no web. Phase 1: Zuper reads (any GET the denylist does not refuse, in
`client.owen_ai_read`) and Quo reads (`quo.py`, GET only). Nothing here writes to Zuper or texts
anyone. Writes arrive later as PROPOSED actions a person approves on the page.

Off unless `OWEN_AI_ENABLED`. Registry entry `owen_ai`. Tests never start the CLI: the runner
takes the turn function as a parameter.
"""
