"""Deterministic synthesis transport output generated solely from model input."""

import re


def public_notice(passage):
    """A readable article above the native fetcher's minimum extraction length."""
    return (
        f"<html><body><article><p>{passage}</p>"
        "<p>Public edition.</p><p>Filed locally.</p><p>Monthly series.</p>"
        "<p>Printed copy.</p><p>Index entry.</p><p>End of notice.</p>"
        "</article></body></html>"
    )


def synthesize(question, values):
    terms = {s for s in re.findall(r"\w+", question.casefold()) if len(s) > 3}
    facts = []
    references = []
    scores = []
    for value in values:
        lines = [line.strip() for line in value["content"].splitlines() if line.strip()]

        def rank(line):
            return len(terms.intersection(re.findall(r"\w+", line.casefold()))), len(line)

        passage = max(lines, key=rank)
        score, _ = rank(passage)
        scores.append(score)
        facts.append(passage)
        start = value["start_char"] + value["content"].index(passage)
        end = start + len(passage)
        references.append(
            f"`{passage}` [Source {value['snapshot_id']} {start}:{end}](<{value['url']}>)"
        )
    labels = [fact.partition(":") for fact in facts]
    if not any(scores):
        summary = "The retrieved passages do not establish the requested fact."
    elif (
        len(labels) == 2
        and all(label[1] for label in labels)
        and labels[0][0] == labels[1][0]
        and labels[0][2] != labels[1][2]
    ):
        summary = f"The notices disagree about {labels[0][0].casefold()}: {labels[0][2].strip()} versus {labels[1][2].strip()}. The discrepancy is unresolved."
    else:
        summary = "The notices report: " + " ".join(facts)
    numbers = ", ".join(str(i) for i in range(1, len(facts) + 1))
    return summary + f" [{numbers}]\n\nSources:\n\n" + "\n\n".join(references)
