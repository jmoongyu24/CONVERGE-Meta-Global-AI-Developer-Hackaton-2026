# Group meeting, June 18 2014

Date: 2014-06-18, Wednesday 10:00–11:20. Present: Prof. Ruiz, Sora, Amir, me (Daniel, taking notes).

Amir showed the first LSTM results. On questions whose answer is 100 tokens back, accuracy went from chance (vanilla RNN) to 61%. Beyond 200 tokens it still drops to 34%, but the curve is far flatter than the baseline's. Amir is convinced this is the right direction and wants to tune it further.

I proposed two refinements: initialize the forget-gate bias to 1 so the cell remembers by default early in training, and add peephole connections so the gates can see the cell state. Prof. Ruiz agreed to try both, one at a time.

Sora was less enthusiastic: training is 3.2x slower per epoch than the RNN, and the 200+ range is exactly what our task needs. Sora thinks we will hit a wall with any purely sequential model.

Prof. Ruiz's summary: LSTM is now the main line; the vanilla RNN stays only as a reference baseline.

Actions: Amir runs the forget-bias ablation first, then peepholes. Sora reads up on alternatives to sequential processing.
