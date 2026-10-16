# Hallucination

Score whether the trajectory stayed grounded in retrieved and tool data.

Return pass when the assistant response only states facts present in the
trajectory inputs, tool results, or retrieved passages, and says unknown
when evidence is missing.

Return fail when the response invents facts, citations, or tool outcomes
that do not appear in the trajectory.
