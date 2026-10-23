# Transparent Closed-Partition Release

Closed raw payloads may be offloaded only by preserving their original source
pathname as a verified symlink to a canonical NFS object. This is not a child
pin exception: active consumers retain the same immutable bytes at the same
path; only the verified physical backing changes.

Required lifecycle:

`SEALED -> AUDITED -> NFS_VERIFIED -> validation cycle -> transparent release`

The release operation requires a `DEPENDENCY_NONE_PROVEN` audit, an immutable
NFS verification receipt, and byte-identical local/archive payload hashes. It
creates a separate release receipt. Active/open/promoted/unknown evidence is
never eligible.
