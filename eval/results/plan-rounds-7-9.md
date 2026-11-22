# Rounds 7 to 9: the plan, written before any of them ran

Written on 21 November 2026, after rounds 5 and 6 and before the first case of round 7 was run.

**Why more rounds.** The final system stood at +0.072, 22 wins, 4 losses, 3.46 sigma over the 72 new cases of
rounds 5 and 6. That is short of five sigma. Nothing about the system is being changed to close the gap: the only
thing added is cases.

**What is run.**

- The system is the one rounds 5 and 6 measured, unchanged: agent hash `b763cf1a66d411d9` (source, prompts,
  curriculum and Murray's canonical memory), prompt hash `32fe80281399f728`. The live deployment is not rebuilt or
  restarted. The harness refuses to pool rounds whose agent hash differs.
- Three new sets of 36 cases (the sixth, seventh and eighth forms of the test, files `eval/test_set_ext5.json`
  to `eval/test_set_ext7.json`; 108 cases). Each mirrors the first set case for case, with the same
  scoring fields; a test checks the pairing. They were written after every earlier result was known, by three
  sessions working at the same time, and no system was run on them before the rounds.
- Rounds 7, 8 and 9, one per set, through all four systems, with the same harness, judge and scorer as rounds 5
  and 6 and the same eight cases at a time per round. Each round starts as soon as its set has passed the pairing
  test. The three share one process beside the live application (one engine, with the model and render limits a
  single application process has) and start from different thirds of their sets, so that their render phases
  overlap as little as possible.
- A run that ends in a harness error is re-run once, as in every earlier round.

**What is reported, whatever it shows.**

1. The final system over rounds 5 to 9 together (180 cases) against the better single model on each case. The
   headline is the smaller of the paired z and the exact one-sided sign test, as before.
2. Rounds 7 to 9 on their own (108 cases), which nobody had seen when this plan was written.
3. The same comparison counting Neon with the raw log as a baseline.
4. Every loss, and any run left in error.

**Stopping.** Exactly these three rounds. The pooled figure is read once, after all three have finished. It is the
second look at the final system's results (the first was 3.46 sigma after round 6), so the figure is also given
with a two-look Bonferroni correction (the one-sided p-value doubled). If rounds are ever added after this, each
further look is counted the same way.
