# Random or Reason

A Claude skill that notices when a person agrees with the model on autopilot or at random, and
asks one friendly question so that they think it through. It is a nudge, not a gate.

Page: https://happyin.work/random-or-reason/

- `random-or-reason/SKILL.md` — the skill.
- `random-or-reason/scripts/agreement_check.py` — the measurement (stdlib Python, no dependencies).
  `--auto` reads the current Claude Code session, `--event` takes one agreement as JSON,
  `--hook` runs as a `UserPromptSubmit` hook, `--self-test` runs the checks, including a
  negative control.
- `random-or-reason/references/method.md` — signals, thresholds, sources.

## Install for Claude Code

macOS and Linux:

    git clone https://github.com/AnastasiyaW/random-or-reason ~/random-or-reason && mkdir -p ~/.claude/skills && cp -r ~/random-or-reason/random-or-reason ~/.claude/skills/

Windows (PowerShell):

    git clone https://github.com/AnastasiyaW/random-or-reason $HOME\random-or-reason; New-Item -ItemType Directory -Force $HOME\.claude\skills | Out-Null; Copy-Item -Recurse $HOME\random-or-reason\random-or-reason $HOME\.claude\skills\

## Check

    python random-or-reason/scripts/agreement_check.py --self-test

Status: v0.2.
