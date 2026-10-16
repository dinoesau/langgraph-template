# Helpfulness

Score whether the trajectory helped the user reach the goal.

Return pass when the assistant response moves the stated intent forward,
uses tool results when tools were called, and respects any approval outcome.

Return fail when the response ignores the user intent, ignores tool results,
or claims an action that was not approved.
