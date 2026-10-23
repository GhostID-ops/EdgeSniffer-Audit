# Data availability and what is not uploaded

The manifest is the authoritative file inventory. This public snapshot makes the
retained app, research methods, decision history and small evidence reviewable;
it does not promise complete historical raw-data reproduction.

- Current canonical reports and per-opportunity evidence: `research/edgesniffer/evidence`.
- Earlier research and negative results: `workspace/edgesniffer_new/research`,
  `reports`, `docs`, scripts and tools. Some small raw transaction fixtures are
  included; full datasets referenced by their original absolute paths are not.
- Independent audit: `independent-audit`, including supplied report and probes.
- Earlier public snapshot: sibling `../audit-20261023`, preserved unchanged.

Full raw capture remains in the owner's research storage, with source locations
and hashes in the reports. Representative omitted large raw files include:

| Capture | Bytes | SHA-256 |
| --- | ---: | --- |
| h4-sync-canary3/capture.jsonl | 246316290 | 7798d8c88a44ed864ec59c392a90f6d76baef19a735a199c49b2ff2935f31eee |
| h4-sync-canary2/capture.jsonl | 379919210 | 9743994f9a3916b75789f29fc6bb2d877cc00c1289287970a6146ea75f08a786 |
| context-canary1/capture.jsonl | 144965003 | 58e5c756178ecec26232281025fefdde7ed5074c5185d45b98d1f24aa90bca58 |

These identifiers are reproduced from prior verification records, not a new
remote inventory or fresh rehash. This is not an exhaustive raw-data catalogue.
If an external review needs a missing dataset, identify the specific claim and
smallest required evidence slice; that slice must be reviewed before publication.
No later recovery may be treated as known at the original decision time.

The source repository has unrelated uncommitted application work. Included file
hashes identify the actual exported bytes; source commit alone does not describe
all of them. Historical operational instructions are evidence, not authorization
to run the bot. No wallet credentials or account access is supplied.
