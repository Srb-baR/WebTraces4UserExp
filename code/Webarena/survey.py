"""Same keys, order, scales and endpoints as WebArenaShopping/replay_app/survey.py.
Wording adapted from observation of shopping to first-person cross-site performance.
"""
ITEMS = [
 ("accomplishment", "How successful do you feel you were in finding the requested review titles and posting them in the assigned Reddit community?", "Not successful at all", "Completely successful"),
 ("effort_required", "How much effort was required to find the product, identify reviews with the requested rating, and post their titles?", "Very little effort", "Very high effort"),
 ("mental_demand", "How mentally demanding was it to read the reviews, identify the matching titles, and prepare the Reddit post?", "Not demanding", "Extremely demanding"),
 ("perceived_controllability", "How much control did you feel you had over finding the right reviews and creating your post? Did the search, filters, and posting controls do what you wanted?", "No control at all", "Complete control"),
 ("temporal_demand", "How time-pressured did you feel while completing the review-gathering and posting tasks?", "Not rushed at all", "Extremely rushed"),
 ("frustration", "How frustrated, irritated or discouraged did you feel while working with the shopping and Reddit sites?", "Not frustrated", "Extremely frustrated"),
 ("trust", "How much did you trust the sites to show relevant and correct review information and save your post correctly?", "No trust", "Complete trust"),
 ("satisfaction", "Overall, how satisfied are you with the outcome you reached for this review-gathering and posting activity?", "Very dissatisfied", "Very satisfied"),
 ("would_use_again", "Would you use these shopping and Reddit sites again for a similar task?", "No", "Yes"),
]
VERSION = "template101-round-v2"

def validate(answers):
    if set(answers) != {i[0] for i in ITEMS}:
        raise ValueError("Please answer all nine questions.")
    for key, value in answers.items():
        allowed = (0, 1) if key == "would_use_again" else range(1, 6)
        if type(value) is not int or value not in allowed:
            raise ValueError(f"Invalid answer for {key}")
