Pick one topic for every slot in this batch.

What you are given:

- `choices`: for each slot, the topic ids it may use. Code has already removed topics used
  earlier this week and topics from recent runs. If that would leave a slot with nothing, its
  whole pool is listed instead; code will replace your pick for that slot, so spend no effort on it.
- `catalog`: what each offered id is: its title, category, severity and a one-sentence summary.
  An angle id (ANGLE-...) has a title only.
- `taken`: topics already fixed for this week's focused slots, as `slot id: "id title"`. It can
  be empty.

How to choose:

- For each slot, choose exactly one id from that slot's own `choices`. Do not invent ids.
- Keep choices distinct: give different slots different topics.
- Spread the batch across categories. Give two slots the same category only when nothing else
  fits.
- Avoid overlap with `taken`: do not pick a topic that largely repeats one of them or is a
  sub-case of it.
- Pick what makes a strong, specific post for that slot's type, not simply the first id.
- In `reasons`, give each slot a one-line reason (under 200 characters) saying why this topic
  suits this slot and this week.
- Return slot_id _batch and attempt 1.

Code checks every id you return. An id outside the slot's choices, or already given to another
slot, is discarded and code picks that slot's topic instead. The choice is a topic only: code
attaches the evidence to it afterwards.
