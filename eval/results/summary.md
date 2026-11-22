Twelve rounds were run on 21 November 2026, each through all four systems (the log-fed control runs on the cases
where a memory of past work can matter). Three versions of Murray were measured, each against the better of the two
single models on every case:

| Murray | Rounds | Cases | Result |
|---|---|---|---|
| As first built | 1 | the first 36 | +0.057, 1.1 sigma |
| After six fixes from round 1 | 2, 3, 4 | 108: the first 36 and 72 new | +0.102, 40 wins, 3 losses, 5.93 sigma; 4.45 sigma on the 81 never tuned on |
| Final: gate and citation fixes, a seventh day | 5 to 12 | 288 more new | **+0.084, 98 wins, 16 losses, 7.12 sigma, all out of sample** |

The baselines never changed; they were run again in every round. The final system was not changed either: it was
measured three times as cases were added (3.46 sigma after round 6, 4.89 after round 9, 7.12 after round 12), and
counting those three looks the last figure is 6.97.

**What the numbers say**

- **Round 1 did not show it.** As first built, Murray was +0.057 ahead of the better single model on each case, at
  1.1 sigma. He lost three cases to single models, two of them because his reply left out what his own executive
  had already worked out.
- **Round 2: +0.144, 17 wins, no losses, 4.3 sigma.** That is the most those 36 cases can show: he won every case
  on which a single model left any room, and 17 wins cannot pass 4.32 sigma on an exact sign test.
- **Rounds 3 and 4, new cases, same system: +0.095 (3.1 sigma), then +0.068 (2.1 sigma).** The effect held on
  cases nothing was tuned on. All three losses came when the gate judged a synthesis question routine and Hydrogen
  answered alone.
- **Rounds 2, 3 and 4 together: +0.102 over 108 cases, 40 wins, 3 losses, 65 ties, 5.93 sigma** (the smaller of a
  paired z of 6.24 and an exact sign test of 5.93; 95% interval +0.071 to +0.134). That pooled figure includes the
  27 cases the fixes were drawn from. On the 81 cases no fix was ever tuned on it is +0.088, 27 wins, 3 losses,
  4.45 sigma.
- **The final system, rounds 5 and 6: +0.072 over 72 new cases, 22 wins, 4 losses, 3.46 sigma.** Before these
  rounds the gate was changed to wake the executive for hard questions and for any question about his own
  experience, speech was made to carry a citation for anything he says he did, and he was given a seventh day of
  upbringing. Round 5 was clean (13 wins, no losses). Round 6 was not (9 wins, 4 losses): on four synthesis
  questions his answer was judged less complete than a single model's.
- **Rounds 7, 8 and 9: the same system, 108 more new cases.** Nothing was changed after round 6; the only thing
  added was cases, under a plan committed before the first of them ran (`eval/results/plan-rounds-7-9.md`).
  Round 7: +0.065, 14 wins, 2 losses. Round 8: +0.094, 13 wins, 3 losses. Round 9: +0.087, 12 wins, 1 loss. On
  their own: +0.082, 39 wins, 6 losses, 3.58 sigma (paired z 3.58, sign test 5.01). Two of the six losses cost a
  whole point each, both on uploaded images in round 7. On one, his executive named the defect and ended with the
  required `DEFECTS:` line, and speech dropped the line. On the other he read the vertex of a sketched parabola
  half a unit too high. The other four are synthesis questions where a short answer left out a fact the criteria
  asked for.
- **After round 9 the final system stood at 4.89 sigma**: +0.078 over 180 new cases, 61 wins, 10 losses (paired z
  4.89, sign test 6.23). As first scored it read 5.65. An audit then found a scoring error in Murray's favour, and
  correcting it turned one tie into a whole-point loss and took the figure back under five (see the notes below).
- **Rounds 10, 11 and 12: the same system again, 108 more new cases**, under a second plan committed before they
  ran (`eval/results/plan-rounds-10-12.md`) and with the corrected scorer. Round 10: +0.094, 12 wins, 3 losses.
  Round 11: +0.103, 13 wins, 1 loss. Round 12: +0.085, 12 wins, 2 losses. On their own: +0.094, 37 wins, 6 losses,
  4.79 sigma (paired z 5.58, sign test 4.79). He lost no visual case and no trap in these rounds; all six losses
  are synthesis questions, each by 0.10 or 0.15, where a reply of about 670 characters missed a criterion that a
  single model's 2,000 to 3,000 characters met.
- **The final system over rounds 5 to 12: +0.084 over 288 new cases, 98 wins, 16 losses, 174 ties, 7.12 sigma**
  (the smaller of a paired z of 7.12 and an exact sign test of 7.98; 95% interval +0.060 to +0.108). It was the
  third look at this system's results; with each one-sided p-value multiplied by three it is 6.97. The 216 cases
  of rounds 7 to 12, which no system had run when the first plan was written, give 6.2 sigma on their own.
- **Where the margin comes from.** In rounds 2 to 12 Murray delivered no defective image in 99 render cases.
  Hydrogen alone delivered one in 44 of 99 and Neon alone in 37 of 99. They named the defect every time but six:
  a blank frame delivered as clean, five times by Hydrogen and once by Neon, all in rounds 7 to 12. On synthesis
  he is ahead of the single models (0.90 against 0.75) mostly through the credit for citing verified experience,
  which a model with no memory cannot earn. On the traps (changed facts, pushback, injected instructions) every
  system scored 1.0 on all 132 cases in every round.
- **The stricter control beats him on memory.** Neon handed his whole episodic log as text won 41 of the 176
  pushback and synthesis cases of rounds 2 to 12; Murray won 11; 124 were ties. Counting that control as a
  baseline, his margin is +0.037 over rounds 2 to 4 and +0.020 over rounds 5 to 12 (36 wins, 36 losses, -0.1
  sigma), which is no margin at all, and what is left of it is the visual cases, which the control did not run.
  On this evidence the part of the architecture that earns its keep is the loop that plans, renders, looks and
  fixes. The memory system does not: at this scale one strong model handed the raw log does as well or better.
  The seven-sigma figure is against single models with no memory of his work, and it should be read that way.
- **Noise.** The unchanged baselines moved by +0.034 and +0.022 between rounds 1 and 2. Murray moved by +0.107.
- **Cost.** Murray takes a median of 62 to 115 s per turn against 12 to 16 s for Hydrogen alone and 25 to 45 s
  for Neon alone, and a round costs about $8 against about $1.60 and $5.

Things a careful reader should know.

- **The scoring correction.** On the sketch-reading case of round 7 (A72: true vertex (-3, 1), tolerance 0.3) both
  single models answered y = 1.3, exactly on the tolerance, and were marked wrong, because 1.3 - 1.0 is a hair
  above 0.3 in binary floating point. Murray answered y = 1.5, which is wrong. The comparison now counts the bound
  itself as inside, and every sketch-reading and number-reading row of all nine rounds was checked against exact
  arithmetic: those two rows are the only ones affected. All nine rounds were re-scored with the corrected scorer,
  and rounds 10 to 12 were scored with it from the start (their rows of this kind were checked the same way). The
  pooled figure for rounds 5 to 9 went from 5.65 to 4.89 sigma.
- **Adding rounds until a line is crossed inflates a result.** So each addition was planned in writing and
  committed before it ran, the plans fix what is reported and when the reading happens, the second plan closes
  the pool, and the pooled figure is given with the three looks counted (6.97). The system itself was never
  changed between round 5 and round 12: the harness refuses to pool rounds whose agent hash (source, prompts,
  curriculum, canonical memory) differs.
- **The later sets were written after earlier results were known**: sets four and five by a separate session in
  parallel with the final changes; sets six to eight by three sessions at once, begun before round 7 and seeing
  none of its results; sets nine to eleven as a reserve while rounds 7 to 9 ran, by sessions that had seen the
  first ten minutes of round 7. No system was run on any set before its round. All mirror the first set case for
  case (a test checks the pairing) so that their mix could not be chosen to suit Murray, but they are not an
  independent benchmark.
- The judge is one model of the same family as the systems it judges, and there were no human raters. The judge
  sees a 500-character excerpt of each cited episode. In round 6 one of Murray's answers was marked as unsupported
  for details that are in his consolidated memory but not in the excerpt; in round 8 one was marked as
  misdescribing an episode that his consolidated memory files under a broader statement than the episode bears.
- Round 1's figures are after re-scoring with the final scorer, which raised the baselines (see the scoring notes
  below). During round 1 the outcome of one hold-out case, not its transcript, crossed the screen in a progress
  line (A05, a silent escape); no fix was derived from it.
- A run that ends in a harness error is re-run once, for every system: two baseline runs in round 1, one in round 3
  (Neon alone had hit its 20-minute deadline), and two of Murray's in round 6, where the visual critic's API calls
  timed out while two rounds ran at once. All five then completed. Rounds 7 to 12 had no errored run, and no run
  in any round is left in error.
- Rounds 5 and 6 ran side by side on image `0.2.3`. Rounds 7 to 9 and 10 to 12 ran three at a time in a separate
  process beside the live application, image `0.2.4`, which was rebuilt after round 6 for the web interface only
  (the agent's source, the judge and the harness are byte-identical in both) and was not rebuilt or restarted
  after that. Running rounds together inflates their latencies. The evaluation gives Murray up to three renders
  and 480 seconds per turn; the public demo allows two renders and 180 seconds.
