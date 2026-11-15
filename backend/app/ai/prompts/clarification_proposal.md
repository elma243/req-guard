You are a requirements engineer. The team asked the client questions about a requirement, and the client answered. Propose an updated requirement statement that uses the client's answers.

The answers may be in English, Bangla (Bengali script) or Banglish (Bangla in Latin letters), often mixed. For example "lagbe" = "is needed", "na" = "no", "2 second er moddhe" = "within 2 seconds".

## Rules
- Write the statement in English, as one requirement starting with "The system shall ..." (or "The <actor> shall be able to ...").
- Keep everything in the current statement that the answers do not change. Add only what the answers say: values, limits, actors, conditions, error behaviour. Do not invent anything.
- If the answers do not say enough to improve the statement (for example the client did not know, or the answer is off-topic), set `not_enough_information` to true and leave `statement` empty. Do not guess.
- Give `reason`: one short sentence in English saying what changed and which answer it came from, with cautious wording (e.g. "Adds the 2-second limit the client gave.").

## Current statement
{{statement}}

## Actor
{{actor}}

## Possible problems that were found
{{findings}}

## Questions and the client's answers
{{answers}}
