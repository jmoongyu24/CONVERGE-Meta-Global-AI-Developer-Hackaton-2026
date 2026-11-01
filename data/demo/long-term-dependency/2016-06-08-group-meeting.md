# Group meeting — self-attention encoder

2016-06-08 Wed 10:00 · R, D, S, A · notes: S

- S: conv pilot is done: 2.5x faster than GRU, but 4 points worse on 200+ token questions. Dropping the conv encoder.
- S: new proposal — self-attention inside the encoder: every position attends to every other position, like the intra-attention in Cheng et al. 2016. Add a position signal so order is not lost.
- D: order is exactly what recurrence gives you for free. Bolting position signals onto attention feels like a hack.
- R: concern — memory grows with the square of the length. 2,000 tokens means 4M attention scores per layer per head.
- A: ran S's prototype on short documents (≤ 500 tokens): trains 3x faster than GRU+attention, accuracy 2 points lower.
- D: GRU speedups landed: one full run is now 3.5 days instead of 9.
- all: agree the sequential bottleneck is now the main limit on how fast we can do research.
- R: keep GRU+attention as the main system; S may continue self-attention as a side project.

TODO
- A: self-attention prototype on 1,000-token documents
- S: chunked attention to cut memory
