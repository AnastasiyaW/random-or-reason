#!/usr/bin/env python3
"""Was the person's agreement with the model grounded, autopilot, or random?

Exit codes: 0 assessed, 1 self-test failed, 2 not assessed (no agreement / no data).
"""
import argparse
import json
import math
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Silent reading of English non-fiction averages 238 wpm, spread ~175-300
# (Brysbaert 2019, J. Memory and Language 109:104047). 200 wpm is a conservative base.
READ_WPM = 200
# Our choice: >1000 wpm is ~4x the measured average, i.e. the text was not read.
NOT_READ_WPM = 1000
# Working memory holds ~4 chunks (Cowan 2001, BBS 24:87); Tolchennikova (OSNOVA, 2026-11-22,
# 00:17:20): with more than four alternatives people choose irrationally.
OVERLOAD_OPTIONS = 4
MIN_PATTERN_EVENTS = 5
# simplification: fixed noise for "rule" models instead of a fitted softmax temperature.
RULE_EPS = 0.15
# our choice: a bare yes at over 400 wpm, above the 175-300 wpm range of Brysbaert 2019
SKIM_RATIO = 0.5
COOLDOWN_TURNS = 3
RECOMMENDED = re.compile(r"recommend|рекоменд", re.I)
ASK_MARK = "\U0001F407"  # every check-in question starts with the rabbit; used for cooldown

APPROVAL = {
    "да", "ага", "угу", "ок", "окей", "ok", "okay", "yes", "yep", "yeah", "sure", "давай",
    "давайте", "делай", "делаем", "го", "погнали", "поехали", "согласна", "согласен",
    "норм", "хорошо", "ладно", "конечно", "валяй", "вперёд", "вперед", "approve", "approved",
    "lgtm", "+", "👍", "go", "fine", "собирай", "запускай", "вливай", "отлично", "супер",
    # the same words typed in the wrong keyboard layout
    "lf", "jr", "lfdfq", "нуы", "щл",
}
# an imperative that can also open a brand-new request ("давай теперь сделай X")
WEAK_APPROVAL = {"давай", "давайте", "делай", "делаем", "го", "go", "погнали", "поехали", "валяй",
                 "вперёд", "вперед", "собирай", "запускай", "вливай", "lfdfq"}
FILLER = {"ну", "а", "ой", "хм", "эм", "well", "hm", "um"}
REFUSAL = re.compile(r"^\s*(нет|не надо|не нужно|стоп|no|nope|stop|отмена|cancel)\b", re.I)
REASON = re.compile(
    r"\b(но|только|кроме|если|чтобы|что бы|потому|так как|поскольку|вместо|лучше|не надо|"
    r"не нужно|поменя\w*|измени\w*|убери|добавь|сначала|а не|because|but|only|except|"
    r"unless|instead|rather|first|change|remove|add)\b|\?", re.I)
HIGH_STAKES = re.compile(
    r"удал|снес|drop\b|delete|rm -rf|force|push|merge|влить|вливан|деплой|deploy|прод\b|"
    r"production|миграц|migrat|оплат|платёж|платеж|payment|charge|деньг|\$\d|€|₽|"
    r"рассылк|отправ\w* (письм|клиент)|send (email|to)|необратим|irreversible|подпис\w* договор|"
    r"увольн|контракт|кредит|invest|инвест", re.I)
MEDIUM_STAKES = re.compile(
    r"план|plan|архитектур|architect|бюджет|budget|срок|deadline|купить|buy|"
    r"выбер|choose|вариант|option|стратег|strategy", re.I)
# simplification: a message is a proposal when it has one of these markers or buttons;
# a classifier if plain statements keep being read as offers
OFFER = re.compile(
    r"\?|предлага|давай|могу|хочешь|хотите|план|шаг|затем|сделаю|запущу|удалю|волью|выбер|"
    r"вариант|plan|step|then|should i|shall|want me|i can|i will|i'll|propose|suggest|"
    r"let me|let's|choose|option", re.I)
WHY = re.compile(r"потому|так как|поскольку|чтобы|причин|зачем|because|so that|since|reason", re.I)
RISK = re.compile(r"необратим|нельзя вернуть|откат|бэкап|риск|потер|backup|rollback|"
                  r"irreversible|risk|undo|cannot be undone", re.I)
STOP = {"этого", "чтобы", "когда", "тогда", "такой", "очень", "просто", "которые", "который", "можно", "нужно", "будет", "потом", "сейчас",
        "about", "would", "should", "there", "their", "which", "these", "those"}


def words(text):
    return re.findall(r"[\w'+-]+|👍", text.lower())


def stems(text):
    # simplification: 5-char prefix as a stem for Russian inflection; a real stemmer if needed
    return {w[:5] for w in words(text) if len(w) >= 5 and w not in STOP and not w.isdigit()}


def strip_wrappers(text):
    text = re.sub(r"<system-reminder>.*?</system-reminder>", "", text, flags=re.S)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith(">")).strip()


def stakes_of(proposal, options=()):
    # text that only informs ("the folder is removed with the session") is not a risky proposal
    is_offer = bool(options) or bool(OFFER.search(proposal))
    if HIGH_STAKES.search(" ".join(options or ())) or (is_offer and HIGH_STAKES.search(proposal)):
        return "high"
    if MEDIUM_STAKES.search(proposal) or len(words(proposal)) >= 300:
        return "medium"
    return "low"


def classify_reply(reply, options, proposal=""):
    toks = words(reply)
    if not toks:
        return {"agreement": False}
    if REFUSAL.search(reply):
        return {"agreement": False}
    chosen, own = None, reply.strip()
    norm = own.lower().rstrip(".!")
    for i, o in enumerate(options or []):
        lab = re.sub(r"\(recommended\)", "", o, flags=re.I).strip().lower()
        if lab and (norm == lab or norm.startswith(lab)):
            # a button label is the model's wording: no evidence of the person's own thought
            chosen, own = i, own[len(lab):]
            break
    if chosen is None and options:
        m = re.fullmatch(r"(?:[^\W\d]+[,.]?\s+){0,2}(\d)\W*", norm)
        if m and 1 <= int(m.group(1)) <= len(options):
            chosen, own = int(m.group(1)) - 1, ""
    head = [t.strip(",.!") for t in toks]
    while len(head) > 1 and head[0] in FILLER:
        head.pop(0)
    if (chosen is None and head[0] in WEAK_APPROVAL and len(head) >= 5
            and not stems(reply) & stems(proposal)):
        return {"agreement": False}  # "давай" + a new unrelated task is a request, not a yes
    agreement = chosen is not None or head[0] in APPROVAL
    own_toks = words(own)
    reasoned = (bool(REASON.search(own)) or len(own_toks) >= 12
                or (bool(re.search(r"\d", own)) and len(own_toks) >= 4))
    return {"agreement": agreement, "chosen": chosen, "reasoned": reasoned, "own": own,
            "bare": agreement and not reasoned and len(own_toks) <= 4}


def self_check(proposal, options=()):
    """What the model's own proposal lacked: the person is not the only one assessed."""
    issues = []
    if stakes_of(proposal, options) == "high":
        if not WHY.search(proposal):
            issues.append("no_reason")
        if not RISK.search(proposal):
            issues.append("risk_not_named")
    if len(words(proposal)) > 300:
        issues.append("too_long")
    if len(options or ()) > OVERLOAD_OPTIONS:
        issues.append("too_many_options")
    return issues


def position_pattern(choices):
    """choices: list of (chosen_index, n_options). Which simple rule explains them best?"""
    # two buttons are "yes / not now": always "yes" there is a yes-streak, not a position habit
    usable = [(c, k) for c, k in choices if c is not None and k >= 3]
    n = len(usable)
    if n < MIN_PATTERN_EVENTS:
        return {"n": n, "verdict": "insufficient"}
    ll_uniform = sum(-math.log(k) for _, k in usable)

    def ll_rule(pick):
        s = 0.0
        for c, k in usable:
            s += math.log(1 - RULE_EPS) if c == pick(k) else math.log(RULE_EPS / (k - 1))
        return s

    rules = {"first_option": ll_rule(lambda k: 0), "last_option": ll_rule(lambda k: k - 1)}
    best = max(rules, key=rules.get)
    # simplification: BIC-style penalty, one extra parameter per rule model
    margin = rules[best] - ll_uniform - 0.5 * math.log(n)
    hist = {}
    for c, k in usable:
        hist[c] = hist.get(c, 0) + 1
    ent = -sum(v / n * math.log(v / n) for v in hist.values())
    k_mean = sum(k for _, k in usable) / n
    ent_norm = ent / math.log(k_mean) if k_mean > 1 else 0.0
    if margin > 1.0:
        verdict = "rule:" + best
    elif ent_norm >= 0.85:
        verdict = "random"
    else:
        verdict = "mixed"
    return {"n": n, "verdict": verdict, "best_rule": best, "margin": round(margin, 2),
            "position_entropy": round(ent_norm, 2)}


def assess(event, history=(), recent_assistant=()):
    """event: {proposal, reply, seconds?, options?, chosen?}; history: earlier assessed events."""
    proposal, reply = event.get("proposal", ""), event.get("reply", "")
    options = event.get("options") or []
    c = classify_reply(reply, options, proposal)
    if event.get("chosen") is not None:
        c.update(agreement=True, chosen=event["chosen"])
        c.setdefault("reasoned", False)
        c.setdefault("bare", not c["reasoned"])
    out = {"stakes": stakes_of(proposal, options), "evidence": [], "ask": False, "ask_focus": None,
           "self": self_check(proposal, options)}
    if not c.get("agreement"):
        out["verdict"] = "not_agreement"
        return out

    # a pick of the option the model marked as recommended is a bare yes to the model, not a
    # position habit; a pick against the recommendation is the person's own decision
    rec = [i for i, o in enumerate(options) if RECOMMENDED.search(o)]
    clicked = c.get("chosen") is not None
    followed_rec = clicked and c["chosen"] in rec
    overrode_rec = clicked and bool(rec) and not followed_rec
    out["pattern_choice"] = None if followed_rec or overrode_rec else c.get("chosen")

    secs = event.get("seconds")
    n_words = len(words(event.get("last_text") or proposal))
    read_ratio = None
    # a typed yes to a short question cannot be timed; a click on a button card can
    if secs is not None and n_words >= (5 if clicked else 40):
        need = n_words / READ_WPM * 60
        read_ratio = secs / need
        wpm = n_words / max(secs, 0.5) * 60
        out["evidence"].append(f"{n_words} слов предложения, ответ через {secs:.0f} с "
                               f"(нужно ~{need:.0f} с на чтение; темп {wpm:.0f} слов/мин)")
    too_fast = read_ratio is not None and n_words / max(secs, 0.5) * 60 > NOT_READ_WPM
    shared = stems(c.get("own", reply)) & stems(proposal)
    if c["reasoned"]:
        out["evidence"].append("в ответе есть условие, правка, вопрос или число")
    if len(shared) >= 2:
        out["evidence"].append("ответ ссылается на конкретику предложения: " + ", ".join(sorted(shared)[:4]))
    if len(options) > OVERLOAD_OPTIONS:
        out["evidence"].append(f"вариантов было {len(options)} (> {OVERLOAD_OPTIONS}): перегруз выбора")

    prior = [h for h in history if h.get("agreement")]

    def hasty(bare, ratio):
        # a bare "yes" after enough time to read the proposal is not autopilot
        return bare and (ratio is None or ratio < 1)

    streak = 0
    for h in reversed(prior):
        if hasty(h.get("bare"), h.get("read_ratio")):
            streak += 1
        else:
            break
    if hasty(c.get("bare"), read_ratio):
        streak += 1
    elif c.get("bare"):
        streak = 0
    choices = [(h.get("pattern_choice", h.get("chosen")), h.get("n_options", 0)) for h in prior]
    if out["pattern_choice"] is not None:
        choices.append((out["pattern_choice"], len(options)))
    pattern = position_pattern(choices)
    out["metrics"] = {"read_ratio": None if read_ratio is None else round(read_ratio, 2),
                      "bare_streak": streak, "pattern": pattern}
    if overrode_rec:
        out["evidence"].append("выбран не тот вариант, который модель пометила рекомендованным")
    # a pattern over the session says nothing about a click made after time to read the card
    judged_by_pattern = out["pattern_choice"] is not None and hasty(True, read_ratio)

    grounded = c["reasoned"] or len(shared) >= 2 or overrode_rec
    if grounded:
        out["verdict"] = "grounded"
    elif too_fast:
        out["verdict"] = "autopilot"
        out["ask_focus"] = "not_read"
    elif judged_by_pattern and pattern["verdict"] == "random":
        out["verdict"] = "random"
        out["ask_focus"] = "random_choice"
        out["evidence"].append(f"выборы из вариантов за сессию не объясняются ни одним правилом "
                               f"(n={pattern['n']}, энтропия позиций {pattern['position_entropy']})")
    elif judged_by_pattern and pattern["verdict"].startswith("rule:"):
        out["verdict"] = "autopilot"
        out["ask_focus"] = "position_habit"
        out["evidence"].append(f"за сессию почти всегда выбран один и тот же по счёту вариант "
                               f"({pattern['best_rule']}, n={pattern['n']})")
    elif streak >= 3:
        out["verdict"] = "autopilot"
        out["ask_focus"] = "bare_streak"
        out["evidence"].append(f"{streak} голых «да» подряд")
    elif len(options) > OVERLOAD_OPTIONS:
        out["verdict"] = "unclear"
        out["ask_focus"] = "overload"
    elif c.get("bare") and read_ratio is not None and read_ratio < SKIM_RATIO:
        out["verdict"] = "unclear"
        out["ask_focus"] = "fast_bare"
        out["evidence"].append("голое «да» больше чем вдвое быстрее обычного чтения")
    elif read_ratio is not None:
        out["verdict"] = "likely_read"
    else:
        out["verdict"] = "unchecked"

    if "recent_ask" in event:  # from a transcript: counted in turns, not in text blocks
        cooled = event["recent_ask"]
    else:
        cooled = any(ASK_MARK in t for t in list(recent_assistant)[-COOLDOWN_TURNS:])
    risky = out["verdict"] in ("autopilot", "random", "unclear")
    if risky and not cooled:
        if out["stakes"] == "high":
            out["ask"] = True
        elif out["stakes"] == "medium" and out["ask_focus"] in ("not_read", "random_choice",
                                                                 "bare_streak", "overload"):
            out["ask"] = True
    if cooled and risky:
        out["evidence"].append("недавно уже спрашивали: не переспрашиваем")
    return out


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def text_blocks(msg):
    c = (msg or {}).get("content")
    if isinstance(c, str):
        return [c]
    return [b.get("text", "") for b in (c or []) if isinstance(b, dict) and b.get("type") == "text"]


def events_from_transcript(path, pending=None):
    """Agreement events from a Claude Code session JSONL.

    pending: (reply, iso_timestamp) of a prompt that is not in the file yet (hook mode).
    """
    events, assistant_texts = [], []
    span_texts, last_text, last_ts, span_options = [], "", None, []
    pending_ask, ask_spans = {}, []

    def close_span(reply, ts):
        asked = any(ASK_MARK in t for t in span_texts)
        secs = (parse_ts(ts) - parse_ts(last_ts)).total_seconds() if ts and last_ts else None
        events.append({"proposal": "\n".join(span_texts), "last_text": last_text, "reply": reply,
                       "options": span_options, "seconds": secs, "ts": ts,
                       "recent_ask": asked or any(ask_spans[-(COOLDOWN_TURNS - 1):]),
                       "assistant_index": len(assistant_texts)})
        ask_spans.append(asked)

    for line in open(path, encoding="utf-8"):
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        t, ts = d.get("type"), d.get("timestamp")
        msg = d.get("message") or {}
        if t == "assistant" and not d.get("isSidechain"):
            for b in msg.get("content") or []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and b.get("text", "").strip():
                    span_texts.append(b["text"])
                    last_text, last_ts = b["text"], ts
                    assistant_texts.append(b["text"])
                elif b.get("type") == "tool_use":
                    name, inp = b.get("name", ""), b.get("input") or {}
                    if name.endswith("wrap_up"):
                        span_options = [a.get("label") or a.get("action", "") for a in inp.get("actions") or []]
                        if inp.get("ask"):
                            span_texts.append(inp["ask"])
                            last_text = (inp.get("recap", "") + "\n" + inp["ask"])
                            last_ts = ts
                    elif name == "AskUserQuestion":
                        qs = inp.get("questions") or []
                        pending_ask[b.get("id")] = (qs, ts)
        elif t == "user" and not d.get("isSidechain"):
            for b in msg.get("content") or [] if isinstance(msg.get("content"), list) else []:
                if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in pending_ask:
                    qs, qts = pending_ask.pop(b["tool_use_id"])
                    raw = b.get("content")
                    raw = raw if isinstance(raw, str) else " ".join(x.get("text", "") for x in raw or [] if isinstance(x, dict))
                    for q in qs:
                        opts = q.get("options") or []
                        labels = [o.get("label", "") for o in opts]
                        card = q.get("question", "") + "\n" + "\n".join(
                            f"{o.get('label', '')}: {o.get('description', '')}" for o in opts)
                        m = re.search(re.escape(q.get("question", "")) + r'"\s*=\s*"([^"]*)"', raw)
                        ans = m.group(1) if m else ""
                        chosen = next((i for i, lab in enumerate(labels) if lab and lab == ans), None)
                        if chosen is None and not ans:
                            continue
                        events.append({"proposal": card, "last_text": card,
                                       "reply": ans, "options": labels, "chosen": chosen,
                                       "seconds": (parse_ts(ts) - parse_ts(qts)).total_seconds() if ts and qts else None,
                                       "ts": ts, "recent_ask": any(ask_spans[-COOLDOWN_TURNS:]),
                                       "assistant_index": len(assistant_texts)})
            if (d.get("origin") or {}).get("kind") != "human" or d.get("isMeta"):
                continue
            reply = strip_wrappers("\n".join(text_blocks(msg)))
            if not reply:
                continue
            close_span(reply, ts)
            span_texts, last_text, last_ts, span_options = [], "", None, []
    if pending and pending[0] and (span_texts or not events or events[-1]["reply"] != pending[0]):
        close_span(*pending)
    return events, assistant_texts


ASK_HINT = {
    "not_read": "the yes came far faster than the proposal can be read",
    "fast_bare": "the yes came more than twice faster than normal reading",
    "bare_streak": "this is the third bare yes in a row",
    "position_habit": "the same option position is picked every time",
    "random_choice": "the choices in this session fit no rule",
    "overload": "there were more than 4 options",
}
SELF_HINT = {
    "no_reason": "your proposal gave no reason",
    "risk_not_named": "your proposal did not name the risk or the way back",
    "too_long": "your proposal was over 300 words",
    "too_many_options": "you offered more than 4 options",
}


def hook():
    """UserPromptSubmit hook: silent unless one check-in question is due. Never blocks a prompt."""
    log = Path.home() / ".claude" / "logs" / "random-or-reason-hook.log"
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        prompt = strip_wrappers(data.get("prompt") or "")
        path = data.get("transcript_path") or ""
        if not prompt or prompt.startswith("<") or not Path(path).is_file():
            return 0
        events, texts = events_from_transcript(path, (prompt, stamp))
        r = assess_session(events, texts)[-1]
        if r["verdict"] != "not_agreement":
            with open(log, "a", encoding="utf-8") as f:
                f.write(f"{stamp} {r['verdict']} ask={r['ask']} focus={r['ask_focus']} "
                        f"stakes={r['stakes']} self={','.join(r['self'])}\n")
        if not r["ask"]:
            return 0
        mine = "; ".join(SELF_HINT[s] for s in r["self"])
        ctx = (f"[random-or-reason] The user's agreement looks unconsidered: "
               f"{ASK_HINT.get(r['ask_focus'], r['verdict'])}. Evidence: {' | '.join(r['evidence'])}. "
               + (f"Your side: {mine}; repair it first in one or two lines and say the gap was yours. "
                  if mine else "")
               + f"Before you act on the agreed step, ask ONE short warm question that starts with "
                 f"{ASK_MARK}, names this fact and the most consequential part of the proposal, and "
                 f"leaves an easy exit («если доверяешь — скажи поехали»). "
                 f"Then wait. It is a nudge, not a gate: never shame, and if the reply is not really "
                 f"an agreement to something risky, skip the question.")
        out = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": ctx}}
        sys.stdout.write(json.dumps(out))
    except Exception as e:  # a broken check must not stop the user's message; the log keeps the error
        try:
            with open(log, "a", encoding="utf-8") as f:
                f.write(f"{stamp} ERROR {e!r}\n")
        except OSError:
            pass
    return 0


def assess_session(events, assistant_texts):
    history, results = [], []
    for ev in events:
        recent = assistant_texts[:ev.get("assistant_index", len(assistant_texts))]
        r = assess(ev, history, recent)
        c = classify_reply(ev.get("reply", ""), ev.get("options"), ev.get("proposal", ""))
        if ev.get("chosen") is not None:
            c.update(agreement=True, chosen=ev["chosen"], bare=not c.get("reasoned", False))
        history.append({**c, "n_options": len(ev.get("options") or []),
                        "pattern_choice": r.get("pattern_choice"),
                        "read_ratio": (r.get("metrics") or {}).get("read_ratio")})
        results.append({"reply": ev.get("reply", "")[:80], **r})
    return results


def auto_transcript():
    root = Path.home() / ".claude" / "projects"
    for folder in [Path(os.getcwd()), *Path(os.getcwd()).parents]:
        d = root / re.sub(r"[^A-Za-z0-9]", "-", str(folder))
        files = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime) if d.is_dir() else []
        if files:
            return files[-1]
    return None


def self_test():
    plan = ("План: удалить старую базу клиентов, затем задеплоить новую версию на прод. " +
            "Шаг подробно описан, тут риски миграции и отката. " * 40)
    cases = [
        ("fast bare yes on risky plan -> autopilot + ask",
         assess({"proposal": plan, "reply": "да", "seconds": 4}),
         lambda r: r["verdict"] == "autopilot" and r["ask"]),
        ("reasoned yes -> grounded, no ask",
         assess({"proposal": plan, "reply": "да, но базу не удаляй, сначала бэкап", "seconds": 40}),
         lambda r: r["verdict"] == "grounded" and not r["ask"]),
        ("bare yes, no timing, no history -> unchecked, no ask (no claim without evidence)",
         assess({"proposal": plan, "reply": "да"}),
         lambda r: r["verdict"] == "unchecked" and not r["ask"]),
        ("bare yes after enough reading time -> likely_read, no ask",
         assess({"proposal": plan, "reply": "ок", "seconds": 400}),
         lambda r: r["verdict"] == "likely_read" and not r["ask"]),
        ("low stakes fast yes -> no ask",
         assess({"proposal": "Назову файл notes.md. " * 20, "reply": "да", "seconds": 1}),
         lambda r: not r["ask"]),
        ("refusal is not agreement",
         assess({"proposal": plan, "reply": "нет, давай иначе", "seconds": 2}),
         lambda r: r["verdict"] == "not_agreement"),
        ("cooldown: rabbit asked recently -> no ask",
         assess({"proposal": plan, "reply": "да", "seconds": 3}, (), [ASK_MARK + " уверена?"]),
         lambda r: r["verdict"] == "autopilot" and not r["ask"]),
    ]
    read_yes = [{"agreement": True, "bare": True, "read_ratio": 5.0}] * 3
    cases.append(("bare yeses after long reading are not a streak",
                  assess({"proposal": plan, "reply": "да", "seconds": 900}, read_yes),
                  lambda r: r["verdict"] == "likely_read" and not r["ask"]))
    fast_yes = [{"agreement": True, "bare": True, "read_ratio": None}] * 2
    cases.append(("third hasty bare yes -> streak autopilot",
                  assess({"proposal": plan, "reply": "ок"}, fast_yes),
                  lambda r: r["verdict"] == "autopilot" and r["ask_focus"] == "bare_streak"))
    opts = ["A", "B", "C"]
    always_first = [{"agreement": True, "chosen": 0, "n_options": 3, "bare": True}] * 7
    cases.append(("always first option -> autopilot by position habit",
                  assess({"proposal": "Выбери вариант бюджета", "reply": "A", "options": opts},
                         always_first),
                  lambda r: r["verdict"] == "autopilot" and r["ask_focus"] == "position_habit"))
    scattered = [{"agreement": True, "chosen": c, "n_options": 3, "bare": True}
                 for c in (2, 0, 1, 1, 2, 0, 2, 1)]
    cases.append(("scattered choices -> random",
                  assess({"proposal": "Выбери вариант бюджета", "reply": "A", "options": opts},
                         scattered),
                  lambda r: r["verdict"] == "random" and r["ask"]))
    cases.append(("too few choices -> no pattern verdict",
                  assess({"proposal": "Выбери вариант", "reply": "B", "options": opts},
                         [{**h, "bare": False} for h in scattered[:2]]),
                  lambda r: r["verdict"] in ("unchecked", "likely_read")))
    mail = ("Предлагаю запустить рассылку по всей базе клиентов сегодня. " +
            "Текст письма готов, проверка меток пройдена. " * 8)
    cases += [
        ("button click repeats the model's words -> not grounded, ask",
         assess({"proposal": mail, "reply": "Да, запускай рассылку", "seconds": 2,
                 "options": ["Да, запускай рассылку", "Не сейчас"]}),
         lambda r: r["verdict"] == "autopilot" and r["ask"]),
        ("'давай' + unrelated new task -> not an agreement",
         assess({"proposal": plan, "reply": "давай теперь сделай лендинг для другого проекта",
                 "seconds": 300}),
         lambda r: r["verdict"] == "not_agreement"),
        ("filler and wrong layout still count as yes",
         assess({"proposal": plan, "reply": "ну lf", "seconds": 2}),
         lambda r: r["verdict"] == "autopilot" and r["ask"]),
        ("bare yes at 700 wpm -> unclear, ask on high stakes",
         assess({"proposal": plan, "reply": "да", "seconds": len(words(plan)) / 700 * 60}),
         lambda r: r["ask_focus"] == "fast_bare" and r["ask"]),
        ("a digit alone is not a reason",
         assess({"proposal": plan, "reply": "да, 2", "seconds": 2}),
         lambda r: r["verdict"] == "autopilot"),
        ("text that only mentions deletion is not high stakes",
         assess({"proposal": "Папка удалится вместе с сессией. " + "Файл лежит в репозитории. " * 12,
                 "reply": "ок", "seconds": 2}),
         lambda r: r["stakes"] == "low" and not r["ask"]),
        ("self-check: risky proposal without reason or risk is flagged",
         assess({"proposal": "Предлагаю удалить старую базу клиентов.", "reply": "да"}),
         lambda r: r["self"] == ["no_reason", "risk_not_named"]),
    ]
    # transcript path as the hook uses it: the prompt is not in the file yet
    def session(reply, earlier=()):
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        rows = []
        for i, (a_text, u_text) in enumerate([*earlier, (mail, None)]):
            rows.append({"type": "assistant", "timestamp": (t0 + timedelta(minutes=10 * i)).isoformat(),
                         "message": {"content": [{"type": "text", "text": a_text}]}})
            if u_text:
                rows.append({"type": "user", "origin": {"kind": "human"},
                             "timestamp": (t0 + timedelta(minutes=10 * i + 5)).isoformat(),
                             "message": {"content": u_text}})
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            f.write("\n".join(json.dumps(r) for r in rows))
        try:
            now = (t0 + timedelta(minutes=10 * len(earlier), seconds=2)).isoformat()
            ev, tx = events_from_transcript(f.name, (reply, now))
            return assess_session(ev, tx)[-1]
        finally:
            os.unlink(f.name)

    cases += [
        ("hook path: fast bare yes to a risky proposal -> ask",
         session("да"), lambda r: r["ask"] and r["ask_focus"] == "not_read"),
        ("hook path: reasoned yes -> no ask",
         session("да, но сначала только на десять адресов"), lambda r: not r["ask"]),
        ("hook path: rabbit asked one turn ago -> cooldown, no ask",
         session("да", [(ASK_MARK + " точно всё прочитала?", "поехали")]),
         lambda r: r["verdict"] == "autopilot" and not r["ask"]),
        ("hook path: rabbit asked four turns ago -> ask again",
         session("да", [(ASK_MARK + " точно?", "поехали"), ("Готово.", "спасибо, а что дальше по списку?"),
                        ("Дальше текст.", "покажи его целиком пожалуйста")]),
         lambda r: r["ask"]),
    ]
    yes_no = [{"agreement": True, "chosen": 0, "n_options": 2, "bare": True, "read_ratio": 3.0}] * 7
    cases.append(("always 'yes' of two buttons, read in time -> not a position habit",
                  assess({"proposal": plan, "reply": "Да", "options": ["Да", "Не сейчас"],
                          "seconds": 900}, yes_no),
                  lambda r: r["verdict"] == "likely_read" and not r["ask"]))
    rec_opts = ["A (Recommended)", "B", "C"]
    card = "Какой вариант бюджета берём на следующий месяц работы"
    cases += [
        ("pick of the recommended option is a yes, not a position habit",
         assess({"proposal": card, "reply": rec_opts[0], "options": rec_opts, "chosen": 0}, always_first),
         lambda r: r["ask_focus"] != "position_habit" and r["verdict"] != "grounded"),
        ("pick against the recommendation -> grounded",
         assess({"proposal": card, "reply": "B", "options": rec_opts, "chosen": 1}, scattered),
         lambda r: r["verdict"] == "grounded" and not r["ask"]),
        ("scattered clicks, but this card was read in time -> not random",
         assess({"proposal": card, "reply": "A", "options": opts, "chosen": 0, "seconds": 120},
                scattered),
         lambda r: r["verdict"] == "likely_read" and not r["ask"]),
    ]
    fails = 0
    for name, r, ok in cases:
        good = ok(r)
        fails += not good
        print(("PASS " if good else "FAIL ") + name + ("" if good else "  -> " + json.dumps(r, ensure_ascii=False)))
    # negative control of the instrument itself: a broken checker that calls everything grounded must fail
    broken = {"verdict": "grounded", "ask": False}
    caught = not cases[0][2](broken)
    print(("PASS " if caught else "FAIL ") + "negative control: always-grounded checker is rejected")
    fails += not caught
    print(f"{len(cases) + 1 - fails}/{len(cases) + 1} passed")
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--transcript", help="Claude Code session .jsonl")
    ap.add_argument("--auto", action="store_true", help="latest session .jsonl of the current folder")
    ap.add_argument("--event", help='JSON: {"proposal","reply","seconds"?,"options"?,"chosen"?}')
    ap.add_argument("--all", action="store_true", help="print every agreement in the session")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--hook", action="store_true", help="UserPromptSubmit hook: JSON on stdin")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if a.hook:
        return hook()
    if a.event:
        r = assess(json.loads(a.event))
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 2 if r["verdict"] == "not_agreement" else 0
    path = a.transcript or (auto_transcript() if a.auto else None)
    if not path or not Path(path).is_file():
        print(json.dumps({"verdict": "not_assessed", "reason": "no transcript found"}, ensure_ascii=False))
        return 2
    events, texts = events_from_transcript(path)
    results = assess_session(events, texts)
    agreed = [r for r in results if r["verdict"] != "not_agreement"]
    if a.all:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0 if agreed else 2
    if not results or results[-1]["verdict"] == "not_agreement":
        print(json.dumps({"verdict": "not_assessed", "reason": "last human message is not an agreement",
                          "last": results[-1] if results else None}, ensure_ascii=False, indent=2))
        return 2
    summary = {"session_agreements": len(agreed),
               "by_verdict": {v: sum(r["verdict"] == v for r in agreed) for v in sorted({r["verdict"] for r in agreed})}}
    print(json.dumps({"last": results[-1], "session": summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
