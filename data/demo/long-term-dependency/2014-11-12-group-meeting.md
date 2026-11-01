# Group meeting — GRU vs LSTM

2014-11-12 Wed 10:00 · R, D, S, A · notes: S

- D: proposal — switch the encoder to the GRU from Cho et al. 2014: two gates instead of three, no separate cell state. Should be faster with similar memory behaviour.
- A: ran GRU vs LSTM on the QA task (3 seeds each): same accuracy within noise at 100+ tokens; GRU ~20% faster per epoch.
- R: that matches what other groups are reporting. Supportive of switching.
- S: fine with GRU for the encoder. But both are still sequential, and on 2,000-token documents the speedup is a drop in the bucket.
- S: attention prototype on translation: works; long sentences improve the most.
- D: good, then GRU encoder + attention decoder is the natural combination for translation.
- all: gated units are clearly better than the vanilla RNN on every distance bucket — no one wants to go back.

TODO
- A: GRU as the default encoder in the QA code
- S: attention prototype on QA (not just translation)
