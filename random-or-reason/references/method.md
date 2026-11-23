# Method

Version 0.1, 2026-11-22. Inspired by neurobiologist Vera Tolchennikova's approach (interview on
the OSNOVA channel, "90 минут для активации вашего мозга", 2026-11-22, 00:09:30–00:26:40 and
01:14:30–01:21:00). Her paper on behaviour entropy in a virtual maze (2026) was not found in open
sources, so her 20 metrics are not reproduced here.

## Mapping her maze to a conversation

| Maze | Conversation |
|---|---|
| Fork = decision point | The model proposes; the user answers |
| Segment fork→fork coded as a letter | One agreement event |
| Random choice at each fork = chaos | Choice explained by no rule |
| Ring repeated many times = perseveration | Streak of bare "yes" / same option every time |
| More than four alternatives → irrational choice | More than four options → `unclear` |
| "Max reinforcement at min action" | Reply engages with the consequential part |

## Signals and thresholds

| Signal | Threshold | Source |
|---|---|---|
| Effective reading speed | > 1000 wpm = not read; base 200 wpm | Brysbaert 2019, *J. Memory and Language* 109:104047 (silent reading of non-fiction 238 wpm, spread ~175–300); 1000 wpm ≈ 4× the average is our choice |
| Fast bare "yes" | bare reply at > 400 wpm (under half the reading time) | 400 wpm is above the 175–300 spread of Brysbaert 2019; the exact value is our choice |
| The person's own words | text left after the clicked button label is removed; a digit counts only in a reply of 4+ words | Our choice: a label is the model's wording, and "да, 2" only picks an option |
| Self-check of the proposal | no reason, no risk named, > 300 words, > 4 options | Our choice; the 4-alternatives limit is Cowan 2001 |
| Options overload | > 4 | Cowan 2001, *BBS* 24:87 (working memory ~4 chunks); Tolchennikova interview 00:17:20 |
| Bare "yes" streak | ≥ 3 | Acquiescence and "weak satisficing" — Krosnick 1991, *Applied Cognitive Psychology* 5:213 |
| Option position habit | rule model beats uniform by BIC margin > 1 | Primacy effects / satisficing, Krosnick 1991 |
| Random choice | ≥ 5 choices, normalised position entropy ≥ 0.85, no rule fits | Complexity of human choice sequences as a measure of randomness: Gauvrit et al. 2017, *PLoS Comput Biol* 13(4):e1005408, https://doi.org/10.1371/journal.pcbi.1005408 ; entropy threshold in maze decisions: Cristín, Méndez, Campos 2022, *Entropy* 24:1819, https://doi.org/10.3390/e24121819 |

The ≥3 streak, the 0.85 entropy and the BIC margin are our choices, not taken from a paper. They
are starting values to tune on real sessions.

## Rule competition

For option choices the script compares models: uniform random, "always first", "always last"
(each rule model gives 0.85 to its pick, the rest spread evenly). Best rule minus uniform
log-likelihood minus 0.5·ln(n) > 1 → the choice follows that rule (autopilot). No rule wins and
positions are spread → random. Fewer than 5 choices → no pattern verdict.

## Known limits

- One agreement without timing cannot be judged: `unchecked`, by design.
- Keyword stakes and 5-letter stems are crude; good enough to decide whether to ask one question.
- This is a nudge for self-reflection, not a psychological assessment.
