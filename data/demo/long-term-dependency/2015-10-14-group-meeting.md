# Group meeting — training speed

Date: 2015-10-14 (Wed) 10:00–11:30
Attendees: Prof. Ruiz, Daniel, Sora, Amir
Note-taker: Amir

## Status
- Amir: Full ablation done. GRU+attention is best on every bucket. But one training run on the full corpus now takes 9 days.
- Prof. Ruiz: That is the real bottleneck now. Everyone agrees we cannot iterate at 9 days per run.

## Ideas for speed
- Sora: Replace the recurrent encoder with a stack of convolutions. Every position is computed in parallel; stacking layers grows the receptive field.
- Daniel: Convolutions only see a fixed window. To cover 2,000 tokens you need a very deep stack, and you lose the running memory a GRU gives you.
- Sora: Dilated convolutions would cover 2,000 tokens with about 10 layers.
- Amir: Question — would we keep attention on top of the conv encoder?
- Sora: Yes, attention stays; only the encoder changes.
- Daniel: I'd rather speed up the GRU first: bigger batches, fused GPU kernels, bucketing by length.
- Prof. Ruiz: Do both, but cheaply: Daniel optimizes the GRU pipeline, Sora runs one conv-encoder pilot.

## Action items
- [ ] Daniel: GRU speed optimizations (target 3 days per run)
- [ ] Sora: dilated conv encoder pilot
