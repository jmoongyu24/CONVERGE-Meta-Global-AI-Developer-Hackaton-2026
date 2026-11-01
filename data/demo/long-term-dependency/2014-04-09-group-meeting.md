# Group meeting — LSTM design

2014-04-09 Wed 10:00 · R, D, S, A · notes: S

- D: presented the LSTM setup — input, output and forget gates; 512 cells; same truncated BPTT as the baseline.
- D: the cell state acts as a constant error carousel, so gradients survive hundreds of steps.
- R: supportive; asked for a direct comparison against the RNN baseline by answer distance, not just perplexity.
- S: question — how much slower per epoch? If it is 4x, we lose a month on the full corpus.
- D: estimate ~3x slower per step, partly offset by needing fewer epochs.
- A: can implement it on top of the existing Theano code within two weeks.
- S: my view: fine as a stepping stone, but I doubt recurrence alone is the long-term answer for 200+ token dependencies.

TODO
- A: LSTM implementation + accuracy-by-distance plot
- D: initialization recipe for the gates
