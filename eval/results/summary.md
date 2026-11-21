Four rounds were run on 21 November 2026, each through all four systems (the log-fed control runs on the cases
where a memory of past work can matter). Rounds 1 and 2 use the first 36 cases: Murray as first built, then Murray
after six general fixes drawn from round 1's failures. Rounds 3 and 4 each use 36 new cases, written after the
round before as further forms of the same test, with Murray's code untouched. The baselines never changed; they
were run again each time.

**What the numbers say**

- **Round 1 did not show it.** As first built, Murray was +0.057 ahead of the better single model on each case, at
  1.1 sigma, and level with it on the nine hold-out cases. He lost three cases to single models, two of them because
  his reply left out what his own executive had already worked out.
- **Round 2: +0.144, 17 wins, no losses, 4.3 sigma.** That is also the most those 36 cases can show. Murray won
  every case on which a single model left any room; the other 19 can only tie, and 17 wins cannot pass 4.32 sigma
  on an exact sign test.
- **Rounds 3 and 4, new cases: +0.095 (13 wins, 1 loss, 3.1 sigma), then +0.068 (10 wins, 2 losses, 2.1 sigma).**
  The effect held on cases nothing was tuned on, and it got smaller each time. On the visual cases he stayed
  perfect: all 24 new ones at full score, and no defective image delivered in any round after the first. The slide
  is in synthesis and it has one cause. When the executive woke on a synthesis question he averaged 0.96 and 0.98.
  When the gate judged the question routine and Hydrogen answered alone, he averaged 0.89 and then 0.71: answers
  with no citation, and twice a claimed experience with no episode behind it. All three of his losses to a single
  model came that way.
- **Rounds 2, 3 and 4 together: +0.102 over 108 cases, 40 wins, 3 losses, 65 ties, 5.93 sigma** (the smaller of a
  paired z of 6.24 and an exact sign test of 5.93; 95% interval +0.071 to +0.134). All three rounds ran the same
  system on different cases. That pooled figure includes the 27 cases the fixes were drawn from. **On the 81 cases
  no fix was ever tuned on** (the 9 hold-outs of the first set and all 72 later ones) **it is +0.088, 27 wins,
  3 losses, 4.45 sigma.** So: past five sigma on everything the final system was run on, and four and a half on
  the part that is strictly out of sample.
- **Where the margin comes from.** Over the 108 cases Murray scores 1.00 on the visual cases against 0.82 for
  either single model; each single model delivered a defective image in 11 of 27 render cases, always naming the
  defect, never silently. On synthesis he scores 0.93 against 0.75 and 0.77, but most of that gap is the credit for
  citing verified experience, which a model with no memory cannot earn. On the traps (changed facts, pushback,
  injected instructions) every system scored 1.0 on all 36 cases in every round.
- **The stricter control beats him on memory.** Hand Neon the whole episodic log as text and it scores 0.98 on the
  48 pushback and synthesis cases against Murray's 0.95: 7 wins for the control, 1 for Murray, 40 ties. Counting
  that control as a baseline, Murray's margin over the 108 cases is +0.037 (12 wins, 7 losses), 0.9 sigma, and
  nearly all the wins are visual cases, which the control did not run. On this evidence the part of the
  architecture that earns its keep is the loop that plans, renders, looks and fixes. The memory system does not:
  at this scale one strong model handed the raw log does better.
- **Noise.** The unchanged baselines moved by +0.034 and +0.022 between rounds 1 and 2. Murray moved by +0.107.
- **Cost.** Murray takes a median of 62 to 87 s per turn against 13 to 16 s for Hydrogen alone and 25 to 31 s for
  Neon alone, and a round costs about $8 against about $1.40 and $4.50.

Things a careful reader should know. The later sets were written by the same hand as the first, each after the
earlier results were known; they mirror the first set case for case (a test checks the pairing) so that their mix
could not be chosen to suit Murray, but they are not an independent benchmark. The judge is one model of the same
family as the systems it judges, and there were no human raters. Round 1's figures are after re-scoring with the
final scorer, which raised the baselines (see the scoring notes below). During round 1 the outcome of one hold-out
case, not its transcript, crossed the screen in a progress line (A05, a silent escape); no fix was derived from it.
In round 3 one run of Neon alone did not answer within 20 minutes and counts as zero for that system; Hydrogen
alone scored 1 on the same case, so the headline is unaffected. The evaluation gives Murray up to three renders and
480 seconds per turn; the public demo allows two renders and 180 seconds.
