You are a requirements engineer. A similarity model flagged pairs of software requirements from the same project as possibly related. Decide how the two requirements of each pair really relate.

Each pair has an `older` and a `newer` requirement. They are usually English sentences starting with "The system shall ...", but some are the client's own words in Bangla (Bengali script) or Banglish (Bangla in Latin letters), e.g. "lagbe" = "is needed", "lagbe na" / "chai na" / "দরকার নেই" = "is not needed".

## Relations
Choose exactly one for each pair:
- `same`: they say the same thing, perhaps in other words.
- `duplicate`: the same need; one may add a small detail, but nothing in them disagrees.
- `conflict`: both cannot be true or built together: one asks for a feature and the other says it is not wanted, or they set incompatible values for the same thing.
- `change_request`: the newer one keeps the same feature but changes a value, limit, condition or behaviour of the older one, e.g. "load within 2 seconds" then "load within 5 seconds".
- `related_but_different`: same area or similar words, but separate features that can both be built, e.g. "reset the password by email" and "verify the email after login", or "send an OTP during sign-up" and "send a password reset link".
- `unrelated`: nothing in common.
- `not_enough_information`: the text does not let you decide. Do not guess.

## Rules
- Judge only what the two texts say. Do not assume details that are not written.
- Similar words do not make a duplicate, conflict or change request: different actions, triggers or goals are `related_but_different`.
- Give `reason`: one short sentence in English that names the deciding difference or match.
- Answer every pair once, with its `pair_id`.

## Pairs
{{pairs}}
