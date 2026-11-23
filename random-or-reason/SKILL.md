---
name: random-or-reason
description: >
  Checks whether a person agreed with the model's proposal on purpose or on autopilot / at random,
  and if the metrics say "autopilot" on something that matters, asks one short, friendly
  question to make them think. Use when: the user replies "да / ок / давай / делай / yes / go" or
  clicks an option right after a plan, a choice of options, a purchase, a deploy, a deletion, a
  send, or any proposal with consequences; when the user asks "я рандомно согласилась?",
  "did I just agree without thinking?", "проверь, обоснованно ли я решила". Measures reading time
  against proposal length, reasons in the reply, streaks of bare "yes", and option-position habits
  across the session (first option every time = autopilot; no rule fits = random). Do NOT use for
  trivial agreements (a file name, a wording tweak) or as an approval gate: it only nudges.
---

# Random or Reason

The goal is motivation, not control. People like to know whether they decided or just clicked.
The skill measures; the question is a nudge to think, never a gate.

## When to run

Run `scripts/agreement_check.py` after the user agrees to something you proposed, before you act
on it, when the proposal has consequences (plan, choice between options, money, deploy, delete,
send, sign). Skip it for trivial agreements (file name, wording) — the script also says `ask:false`
for low stakes.

## Steps

1. Run the check on the current session (Claude Code keeps the transcript with timestamps):
   ```bash
   python scripts/agreement_check.py --auto
   ```
   `--auto` takes the newest `.jsonl` in `~/.claude/projects/<current folder slug>/`. If several
   sessions share the folder, pass `--transcript <path>` explicitly.
   No transcript (claude.ai, API, another app) — build the event yourself:
   ```bash
   python scripts/agreement_check.py --event '{"proposal":"<your proposal>","reply":"<user reply>","options":["A","B"],"chosen":0}'
   ```
   Without `seconds` the reading-time signal is absent; the script will not invent it.
2. Read `last.verdict`, `last.ask`, `last.ask_focus`, `last.evidence`, and `last.self`
   (what your own proposal lacked, see "Check yourself first").
3. `ask: false` → just proceed. Do not mention the check unless the user asked for it.
4. `ask: true` → before acting, ask **one** question (rules below). Then wait.
5. The user explains or says "доверяю / уверена / just do it" → proceed without a second question.
   Their word wins: this is a nudge, not a lock.
6. The user asked directly "я рандомно?" → show the verdict and the evidence lines in plain words,
   and the session summary (`session.by_verdict`).

## Automatic mode (hook)

Without a hook the check runs only when the model remembers it. To run it on every user message,
add a `UserPromptSubmit` hook to `~/.claude/settings.json`:

```json
{"hooks": [{"type": "command", "timeout": 15,
  "command": "python \"<home>/.claude/skills/random-or-reason/scripts/agreement_check.py\" --hook"}]}
```

- The hook is silent unless a question is due. Then it adds a `[random-or-reason]` note to your
  context with the fact, the evidence and your own gaps: ask the one 🐇 question before acting.
- If the note contradicts what you see (the reply is not an agreement to anything risky), skip
  the question: the note is a hint, not an order.
- It never blocks a message: any error exits 0 and goes to
  `~/.claude/logs/random-or-reason-hook.log`, which also keeps one line per assessed agreement.

## Verdicts

| verdict | meaning |
|---|---|
| `grounded` | the person's own words (not a button label) have a condition, edit, question, or refer to concrete parts of the proposal |
| `likely_read` | bare "yes", but enough time passed to read the proposal |
| `autopilot` | faster than skimming speed, or a streak of 3+ bare "yes", or always the same option position |
| `random` | choices from options fit no simple rule (≥5 choices, high position entropy) |
| `unclear` | more than 4 options and no reasons (overload), or a bare "yes" at over 400 words/min (`fast_bare`) |
| `unchecked` | bare "yes" without timing or history: not enough to judge — do not claim autopilot |
| `not_agreement` | the reply is not an agreement |

## How to ask (when `ask: true`)

- Start with 🐇 — the script uses it for cooldown (no second question within 3 turns).
- One question, one or two sentences, warm and a little playful. No lecture, no list of metrics.
- Name the concrete fact and point to the most consequential part of the proposal.
- Always leave an easy exit: "если доверяешь — скажи «поехали»".

By `ask_focus`:
- `not_read`: «🐇 Ты согласилась за 4 секунды с планом на 600 слов — по метрикам это похоже на автопилот. Что для тебя главное в шаге с удалением базы? Если доверяешь — скажи «поехали».»
- `bare_streak`: «🐇 Это уже третье «да» подряд без единой правки. Точно всё устраивает, или где-то хочется поспорить?»
- `position_habit`: «🐇 Ты каждый раз выбираешь первый вариант. Он правда лучший здесь, или просто сверху?»
- `random_choice`: «🐇 Твои выборы за сессию не складываются ни в одно правило — похоже на монетку. По какому признаку выбрала сейчас?»
- `overload`: «🐇 Вариантов было много, в таком случае люди часто выбирают наугад. Давай сузим до двух — какие два оставить?»

- `fast_bare`: as `not_read`, but softer: the person may have skimmed, not skipped. «🐇 Ответ пришёл вдвое быстрее, чем это читается. Глянь ещё раз на шаг с удалением — всё так? Если да — скажи «поехали».»

Match the user's language. Never shame, never say "irrational".

## Check yourself first

A hasty "yes" is often the fault of the proposal. `self` lists what your proposal lacked:

| `self` item | meaning | what to do |
|---|---|---|
| `no_reason` | a risky proposal with no "because" | give the reason in one sentence |
| `risk_not_named` | a risky proposal that does not say what can be lost or how to undo it | name the risk and the way back |
| `too_long` | over 300 words for one decision | restate the decision in two lines |
| `too_many_options` | more than 4 options | cut to the best two |

- `ask: true` and `self` is not empty: repair your side inside the same question. First the short
  restatement with the reason and the risk, then the question. Say the gap was yours
  («я расписала длинно»), not the person's.
- `ask: false`: use `self` silently for your next proposal. Do not report it.

## Gotchas

- Reading time is measured from the last text block of the reply-to message; the user may have
  read while it streamed. That is why "not read" triggers only above 1000 words/min (about 4× the
  measured average of 238 wpm), not at normal reading speed.
- A proposal made of many small messages: only the last text block counts for timing; the whole
  span counts for stakes and shared terms.
- Stakes are keyword-based: a design discussion that merely mentions "деплой" or "деньги" is rated
  high. Use judgment: if nothing irreversible is about to happen, you may skip the question.
- Messages typed while the model is working (queued) are not parsed in v0.1.
- A click on a button counts as a bare "yes": the label is your wording, not theirs.
- Position habit needs 3+ options. Two buttons are "yes / not now": always "yes" there is a
  yes-streak, judged by reading time, not a habit of position.
- An option you marked "(Recommended)" is your advice. A pick of it is a bare "yes" and is left
  out of the position pattern; a pick against it is `grounded`.
- "Random" and "position habit" apply only to a click made faster than the card can be read.
- Cooldown is counted in turns when the transcript is read (`--auto`, `--hook`): no second 🐇
  question within 3 turns.
- A short direct question (under 40 words) answered "yes" in a second stays `unchecked`: it can
  be read that fast, so the script does not ask.
- "давай / делай" followed by a new unrelated task (5+ words, no shared terms) is a new request,
  not an agreement.
- Text with no proposal marker and no buttons is treated as information: a mention of "удалится"
  in it does not make the stakes high.
- Shared-terms uses 5-letter prefixes as stems: short words and synonyms are missed.
- One agreement with no history is `unchecked` on purpose. Never turn "no data" into "autopilot".

## Troubleshooting

| symptom | cause | fix |
|---|---|---|
| exit 2, `no transcript found` | not Claude Code, or folder slug differs | pass `--transcript`, or use `--event` |
| exit 2, `last human message is not an agreement` | user said no / asked a question | nothing to check |
| `read_ratio: null` | proposal < 40 words or no timestamps | expected; other signals still work |
| always `grounded` | long replies (≥12 words) count as reasoned | expected: a long reply is engagement |
| wrong session analysed | another session in the same folder is newer | pass `--transcript` with the session id |

## Method and sources

`references/method.md`. Self-test with a negative control: `python scripts/agreement_check.py --self-test`.
