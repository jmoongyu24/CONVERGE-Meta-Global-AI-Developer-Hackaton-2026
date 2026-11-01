# Group meeting, March 11 2015 — main system decision

Date: 2015-03-11, 10:00–12:00. Present: Prof. Ruiz, Sora, Amir, me (Daniel, notes).

Sora presented attention on the QA task. With a GRU encoder and soft attention over all encoder states, accuracy on 200+ token questions went from 38% to 57%. Everyone agreed attention is a real gain on long-range questions, me included.

Prof. Ruiz decided that our main system is now a GRU encoder with soft attention, and asked me to lead it since the encoder is the core of the pipeline. Sora owns the attention module. LSTM is parked; we keep the code but stop running it.

Sora objected to the framing: the 19-point gain comes from attention, not from the encoder, so calling attention a module undersells it. Prof. Ruiz said the leadership question stays open if attention keeps carrying the results.

Amir backed my plan to stabilize the GRU+attention system first before trying anything more radical, and will run the full ablation grid.

Actions: Amir — ablation grid (encoder type × attention on/off). Me — training recipe for the joint system. Sora — attention visualizations for the paper.
