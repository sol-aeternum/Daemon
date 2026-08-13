#!/usr/bin/env python3
"""Find 3 LongMemEval examples with evidence for diagnostic testing."""

import json
import os
import tempfile
from pathlib import Path

input_path = Path(tempfile.gettempdir()) / "longmemeval-review/data/longmemeval_s.json"
with input_path.open() as f:
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

# Use a securely created sibling and replace the stable hand-off path atomically.
# This avoids following a pre-planted symlink at the predictable output name.
output_path = Path(tempfile.gettempdir()) / "longmemeval_examples.json"
temporary_path: Path | None = None
try:
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=output_path.parent,
        prefix=f".{output_path.name}.",
        delete=False,
    ) as output_file:
        temporary_path = Path(output_file.name)
        json.dump(examples, output_file, indent=2)
    os.replace(temporary_path, output_path)
finally:
    if temporary_path is not None:
        temporary_path.unlink(missing_ok=True)
