# Domain model

`domain-model.svg` is the source; `domain-model.png` is the rendered copy;
`build_domain_model.py` draws both.

The model answers four questions:

1. **What are the things?** Market data, intelligence, books, risk, recording,
   forensics, control — seven contexts, each with its aggregates.
2. **What travels between them?** Exactly one thing: an envelope, on one bus.
3. **What may change behaviour?** Only the control plane, and only through the
   kill switch and the interceptor chain — never by a side channel.
4. **What is the record?** The lake, written by the recorder alone, read by the
   explorer alone, and never reconstructed after the fact.

Bold names are aggregates: the units that must stay consistent. Dashed arrows are
control, not data.

Regenerate with `python3 docs/domain/build_domain_model.py`.
