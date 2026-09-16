# Invalid Second Implementation Run

Classification: `IMPLEMENTATION_ABORTED_BEFORE_RESULT`

The corrected arrival-join run was stopped before publishing any economic rows
because the adapter retained every target-tagged finalized transaction payload
until the end of the 43.9GB source pass. This was unnecessary and created avoidable
memory pressure. Only the run identity and frozen contract had been written.

The replacement uses the validated streaming pattern: preserve Pump arrivals,
then decode each joined finalized transaction immediately. No economic result
was produced or inspected, and no contract, policy, population, or assumption
changed.
