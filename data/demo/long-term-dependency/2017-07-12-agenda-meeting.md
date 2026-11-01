# Agenda meeting — next two years

Date: 2017-07-12 (Wed) 10:00–12:00
Attendees: Prof. Ruiz, Daniel, Sora, Amir
Note-taker: Amir

## Numbers
- Amir: Transformer up to 2,000 tokens: best accuracy of anything we have. At 4,000 tokens it runs out of memory on our 12 GB cards unless we cut the batch to 1.
- Daniel: Recurrent-memory prototype handles 4,000 tokens and the streaming transcripts at constant memory, but it is 4 points below the Transformer at 2,000 tokens.

## Discussion
- Sora: The field is moving to attention-only models. If we build our agenda on recurrence we will be behind within a year.
- Daniel: Memory cost is not a detail. Our documents are getting longer, not shorter, and streaming input never ends.
- Amir: I side with Sora — the accuracy gap is large and the memory problem looks like an engineering issue we can work around.
- Prof. Ruiz: Both of you have a case: attention for accuracy, recurrence for unbounded length. We can only fund one main line with our GPU budget.
- Prof. Ruiz: Open question — is there a version where attention handles accuracy and something recurrent handles length, or is that just postponing the decision?
- (Agreed by all: whatever we choose must be evaluated on 4,000+ token documents, not just short benchmarks.)

## Decisions
- Lab agenda and who leads it to be decided at the next meeting (07/26).

## Action items
- [ ] Sora: one-page case for an attention-only agenda
- [ ] Daniel: one-page case for a recurrent-memory agenda
- [ ] Amir: shared benchmark of 4,000+ token documents
