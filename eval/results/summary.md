Three rounds were run on 21 November 2026, each through all four systems (the log-fed control runs on the cases
where a memory of past work can matter). Rounds 1 and 2 use the first 36 cases: Murray as first built, then Murray
after six general fixes drawn from round 1's failures. Round 3 uses 36 new cases, written after round 2 as a second
form of the same test, with Murray's code untouched. The baselines never changed; they were run again each time.

**What the numbers say**

- **Round 1 did not show it.** As first built, Murray was +0.057 ahead of the better single model on each case, at
  1.1 sigma, and level with it on the nine hold-out cases. He lost three cases to single models, two of them because
  his reply left out what his own executive had already worked out.
- **Round 2: +0.144, 17 wins, no losses, 4.3 sigma.** That is also the most those 36 cases can show. Murray won
  every case on which a single model left any room; the other 19 can only tie, and 17 wins cannot pass 4.32 sigma
  on an exact sign test.
- **Round 3, new cases: +0.095, 13 wins, 1 loss, 3.1 sigma on its own.** The effect replicated on cases nothing
  was tuned on, with a smaller margin: the single models left room on 15 of the 36, Murray won 13 of those, tied
  one and lost one. The loss is his own: answering a question the gate judged routine, he claimed a rendering
  experience without citing an episode for it.
- **Rounds 2 and 3 together: +0.120 over 72 cases, 30 wins, 1 loss, 41 ties, 5.54 sigma** (the smaller of a paired
  z of 5.87 and an exact sign test of 5.54; 95% interval +0.080 to +0.160). Both rounds ran the same system on
  different cases. That pooled figure includes the 27 cases the fixes were drawn from. On the 45 cases no fix was
  ever tuned on (the 9 hold-outs of the first set and all 36 new ones) it is +0.104, 17 wins, 1 loss, 3.8 sigma.
- **Where the margin comes from.** Over the 72 cases Murray scores 1.00 on the visual cases against 0.83 and 0.79
  for the single models, and delivered no defective image in either round; each single model delivered one in 8 of
  18 render cases, always naming the defect, never silently. On synthesis he scores 0.96 against 0.74 and 0.77, but
  most of that gap is the credit for citing verified experience, which a model with no memory cannot earn. On the
  traps (changed facts, pushback, injected instructions) every system scored 1.0 in every round.
- **The stricter control still ties him.** Hand Neon the whole episodic log as text and it scores 0.98 on the
  memory cases, slightly ahead of Murray (1 win for Murray, 3 for the control, 28 ties). Counting that control as a
  baseline, Murray's margin over 72 cases is +0.050 (9 wins, 3 losses), 1.5 sigma, and the wins are almost all
  visual cases, which the control did not run. On this evidence the part of the architecture that earns its keep
  is the loop that plans, renders, looks and fixes. The memory system matches brute-force context at this scale;
  it does not beat it.
- **Noise.** The unchanged baselines moved by +0.034 and +0.022 between rounds 1 and 2. Murray moved by +0.107.
- **Cost.** Murray takes a median of 63 to 87 s per turn against 13 to 16 s for Hydrogen alone and 25 to 28 s for
  Neon alone, and a round costs about $8 against about $1.40 and $4.30.

Things a careful reader should know. The second set was written by the same hand as the first, after round 2's
results were known; it mirrors the first set case for case (a test checks the pairing) so that its mix could not be
chosen to suit Murray, but it is not an independent benchmark. The judge is one model of the same family as the
systems it judges, and there were no human raters. Round 1's figures are after re-scoring with the final scorer,
which raised the baselines (see the scoring notes below). During round 1 the outcome of one hold-out case, not its
transcript, crossed the screen in a progress line (A05, a silent escape); no fix was derived from it. In round 3
one run of Neon alone did not answer within 20 minutes and counts as zero for that system; Hydrogen alone scored 1
on the same case, so the headline is unaffected. The evaluation gives Murray up to three renders and 480 seconds
per turn; the public demo allows two renders and 180 seconds.
