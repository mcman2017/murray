Two full rounds were run on 21 November 2026: all 36 cases through all four systems, twice (the log-fed control runs
on the 16 cases where a memory of past work can matter). Round 1 is Murray as first built. Round 2 is Murray after
six general fixes drawn from round 1's failures. The baselines did not change between rounds; they were simply run
again, which shows how much a score moves by chance.

**What the numbers say**

- **Round 1 did not show it.** As first built, Murray was +0.057 ahead of the better single model on each case, at
  1.1 sigma, and level with it on the nine hold-out cases. He lost three cases to single models, two of them because
  his reply left out what his own executive had already worked out.
- **Round 2: +0.144, 17 wins, no losses, 4.3 sigma.** Not five. The 95% interval is +0.09 to +0.21. On the hold-out
  cases, whose transcripts were not read while the fixes were made, the margin is the same (+0.139, 4 wins, no
  losses), so the fixes carried beyond the cases they were drawn from.
- **Where the margin comes from.** On the visual cases (category A) Murray scores 1.00 against 0.79 for either
  single model. In round 2 he delivered no defective image; each single model delivered one in 5 of the 9 render
  cases, always naming the defect, never silently. On synthesis he scores 0.99 against 0.76, but nearly all of
  that gap is the credit for citing verified experience, which a model with no memory cannot earn: on the judged
  criteria alone the systems are level (0.98 against 0.96). On the traps (changed facts, pushback, injected
  instructions) every system scored 1.0 in both rounds, so there was nothing to win.
- **The stricter control.** Hand Neon the whole episodic log as text and it scores 0.99 on the memory cases, the
  same as Murray. Counting that control as a baseline, Murray's round 2 margin is +0.069 (6 wins, 1 loss), 1.5
  sigma, and five of the six wins are visual cases, which the control did not run. On this evidence the part of the
  architecture that earns its keep is the loop that plans, renders, looks and fixes. The memory system matches
  brute-force context at this scale; it does not beat it.
- **Noise.** The unchanged baselines moved by +0.034 and +0.022 between the two rounds. Murray moved by +0.107.
- **Cost.** Murray takes a median of 87 s per turn against 16 s for Hydrogen alone and 28 s for Neon alone, and one
  round costs $7.58 against $1.29 and $4.71.

Things a careful reader should know: the judge is one model of the same family as the systems it judges, and there
were no human raters. Round 1's figures are after re-scoring with the final scorer, which raised the baselines
(see the scoring notes below). During round 1 the outcome of one hold-out case, not its transcript, crossed the
screen in a progress line (A05, a silent escape); no fix was derived from it. The evaluation gives Murray up to
three renders and 480 seconds per turn; the public demo allows two renders and 180 seconds.
