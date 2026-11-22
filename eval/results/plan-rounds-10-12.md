# Rounds 10 to 12: the plan, written before any of them ran

Written on 21 November 2026, after rounds 7 to 9 had been read and before the first case of round 10 was run.

**What rounds 7 to 9 showed.** As first scored, the final system over rounds 5 to 9 stood at +0.084 over 180 cases,
61 wins and 9 losses: 5.65 sigma. An audit of the visual cases then found a scoring error in Murray's favour. On
one sketch-reading case (A72: true vertex (-3, 1), tolerance 0.3) both single models answered y = 1.3, exactly on
the tolerance, and were marked wrong, because 1.3 - 1.0 is a hair above 0.3 in binary floating point. Murray
answered y = 1.5, which is wrong. With the comparison corrected the case is a loss for Murray instead of a tie, and
the pooled figure is **+0.078, 61 wins, 10 losses: 4.89 sigma**. Every sketch-reading and number-reading row of all
nine rounds was checked against exact arithmetic: those two rows are the only ones the error touched. All nine
rounds were re-scored with the corrected scorer, and a test now covers an answer that sits exactly on a tolerance.

**Why more rounds.** 4.89 is short of five. The first plan said rounds 7 to 9 would be read once and that any later
look would be counted. This is that later look. Nothing about the system is changed: the only thing added is cases.

**What is run.**

- The same system: agent hash `b763cf1a66d411d9`, prompt hash `32fe80281399f728`. The live deployment is not
  rebuilt or restarted.
- Three further sets of 36 cases (`eval/test_set_ext8.json` to `eval/test_set_ext10.json`, 108 cases), mirrored
  case for case like the others. They were written as a reserve while rounds 7 to 9 were running, by three sessions
  that had seen the first ten minutes of round 7 (an interim tally and the transcript of case A70) and nothing
  later. No system has been run on them.
- Rounds 10, 11 and 12, one per set, through all four systems, with the same harness and judge, the corrected
  scorer, and eight cases at a time per round. The three run side by side in one process beside the live
  application and start from different thirds of their sets.
- A run that ends in a harness error is re-run once, as in every earlier round.

**What is reported, whatever it shows.**

1. The final system over rounds 5 to 12 together (288 cases) against the better single model on each case: the
   smaller of the paired z and the exact one-sided sign test.
2. Rounds 10 to 12 on their own (108 cases).
3. The same comparison counting Neon with the raw log as a baseline.
4. Every loss, any run left in error, and any further scoring correction, whichever way it goes.

**Stopping.** Exactly these three rounds, read once after all three have finished. It is the third look at this
system's results (3.46 sigma after round 6, 4.89 after round 9), so the figure is also given with each one-sided
p-value multiplied by three. This is the last block: no further rounds are added to this pool whatever it shows.
