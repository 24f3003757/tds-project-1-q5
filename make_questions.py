#!/usr/bin/env python3
"""Emit evals/questions.json: the 38 question local eval suite in EVALS.md.

Written as a generator rather than by hand so the log_url can be changed in
one place. If you switch log_url (for example by turning on GitHub log
publishing, which moves it to raw.githubusercontent), edit LOG below and
re-run this, or every expected answer will mismatch on the URL alone.

    python3 make_questions.py --log-url https://.../run.jsonl > evals/questions.json
"""
import argparse
import json

TAIL = "Reply with ONLY this JSON object and nothing else: "


def build(LOG):
    Q = []

    def add(qid, messages, expected=None, expected_code=None, randomize=None,
            timeout=300):
        q = {"id": qid, "timeout_seconds": timeout}
        if randomize:
            q["randomize"] = randomize
        q["messages"] = messages if isinstance(messages, list) else [messages]
        if expected_code is not None:
            q["expected_code"] = expected_code
        else:
            q["expected"] = expected
        Q.append(q)

    def code(body):
        """Wrap an answer body so every key gets the log_url beside it."""
        return '{%s, "log_url": "%s"}' % (body, LOG)

    R6 = {"inputs": "rng.sample(range(100, 900), 6)"}

    # ---------------------------------------------- family a: inline compute
    add("a01_mean_2dp", randomize=R6,
        messages=f"Here are six readings: $inputs. Return their arithmetic mean "
                 f"rounded to 2 decimal places. {TAIL}" '{"mean": <number>}',
        expected_code=code('"mean": round(statistics.mean(inputs), 2)'))

    add("a02_median_even", randomize={"inputs": "rng.sample(range(50, 500), 6)"},
        messages=f"Given the six values $inputs, return their median rounded to "
                 f"2 decimal places. {TAIL}" '{"median": <number>}',
        expected_code=code('"median": round(statistics.median(inputs), 2)'))

    add("a03_sample_stdev", randomize=R6,
        messages=f"For the sample $inputs, return the SAMPLE standard deviation "
                 f"rounded to 3 decimal places. {TAIL}" '{"stdev": <number>}',
        expected_code=code('"stdev": round(statistics.stdev(inputs), 3)'))

    add("a04_pop_stdev", randomize=R6,
        messages=f"Treat $inputs as an entire POPULATION. Return the population "
                 f"standard deviation rounded to 3 decimal places. {TAIL}"
                 '{"stdev": <number>}',
        expected_code=code('"stdev": round(statistics.pstdev(inputs), 3)'))

    add("a05_sort_desc", randomize=R6,
        messages=f"Given $inputs, return the values sorted descending. {TAIL}"
                 '{"sorted": [<numbers>]}',
        expected_code=code('"sorted": sorted(inputs, reverse=True)'))

    add("a06_sort_asc_odd_key", randomize=R6,
        messages=f"Given $inputs, return the values sorted ascending. {TAIL}"
                 '{"ascending_values": [<numbers>]}',
        expected_code=code('"ascending_values": sorted(inputs)'))

    add("a07_sum", randomize=R6,
        messages=f"Add up $inputs. {TAIL}" '{"total": <number>}',
        expected_code=code('"total": sum(inputs)'))

    add("a08_multi_key", randomize=R6,
        messages=f"For $inputs report the smallest value, the largest value and "
                 f"how many values there are. {TAIL}"
                 '{"min": <number>, "max": <number>, "count": <number>}',
        expected_code=code('"min": min(inputs), "max": max(inputs), '
                           '"count": len(inputs)'))

    add("a09_nested_shape", randomize=R6,
        messages=f"For $inputs report summary statistics, where spread means the "
                 f"largest value minus the smallest. Round the mean to 2 decimal "
                 f"places. {TAIL}"
                 '{"stats": {"mean": <number>, "spread": <number>}}',
        expected_code=code('"stats": {"mean": round(statistics.mean(inputs), 2), '
                           '"spread": max(inputs) - min(inputs)}'))

    add("a10_count_threshold",
        randomize={"inputs": "rng.sample(range(100, 900), 8)",
                   "threshold": "rng.randrange(300, 700)"},
        messages=f"How many of the values in $inputs are strictly greater than "
                 f"$threshold? {TAIL}" '{"count": <number>}',
        expected_code=code('"count": len([x for x in inputs if x > threshold])'))

    add("a11_cumulative", randomize={"inputs": "rng.sample(range(10, 200), 5)"},
        messages=f"Return the running totals of $inputs, one entry per input "
                 f"value, in order. {TAIL}" '{"cumulative": [<numbers>]}',
        expected_code=code('"cumulative": [sum(inputs[:i + 1]) '
                           'for i in range(len(inputs))]'))

    add("a12_percent_change",
        randomize={"earlier": "rng.randrange(200, 900)",
                   "later": "rng.randrange(100, 800)"},
        messages=f"A figure moved from $earlier to $later. Compute the percentage "
                 f"change relative to the earlier value, rounded to 2 decimal "
                 f"places. {TAIL}" '{"pct_change": <number>}',
        expected_code=code('"pct_change": round((later - earlier) / earlier * 100, 2)'))

    add("a13_second_largest", randomize=R6,
        messages=f"What is the SECOND largest value in $inputs? {TAIL}"
                 '{"second_largest": <number>}',
        expected_code=code('"second_largest": sorted(inputs, reverse=True)[1]'))

    add("a14_top_three", randomize={"inputs": "rng.sample(range(100, 900), 7)"},
        messages=f"Return the three largest values in $inputs, largest first. "
                 f"{TAIL}" '{"top_three": [<numbers>]}',
        expected_code=code('"top_three": sorted(inputs, reverse=True)[:3]'))

    add("a15_red_herring", randomize=R6,
        messages=f"Table 7 of the 2019-21 report lists these six district "
                 f"figures: $inputs. Ignore the table number and the report "
                 f"years, they are not data. Return the mean of the six figures "
                 f"rounded to 2 decimal places. {TAIL}" '{"mean": <number>}',
        expected_code=code('"mean": round(statistics.mean(inputs), 2)'))

    add("a16_inline_despite_source_word", randomize=R6,
        messages=f"These six values are taken from a MOSPI bulletin and are "
                 f"reproduced here in full: $inputs. Do not look anything up. "
                 f"Return their maximum. {TAIL}" '{"max": <number>}',
        expected_code=code('"max": max(inputs)'))

    add("a17_string_answer", randomize={"inputs": "rng.sample(range(100, 900), 3)"},
        messages=f"Three districts, Alpha, Beta and Gamma, recorded these values "
                 f"in that order: $inputs. Which district recorded the highest "
                 f"value? {TAIL}" '{"district": "<name>"}',
        expected_code=code('"district": ["Alpha", "Beta", "Gamma"]'
                           '[inputs.index(max(inputs))]'))

    add("a18_boolean_answer",
        randomize={"inputs": "rng.sample(range(10, 90), 3)",
                   "threshold": "rng.randrange(20, 80)"},
        messages=f"Does any value in $inputs exceed $threshold? Answer with a "
                 f"JSON boolean, not a string. {TAIL}"
                 '{"exceeds": <true or false>}',
        expected_code=code('"exceeds": max(inputs) > threshold'))

    # -------------------------------------------------- family b: multi turn
    add("b01_multiturn_basic", randomize={"inputs": "rng.sample(range(50, 800), 7)"},
        messages=["I am going to give you some data, then ask one question about it.",
                  "The data is $inputs",
                  f"What is the maximum value? {TAIL}" '{"max": <number>}'],
        expected_code=code('"max": max(inputs)'))

    add("b02_multiturn_filter", randomize={"inputs": "rng.sample(range(100, 800), 6)"},
        messages=["Data: $inputs",
                  "Now drop the single smallest value.",
                  f"What is the mean of what remains, rounded to 2 decimal "
                  f"places? {TAIL}" '{"mean": <number>}'],
        expected_code=code('"mean": round(statistics.mean('
                           'sorted(inputs)[1:]), 2)'))

    add("b03_multiturn_two_lists",
        randomize={"first": "rng.sample(range(100, 500), 4)",
                   "second": "rng.sample(range(500, 900), 4)"},
        messages=["Here is the first dataset: $first",
                  "Here is the second dataset: $second",
                  f"Combine both datasets and return the total. {TAIL}"
                  '{"total": <number>}'],
        expected_code=code('"total": sum(first) + sum(second)'))

    add("b04_multiturn_revision",
        randomize={"inputs": "rng.sample(range(100, 600), 5)",
                   "extra": "rng.randrange(100, 600)"},
        messages=["The readings are $inputs",
                  "Correction: I missed one. Also include $extra.",
                  f"What is the mean of all the readings, rounded to 2 decimal "
                  f"places? {TAIL}" '{"mean": <number>}'],
        expected_code=code('"mean": round(statistics.mean(inputs + [extra]), 2)'))

    add("b05_multiturn_four_turns",
        randomize={"inputs": "rng.sample(range(20, 200), 6)"},
        messages=["I have some measurements to share.",
                  "The measurements are $inputs",
                  "Ignore any measurement below 50.",
                  f"How many measurements are left, and what is their total? "
                  f"{TAIL}" '{"count": <number>, "total": <number>}'],
        expected_code=code('"count": len([x for x in inputs if x >= 50]), '
                           '"total": sum([x for x in inputs if x >= 50])'))

    # ------------------------------------------------ family c: output shape
    add("c01_wrapper_envelope", randomize={"inputs": "rng.sample(range(20, 300), 4)"},
        messages=f"Sum these numbers: $inputs. {TAIL}"
                 '{"answer": {"sum": <number>}, "log_url": '
                 '"<public wget-able URL to your agent\'s JSONL log>"}',
        expected_code='{"answer": {"sum": sum(inputs)}, "log_url": "%s"}' % LOG)

    add("c02_wrapper_list_answer", randomize={"inputs": "rng.sample(range(20, 300), 5)"},
        messages=f"Sort $inputs ascending. {TAIL}"
                 '{"answer": {"sorted": [<numbers>]}, "log_url": '
                 '"<public wget-able URL to your agent\'s JSONL log>"}',
        expected_code='{"answer": {"sorted": sorted(inputs)}, "log_url": "%s"}' % LOG)

    add("c03_wrapper_scalar_answer", randomize={"inputs": "rng.sample(range(20, 300), 3)"},
        messages=f"What is the largest of $inputs? {TAIL}"
                 '{"answer": <number>, "log_url": '
                 '"<public wget-able URL to your agent\'s JSONL log>"}',
        expected_code='{"answer": max(inputs), "log_url": "%s"}' % LOG)

    add("c04_bare_no_envelope", randomize={"inputs": "rng.sample(range(100, 600), 5)"},
        messages=f"What is the smallest of $inputs? Do not include an answer "
                 f"envelope. {TAIL}" '{"min": <number>}',
        expected_code=code('"min": min(inputs)'))

    add("c05_prose_bait", randomize={"inputs": "rng.sample(range(100, 600), 4)"},
        messages=f"Explain your reasoning carefully, then give the mean of "
                 f"$inputs rounded to 2 decimal places. Whatever you do, your "
                 f"message must contain nothing except the JSON. {TAIL}"
                 '{"mean": <number>}',
        expected_code=code('"mean": round(statistics.mean(inputs), 2)'))

    # -------------------------------------------------- family d: retrieval
    MC = "MANUAL_CHECK: "

    add("d01_mmr_highest_state",
        messages='Which state has the highest maternal mortality rate based on '
                 'MOSPI data? Reply with ONLY a JSON object like '
                 '{"state": "<state name>"}',
        expected=MC + "must name the leading state in the NEWEST SRS maternal "
                      "mortality bulletin, not an older edition. Reply must also "
                      "carry a top level log_url.")

    add("d02_mmr_national",
        messages='What is India\'s national maternal mortality ratio in the most '
                 'recent SRS bulletin? Reply with ONLY a JSON object like '
                 '{"mmr": <number>}',
        expected=MC + "a national ratio from the newest bulletin, roughly 80 to "
                      "100, not a state figure and not a rate.")

    add("d03_mmr_lowest_state",
        messages='Which Indian state has the LOWEST maternal mortality ratio in '
                 'the most recent SRS bulletin? Reply with ONLY a JSON object '
                 'like {"state": "<state name>"}',
        expected=MC + "typically Kerala or Maharashtra, but verify against the "
                      "newest edition rather than trusting this note.")

    add("d04_ratio_vs_rate_trap",
        messages='What is the maternal mortality RATE, that is deaths per 1,000 '
                 'women of reproductive age, for India in the most recent SRS '
                 'bulletin? Reply with ONLY a JSON object like '
                 '{"rate": <number>}',
        expected=MC + "must be a single digit number, not a figure in the "
                      "hundreds. A value near 90 means the ratio column was "
                      "read instead of the rate column.")

    add("d05_indian_states_only",
        messages='Name the three Indian states with the highest maternal '
                 'mortality ratio in the most recent SRS bulletin, highest '
                 'first. Reply with ONLY a JSON object like '
                 '{"states": ["<name>", "<name>", "<name>"]}',
        expected=MC + "all three must be Indian states. Any United States "
                      "state name is an outright fail.")

    add("d06_given_url",
        messages="Using https://www.pib.gov.in/PressReleasePage.aspx?PRID=2128024 "
                 "what was India's maternal mortality ratio in the 2019-21 "
                 'period? Reply with ONLY a JSON object like {"mmr": <number>}',
        expected={"mmr": 93, "log_url": LOG})

    add("d07_two_period_compare",
        messages="Using the SRS maternal mortality bulletins, by how many points "
                 "did India's national maternal mortality ratio fall between the "
                 "2014-16 period and the 2019-21 period? Reply with ONLY a JSON "
                 'object like {"decline": <number>}',
        expected={"decline": 37, "log_url": LOG})

    add("d08_sdg_target",
        messages="What is the Sustainable Development Goal target for maternal "
                 "mortality ratio per one lakh live births by the year 2030? "
                 'Reply with ONLY a JSON object like {"target": <number>}',
        expected={"target": 70, "log_url": LOG})

    add("d09_list_shape_retrieval",
        messages='List the three Indian states with the LOWEST maternal '
                 'mortality ratio in the most recent SRS bulletin, lowest '
                 'first. Reply with ONLY a JSON object like '
                 '{"states": ["<name>", "<name>", "<name>"]}',
        expected=MC + "three real Indian state names in ascending order of "
                      "ratio, taken from one table in one edition.")

    add("d10_commitment_probe",
        messages='Name one district in India with a notably high maternal '
                 'mortality burden. Commit to a single answer. Reply with ONLY '
                 'a JSON object like {"district": "<district name>"}',
        expected=MC + "passes on any plausible real district name. Fails on "
                      "null, an empty string, N/A, or angle bracket text.")

    return Q


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-url",
                    default="https://tds-project-1-q5-1.onrender.com/run.jsonl",
                    help="must match exactly what the bot puts in its replies")
    args = ap.parse_args()
    print(json.dumps(build(args.log_url), indent=2))


if __name__ == "__main__":
    main()
