"""Fixed study descriptions. No individual traces, outcomes or survey answers are read."""
VERSION = 'description-only-time-blind-v1'
SYSTEM = '''Imagine you personally used the described system and completed the study activities below.
Answer the post-study survey from that human user's perspective, as if you had that experience.
You receive only the system description, study context and general activities; no individual interaction trace or original survey answers are provided.
Use your own interpretation to choose plausible ratings. No emotional state, success level, speed or effort is prescribed.
Interaction durations are not provided. Do not invent specific elapsed times or claim to have observed individual events.
These ratings are hypothetical expectations from a description, not measurements of any identified human participant.
Return exactly one JSON object with every required survey key and an integer from its stated scale. No explanations or markdown.'''

DESCRIPTIONS = {
 'testreco': {
  'system': 'TestReco is an adaptive multiple-choice mathematics learning system for Fundamental Mathematics. It estimates mastery from performance and recommends batches of questions using performance, learning-gap or aptitude objectives.',
  'study_context': 'Original participants were students with high-school, undergraduate or graduate education. A participant first completed a 12-question prequalification test, then an adaptive learning phase planned for ten steps with up to five recommended questions per step, then a post-study questionnaire.',
  'activities': ['Read each mathematics question and all available answer options in the prequalification test and select an answer.', 'Work through recommended question batches in the learning phase, selecting an answer for each question.', 'Review recorded answers and correctness feedback, the recommendation strategy and mastery/accuracy updates across learning steps.', 'Continue through the learning steps and complete the final survey about the experience.']
 },
 'quizcomp': {
  'system': 'QuizComp helps mathematics teachers compose multiple-choice quizzes. The teacher specifies topic coverage, a difficulty distribution and the number of questions. The system proposes a quiz with question text, answer options, recorded correct answers, coverage analysis and a target-match score.',
  'study_context': 'Original participants used the quiz-composition interface iteratively to produce a quiz matching their teaching needs, then answered a post-study questionnaire. The interface distinguishes fresh composition with new parameters from improving a quiz with the same parameters.',
  'activities': ['Set desired mathematics topics, difficulty distribution and quiz size.', 'Inspect the proposed questions, all answer options and correct answers, and compare actual coverage and match quality to the requested parameters.', 'Decide whether the quiz meets the teaching needs. If further changes are needed, request improvement with the same parameters or start fresh with different parameters.', 'When finished, validate the quiz or download it as appropriate and complete the final survey.']
 },
 'webarena': {
  'system': 'The WebArena study uses a shopping website (OneStopShop) and a Reddit-like community site. The shopping site offers product search, product pages and customer reviews. The community site provides a form for publishing a titled post with a text body.',
  'study_context': 'Original participants performed a cross-site review-gathering and posting activity for an assigned product, review-star rating and community. The protocol can repeat this activity with another assignment; questionnaires evaluate each activity. The task requires transferring relevant review information accurately between the sites.',
  'activities': ['Open the shopping site, search for the assigned product and inspect its customer reviews.', 'Identify the review titles with the requested star rating and collect those titles.', 'Open the assigned community on the Reddit-like site and create a post using the required title and gathered review titles.', 'Check the result or indicate inability to complete the task, then answer the post-task survey.']
 }
}


def scenario(app):
    return dict(application=app,condition='description_only',**DESCRIPTIONS[app])
