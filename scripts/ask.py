#!/usr/bin/env python3
"""The chat brain: answers a free-form revenue question from the live snapshot.

    pip install anthropic
    python3 ask.py "why is revenue down this week?" [--user U012345]

Ported from the Agentic Society RevOps agent:
  - GROUNDED: it answers only from the snapshot handed to it this turn. Every number in
    the answer is checked against that snapshot (grounding.py); an untraceable number is
    flagged under the answer and logged as a gap - never silently shipped.
  - MODEL-ROUTED: a simple metric lookup goes to Claude Haiku 5.5 at low effort; anything
    analytical (why, what should, compare, forecast, plan) goes to Claude Opus 5.5.
  - CACHED: the stable system prefix (rules, voice, metric catalog) is marked for prompt
    caching, so repeat questions pay for it once. The snapshot rides in the user turn.
  - REMEMBERS: the last few questions and answers per person, as context only. If memory
    and the snapshot disagree, the snapshot wins.
  - HONEST ABOUT GAPS: when it cannot answer, it says so and the gap is logged with a
    stable key, so the backlog shows what to connect next.
Credentials: ANTHROPIC_API_KEY in your environment or .env. Never in code, never in chat.
"""
import argparse, inspect, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import catalog, gaps, grounding, jsonio  # noqa: E402

STATE_DIR = os.environ.get("REVOPS_STATE_DIR", ".revops")
SIMPLE_MODEL, DEEP_MODEL = "claude-haiku-5-5", "claude-opus-5-5"
ANALYTICAL = re.compile(r"\b(why|should|recommend|plan|compare|forecast|strategy|what if|whatif|explain|cause|improve|test|prioriti[sz]e|lever)\b", re.I)
MEMORY_TURNS = 6


def system_prompt():
    skill = jsonio.text(os.path.join(HERE, "..", "SKILL.md"))
    voice_path = os.path.join(HERE, "..", "personality.md")
    voice = jsonio.text(voice_path) if os.path.exists(voice_path) else ""
    return (skill + "\n\n" + voice + "\n\n## Metric catalog (the provenance of every number)\n" + catalog.digest() +
            "\n\n## Chat contract\n"
            "- Answer ONLY from the SNAPSHOT in the user turn. Quote numbers exactly as given; do not re-derive.\n"
            "- A null or 'unavailable' value is unavailable. Never write it as 0.\n"
            "- Say measured, trained, estimate or scenario next to any rate or projection.\n"
            "- You cannot take actions in this chat. Never say you filed, changed, sent or saved anything.\n"
            "- If the snapshot cannot answer the question, say exactly what is missing, then end with one line:\n"
            "  GAP: <what was asked> | <what data would answer it>\n"
            "- Answer the question in the first line. 3-6 lines total. No preamble.\n")


def route(question):
    return (DEEP_MODEL, "medium") if ANALYTICAL.search(question) else (SIMPLE_MODEL, "low")


def _mem_path():
    os.makedirs(STATE_DIR, exist_ok=True)
    return os.path.join(STATE_DIR, "memory.json")


def recall(user):
    return (jsonio.read(_mem_path(), {}) or {}).get(user, [])[-MEMORY_TURNS:]


def remember(user, q, a):
    mem = jsonio.read(_mem_path(), {}) or {}
    mem.setdefault(user, []).append({"q": q[:500], "a": a[:1500]})
    mem[user] = mem[user][-MEMORY_TURNS * 2:]
    jsonio.write(_mem_path(), mem, indent=2)


def ask(question, snapshot, user="owner", client=None):
    import anthropic
    client = client or anthropic.Anthropic()
    model, effort = route(question)
    slim = {k: v for k, v in snapshot.items() if k not in ("daily", "brief")}
    history = recall(user)
    convo = "\n".join(f"Q: {h['q']}\nA: {h['a']}" for h in history)
    user_turn = (("CONVERSATION SO FAR (context only; the snapshot wins on any number):\n" + convo + "\n\n" if convo else "") +
                 "SNAPSHOT:\n" + json.dumps(slim, default=str) + "\n\nQUESTION: " + question)
    kwargs = dict(model=model, max_tokens=4000,
                  system=[{"type": "text", "text": system_prompt(), "cache_control": {"type": "ephemeral"}}],
                  messages=[{"role": "user", "content": user_turn}],
                  output_config={"effort": effort})
    try:
        if model == DEEP_MODEL:
            # Refusal fallback: if Opus declines, the API re-runs the request on a fallback
            # model in the same call. Older SDKs lack the `fallbacks` keyword; the field is
            # then sent in the body, which is the same request on the wire.
            create = client.beta.messages.create
            try:
                native = "fallbacks" in inspect.signature(create).parameters
            except (TypeError, ValueError):
                native = False
            fb = {"fallbacks": "default"} if native else {"extra_body": {"fallbacks": "default"}}
            resp = create(betas=["server-side-fallback-2026-07-01"], **fb, **kwargs)
        else:
            resp = client.messages.create(**kwargs)
    except anthropic.AuthenticationError:
        return {"answer": "I can't reach Claude: the API key was rejected. Check ANTHROPIC_API_KEY.", "model": model}
    except anthropic.RateLimitError:
        return {"answer": "Claude is rate limiting right now. Try again in a minute.", "model": model}
    except anthropic.APIConnectionError:
        return {"answer": "I can't reach Claude right now (network). Try again shortly.", "model": model}
    except anthropic.APIStatusError as e:
        return {"answer": f"Claude returned an error ({e.status_code}). Nothing was answered.", "model": model}
    if resp.stop_reason == "refusal":
        return {"answer": "I can't answer that one.", "model": model}
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    gap = re.search(r"^GAP:\s*(.+?)\s*\|\s*(.+)$", text, re.M)
    if gap:
        gaps.add("cant_answer", gap.group(1), gap.group(2))
        text = text[:gap.start()].rstrip()
    g = grounding.check(text, slim)
    if not g["grounded"]:
        gaps.add("ungrounded_answer", question, "untraceable numbers: " + ", ".join(g["ungrounded"]))
        text += "\n\n_Check: " + ", ".join(g["ungrounded"]) + " could not be traced to your data. Treat as unverified._"
    remember(user, question, text)
    return {"answer": text, "model": model, "grounded": g["grounded"], "gap_logged": bool(gap),
            "cache_read_tokens": getattr(resp.usage, "cache_read_input_tokens", None)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("question")
    ap.add_argument("--snapshot", default=os.path.join(STATE_DIR, "snapshot.json"))
    ap.add_argument("--user", default="owner")
    a = ap.parse_args()
    if not os.path.exists(a.snapshot):
        sys.exit("No snapshot yet. Run: python3 scripts/revops.py brief --post-state")
    print(ask(a.question, jsonio.read(a.snapshot), a.user)["answer"])
