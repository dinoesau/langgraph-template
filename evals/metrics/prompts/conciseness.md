# Conciseness

Score whether the trajectory response length fit the task.

Return pass when the assistant response is direct, avoids repeated tool calls
for the same arguments, and stops once the intent is satisfied.

Return fail when the response repeats content, makes redundant tool calls,
or adds detail unrelated to the stated intent.
