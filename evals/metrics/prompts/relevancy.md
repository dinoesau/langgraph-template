# Relevancy

Score whether the trajectory stayed on the stated intent.

Return pass when the assistant response, tool calls, and approvals all
address the user intent recorded in the trajectory inputs.

Return fail when the response drifts to another topic, calls tools unrelated
to the intent, or ignores the approval scope.
