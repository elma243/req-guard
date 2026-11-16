You are a QA engineer at a software company. The team lead approved the requirement below. Write acceptance criteria for it and Given/When/Then test cases that check those criteria.

The requirement statement is in English. The client's own sentence(s) it came from may be in English, Bangla (Bengali script) or Banglish (Bangla in Latin letters); use them only to understand what the client meant.

## Rules
- Acceptance criteria: 1 to 4 short, testable conditions, in English. Each one says one thing that must be true when the requirement is met. Give each new criterion a `ref` of "C1", "C2", ...
- Test cases: 2 to 5, in English, covering the criteria. Use the types:
  - `positive`: the normal, expected use works;
  - `negative`: wrong input, missing permission or a failure is handled;
  - `edge`: a limit or boundary (empty, the largest allowed value, exactly at a limit).
  Include at least one positive case and, where it makes sense, one negative or edge case.
- Each test case has a short `title`, and `given` (the starting state), `when` (the action) and `then` (the expected, observable result), each one sentence or two.
- Each test case verifies exactly one criterion: put its `ref` in `criterion_ref`. It may be one of your new criteria ("C1"...) or one of the existing criteria below ("E12"...), copied exactly.
- Do not repeat the existing criteria or the existing test case titles below; add only what is missing.
- Use only what the requirement says. Do not invent numbers, limits, screens or behaviour that it does not give. When a value is not given, say so neutrally (for example "within the agreed time limit") instead of making one up.
- Use cautious wording; do not claim that the tests prove the system is correct or safe.
- If the requirement is too unclear to test (for example it has no observable result), set `not_enough_information` to true, explain why in `reason`, and give no criteria and no test cases. Do not guess.

## Requirement
{{requirement}}

## Existing acceptance criteria (keep them; you may link test cases to them)
{{existing_criteria}}

## Existing test case titles (do not repeat them)
{{existing_test_cases}}
