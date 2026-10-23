# Evidence Lake V1 Policy

This policy separates physical evidence retention from experiment membership.

1. Raw capture belongs to a source plane and is immutable once sealed.
2. Experiments reference source evidence; they never own or duplicate it.
3. `OPEN`, `PIN_ACTIVE`, promoted, unresolved, and unknown paths are protected.
4. A sealed raw partition may be released locally only after complete archive
   readback verification, an immutable receipt, and an active-pin check.
5. A raw archive object is never garbage-collected. Only a verified local
   redundant copy may be released.
6. Derived outputs are removable only when reproducible, unpinned, and
   explicitly classified by a snapshot/receipt.
7. The SQLite catalog lives on local storage; hash-stamped JSON snapshots live
   on NFS so NFS locking cannot become a catalog integrity dependency.
8. Unknown data is protected by default.

The V1 catalog and release gate are intentionally outside live collector,
finalizer, control, and shadow paths. They must not trigger restarts or change
scientific cohort membership.
