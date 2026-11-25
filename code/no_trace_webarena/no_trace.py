"""Task context only: never render participant actions, answers or submissions."""
import re
from pathlib import Path

VISUALS = Path(__file__).resolve().parent / 'data/no_trace_visuals'

def task_pages(task):
    product = task['product']
    match = re.search(r'reviews with (.+?) rating', task['intent'], re.I)
    rating = match.group(1) if match else 'the rating specified in the task instruction'
    community = task['forum_path']
    post_title = 'real user feedback on ' + product
    return [
        {'title': 'Task description', 'description': f'''Imagine that you completed the following task:

1. Open **OneStopShop** and search for **{product}**.
2. Find customer reviews with **{rating}**. Check each review’s own star rating.
3. Copy only the **titles** of the matching reviews to the study platform, one title per line.
4. Open **Reddit**, which takes you directly to the dedicated community **{community}**.
5. Create and submit a post with the collected review titles in its body and this post title:

> {post_title}

After viewing the general website screenshots, answer the questionnaire as if you had performed this interaction yourself.'''},
        {'title': 'Shopping website', 'description': f'''Imagine using this shopping website to find **{product}**, identify reviews with **{rating}**, and copy their titles to the study platform. You may find the product on your first search, or you may need to scroll through more results or browse additional pages. Collect the short review headings, rather than the longer review bodies.''', 'image': VISUALS / 'shopping.png'},
        {'title': 'Original study platform', 'description': 'Imagine returning to this study platform after finding the matching reviews. Paste their titles into the **Review titles** field, one per line, then select **Save review titles · Continue to Reddit**. The screenshot shows the original Streamlit layout with generic task labels and an empty answer field.', 'image': VISUALS / 'study.png'},
        {'title': 'Reddit website', 'description': f'''Imagine opening Reddit directly in **{community}** and submitting a post there. Use **{post_title}** as the title and the collected review titles as the body.

The screenshot shows the general Reddit interface; your assigned community is **{community}**.''', 'image': VISUALS / 'reddit.png'},
    ]
