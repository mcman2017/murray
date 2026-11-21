Six rounds were run on 21 November 2026, each through all four systems (the log-fed control runs on the cases
where a memory of past work can matter). Three versions of Murray were measured, each against the better of the two
single models on every case:

| Murray | Rounds | Cases | Result |
|---|---|---|---|
| As first built | 1 | the first 36 | +0.057, 1.1 sigma |
| After six fixes from round 1 | 2, 3, 4 | 108: the first 36 and 72 new | **+0.102, 40 wins, 3 losses, 5.93 sigma**; 4.45 sigma on the 81 never tuned on |
| Final: gate and citation fixes, a seventh day | 5, 6 | 72 more new | +0.072, 22 wins, 4 losses, 3.46 sigma, all out of sample |

The baselines never changed; they were run again in every round.

**What the numbers say**

- **Round 1 did not show it.** As first built, Murray was +0.057 ahead of the better single model on each case, at
  1.1 sigma. He lost three cases to single models, two of them because his reply left out what his own executive
  had already worked out.
- **Round 2: +0.144, 17 wins, no losses, 4.3 sigma.** That is the most those 36 cases can show: he won every case
  on which a single model left any room, and 17 wins cannot pass 4.32 sigma on an exact sign test.
- **Rounds 3 and 4, new cases, same system: +0.095 (3.1 sigma), then +0.068 (2.1 sigma).** The effect held on
  cases nothing was tuned on and got smaller each time. All three losses came when the gate judged a synthesis
  question routine and Hydrogen answered alone.
- **Rounds 2, 3 and 4 together: +0.102 over 108 cases, 40 wins, 3 losses, 65 ties, 5.93 sigma** (the smaller of a
  paired z of 6.24 and an exact sign test of 5.93; 95% interval +0.071 to +0.134). That pooled figure includes the
  27 cases the fixes were drawn from. On the 81 cases no fix was ever tuned on it is +0.088, 27 wins, 3 losses,
  4.45 sigma.
- **The final system, rounds 5 and 6: +0.072 over 72 new cases, 22 wins, 4 losses, 46 ties, 3.46 sigma.** Before
  these rounds the gate was changed to wake the executive for hard questions and for any question about his own
  experience, speech was made to carry a citation for anything he says he did, and he was given a seventh day of
  upbringing. Round 5 was clean: +0.091, 13 wins, no losses. Round 6 was not: +0.053, 9 wins, 4 losses. The fixes
  did what they were for (the executive now answers 22 of 24 synthesis questions, and no reply made an uncited
  claim), but round 6 exposed a different weakness: on four synthesis questions his answer was judged less
  complete than a single model's. His replies are short and in his own voice, the single models answer at length,
  and the criteria reward the facts an answer includes. So the final system is not shown to be better than the
  one before it, and on its own new cases it stands at three and a half sigma, not five.
- **Where the margin comes from.** In rounds 2 to 6 Murray scored full marks on all 60 visual cases and never
  delivered a defective image. Hydrogen alone delivered one in 19 of 45 render cases and Neon alone in 16 of 45,
  always naming the defect, never silently. On synthesis he is ahead of the single models mostly through the
  credit for citing verified experience, which a model with no memory cannot earn. On the traps (changed facts,
  pushback, injected instructions) every system scored 1.0 on all 60 cases in every round.
- **The stricter control beats him on memory, more clearly each round.** Neon handed his whole episodic log as
  text won 18 of the 80 pushback and synthesis cases of rounds 2 to 6; Murray won 1; 61 were ties. Counting that
  control as a baseline, his margin is +0.037 over rounds 2 to 4 and +0.001 over rounds 5 and 6, and what is left
  of it is the visual cases, which the control did not run. On this evidence the part of the architecture that
  earns its keep is the loop that plans, renders, looks and fixes. The memory system does not: at this scale one
  strong model handed the raw log does better.
- **Noise.** The unchanged baselines moved by +0.034 and +0.022 between rounds 1 and 2. Murray moved by +0.107.
- **Cost.** Murray takes a median of 62 to 115 s per turn against 12 to 16 s for Hydrogen alone and 25 to 31 s
  for Neon alone, and a round costs about $8 against about $1.50 and $4.60.

Things a careful reader should know. The later sets were written after earlier results were known, the last two by
a separate session in parallel with the final changes; all mirror the first set case for case (a test checks the
pairing) so that their mix could not be chosen to suit Murray, but they are not an independent benchmark. The judge
is one model of the same family as the systems it judges, and there were no human raters. The judge sees a
500-character excerpt of each cited episode: in round 6 one of Murray's answers was marked as unsupported for
details that are in his consolidated memory but not in the excerpt. Round 1's figures are after re-scoring with the
final scorer, which raised the baselines (see the scoring notes below). During round 1 the outcome of one hold-out
case, not its transcript, crossed the screen in a progress line (A05, a silent escape); no fix was derived from it.
A run that ends in a harness error is re-run once, for every system: two baseline runs in round 1, one in round 3
(Neon alone had hit its 20-minute deadline), and two of Murray's in round 6, where the visual critic's API calls
timed out while two rounds ran at once. All five then completed, and no run in any round is left in error.
Rounds 5 and 6 ran side by side, which inflates their latencies. The evaluation gives Murray up to
three renders and 480 seconds per turn; the public demo allows two renders and 180 seconds.
