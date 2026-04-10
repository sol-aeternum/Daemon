#!/usr/bin/env python3
"""Find 3 LongMemEval examples with evidence for diagnostic testing."""

import json

with open("/tmp/longmemeval-review/data/longmemeval_s.json", "r") as f:
    data = json.load(f)

# Find 3 examples with haystack sessions and clear answers
examples = []
for item in data:
    question = item.get("question", "")
    answer = item.get("answer", "")
    haystack_sessions = item.get("haystack_session_ids", [])

    # Skip abstention/cannot answer
    if not answer or "cannot" in str(answer).lower():
        continue

    # Need haystack sessions
    if haystack_sessions:
        examples.append(
            {
                "id": item.get("question_id"),
                "question": question,
                "answer": answer,
                "haystack_sessions": haystack_sessions,
                "answer_session": item.get("answer_session_ids", []),
            }
        )

    if len(examples) >= 3:
        break

print(f"Found {len(examples)} examples")
for i, ex in enumerate(examples, 1):
    print(f"\n=== Example {i} ===")
    print(f"ID: {ex['id']}")
    print(f"Question: {ex['question']}")
    print(f"Answer: {ex['answer']}")
    print(f"Haystack Sessions: {ex['haystack_sessions']}")
    print(f"Answer Sessions: {ex['answer_session']}")
    print("---")

# Save for later use
with open("/tmp/longmemeval_examples.json", "w") as f:
    json.dump(examples, f, indent=2)
