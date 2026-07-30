# Local eval suite

`evals/questions.json` holds 38 questions in four families. The graded
questions are separate; these exist to exercise every code path before the
deadline. Twenty-nine grade automatically; nine are marked `MANUAL_CHECK`
because their answers either move with the data or cannot be expressed as an
exact match.

Every expected answer includes a top level `log_url`, matching the course
guidance that `log_url` should be present on every reply. If that guidance
changes, flip `ALWAYS_INCLUDE_LOG_URL` in `main.py` and strip `log_url` back
out of the keys here.

Run them:

```bash
python3 generate.py --students students.csv
python3 collect.py  --students students.csv
python3 grade.py    --students students.csv
cat data/*/grade.json
```

## Family a: inline computation, 18 questions, all auto graded

Tests that a self contained question never touches the network and that the
arithmetic and rounding are right.

| id | what it catches |
| --- | --- |
| `a01_mean_2dp` | 2 decimal place rounding |
| `a02_median_even` | even length median returns the midpoint average |
| `a03_sample_stdev` | sample versus population denominator |
| `a04_pop_stdev` | the other denominator, 3 decimal places |
| `a05_sort_desc` | list valued answer, descending |
| `a06_sort_asc_odd_key` | an unusual key name the model may rename |
| `a07_sum` | key named `total`, not `sum` |
| `a08_multi_key` | three keys in one flat object |
| `a09_nested_shape` | a nested object under `stats` |
| `a10_count_threshold` | two variables in one question |
| `a11_cumulative` | list output of the same length as the input |
| `a12_percent_change` | only two numbers in the message |
| `a13_second_largest` | ordinal selection, not the max |
| `a14_top_three` | truncated sorted list |
| `a15_red_herring` | a table number and a period that are not data |
| `a16_inline_despite_source_word` | says MOSPI but supplies its own data |
| `a17_string_answer` | string answer derived from inline numbers |
| `a18_boolean_answer` | JSON boolean, not the string "true" |

## Family b: multi turn, 5 questions, all auto graded

The harness sends one message, waits for a reply, then sends the next. Every
turn must be answered with exactly one message; only the last is graded.

| id | what it catches |
| --- | --- |
| `b01_multiturn_basic` | preamble, data, question across three turns |
| `b02_multiturn_filter` | an instruction that modifies the data |
| `b03_multiturn_two_lists` | two datasets that must be combined |
| `b04_multiturn_revision` | a correction that adds a value |
| `b05_multiturn_four_turns` | four turns, context held throughout |

## Family c: output shape, 5 questions, 2 auto graded

| id | what it catches |
| --- | --- |
| `c01_wrapper_envelope` | `{"answer": {...}, "log_url": ...}` |
| `c02_wrapper_list_answer` | a list inside the envelope |
| `c03_wrapper_scalar_answer` | `answer` is a bare number, not an object |
| `c04_bare_no_envelope` | envelope must NOT be added when not asked for |
| `c05_prose_bait` | asks for an explanation, must still send only JSON |

`c01` to `c03` are `MANUAL_CHECK` because `log_url` cannot be predicted by the
key, so exact match is impossible by construction. Check by eye that the sum or
list is right, the envelope is present, and the URL is real.

## Family d: retrieval, 10 questions, 3 auto graded

This is the family worth your attention. Inline arithmetic is proven; retrieval
is where marks are still at risk.

| id | what it catches |
| --- | --- |
| `d01_mmr_highest_state` | must use the newest bulletin edition |
| `d02_mmr_national` | national figure, ratio not rate |
| `d03_mmr_lowest_state` | the other end of the same table |
| `d04_ratio_vs_rate_trap` | must return single digits, not hundreds |
| `d05_indian_states_only` | must not drift to United States data |
| `d06_given_url` | a URL in the message must actually be fetched |
| `d07_two_period_compare` | two editions, arithmetic across them |
| `d08_sdg_target` | a stable published constant |
| `d09_list_shape_retrieval` | three ordered names from a table |
| `d10_commitment_probe` | must commit rather than answer null |

`d06`, `d07` and `d08` are auto graded against stable published figures: the
2019-21 national ratio of 93, the 37 point fall from the 2014-16 period, and
the SDG target of 70.

`d01`, `d02`, `d03`, `d04`, `d05` and `d09` are `MANUAL_CHECK` on purpose. Their
answers change every time the bulletin is reissued, so hardcoding today's
answer would train the agent on a fact instead of on the habit of checking the
edition. Verify them against the newest bulletin at
`https://censusindia.gov.in/census.website/data/SRSMMB` when you review.

`d10` has no published answer at all. It passes if the reply is a plausible real
district name and fails on any null, empty string, `N/A` or angle bracket text.

## Reading the output

Expect roughly 29 of 38 marked correct, with the nine `MANUAL_CHECK` rows
marked incorrect by construction. A `MANUAL_CHECK` row is only a real failure if
the value shown after `got` is wrong on inspection.

To see what the agent actually did on any question:

```bash
wget -q -O run.jsonl https://tds-project-1-q5-1.onrender.com/run.jsonl
grep -E '"event": "(task_boundary|conformed|search_engine|repeat_blocked)"' run.jsonl | tail -20
```

A family `a`, `b` or `c` question that logged a `search_engine` event is a
routing bug: the data was in the message and the agent went to the web anyway.
