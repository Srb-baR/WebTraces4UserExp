"""Prolific entry point: briefing → consent → two tasks → nine-item survey."""
import json
import os
import streamlit as st
import streamlit.components.v1 as components
import db
import native
from urllib.parse import urlencode
from settings import config
from survey import ITEMS, validate

st.set_page_config(
    page_title="Interaction Experience Study",
    page_icon="💬",
    layout="wide"
)

db.init()

st.title("Interaction Experience Study")
st.caption("Understanding subjective experience during web interactions")

token = st.query_params.get("session", "")

if not token:
    st.markdown(
        ("You will complete **two full tasks**: one about a gaming product and one about toddler clothing. "
        "For each product, find reviews on the shopping website, publish a Reddit post, and answer "
        "the same nine-question survey. You will finish the first survey before starting the second task.") if config().get("full_tasks") else "You will find review titles on the shopping website, publish a Reddit post, and answer a nine-question survey about your experience."
    )

    st.markdown(
        "We are interested in your **effort, mental demand, temporal demand, sense of "
        "control, frustration, satisfaction, and overall experience**. "
        "We are evaluating the interaction, not you."
    )

    if config()["mode"] == "local_replica":
        st.info(
            "This study uses local research replicas of OneStopShop and Reddit. "
            "Posts remain within your study session."
        )
    else:
        st.info(
            "You will use the shopping and Reddit websites in separate browser tabs, "
            "then return here."
        )

    st.markdown(
        "Your interactions with the study websites, including clicks, navigation, "
        "scrolling, typed text, and task duration, will be recorded for research purposes."
    )

    st.markdown(
        "Participation is voluntary. You may withdraw at any time. "
        "Please do not enter personal information during the task."
    )

    from prolific import identity
    try:pid,study,psession=identity(st.query_params)
    except ValueError:
        st.info('Please open this study from Prolific. Your participant ID is passed automatically in that link; you do not need to type it here.')
        st.stop()
    st.caption('Your Prolific participant ID has been received automatically.')
    with st.form("consent"):
        consent = st.checkbox(
            "I have read the study information and consent to participation "
            "and interaction recording."
        )

        submit = st.form_submit_button(
            "Consent and enter study",
            type="primary"
        )

    if submit:
        if not consent or not pid.strip():
            st.error("Please confirm consent to continue.")
        else:
            st.query_params["session"] = db.create(pid, study, psession)
            st.rerun()

    st.stop()
try:
    s=db.session(token)
except ValueError:
    st.error("This study session could not be found. Open your original Prolific study link.")
    st.stop()
cfg=json.loads(s["config_json"])
phase=s["phase"]
round_no=cfg.get("full_task_no",1)
round_count=cfg.get("full_task_count",1)
if phase=="withdrawn":
    st.info("You have withdrawn. Recording has stopped. Return to Prolific to return your submission or contact the researcher.")
    st.stop()
if phase=="done":
    next_token=db.next_task_token(token)
    if next_token:
        st.success(f"Full task {round_no} and its questionnaire are saved.")
        st.write("Next: a separate shopping-and-Reddit task about toddler clothing, followed by the same questionnaire.")
        if st.button("Start full task 2",type="primary"):
            st.query_params['session']=next_token
            st.rerun()
        st.stop()
    st.success("Thank you. All assigned tasks and their questionnaires have been saved.")
    if cfg.get("completion_code"):
        st.code(cfg["completion_code"],language=None)
        st.link_button("Return to Prolific",f'https://app.prolific.com/submissions/complete?cc={cfg["completion_code"]}')
    else:
        st.warning("Preview complete. A Prolific completion code has not been configured for this study.")
    st.stop()
with st.sidebar:
    st.write(f"Full task {round_no} of {round_count} · Shopping + Reddit")
    st.caption("Your session is saved. Keep this browser link private; it lets you resume your study.")
    if st.button("Withdraw from study"):
        db.change(token,"withdraw")
        st.rerun()
if phase=="ready":
    st.subheader(f"Full task {round_no} · Your assignment")
    st.write(s["intent"])
    st.markdown("Complete shopping and Reddit for this product, then answer the questionnaire about this full task only. Timing starts when you select Start.")
    if st.button(f"Start full task {round_no} · Shopping",type="primary"):
        db.change(token,"start")
        st.rerun()
elif phase in ("shopping","reddit") and cfg["mode"] == "live":
    st.progress(0.25 if phase=="shopping" else 0.65)
    st.subheader(f"Full task {round_no} · "+("Shopping: find review titles" if phase=="shopping" else "Reddit: publish your post"))
    st.write(f"**Product:** {cfg['product']}")
    st.write(f"**Rating:** {cfg['rating']}")
    site="shopping" if phase=="shopping" else "reddit"
    # Return URL can be configured for a public deployment; local VS Code uses 8505.
    study_url=os.environ.get("WEBARENA_STUDY_URL","http://127.0.0.1:8505").rstrip('/')
    return_url=study_url+"/?"+urlencode({"session":token})
    website=config().get(site+"_browser_url",cfg[site+"_url"]).rstrip('/')
    link=website+"/__study__/start/"+site+"#"+urlencode({"token":token,"return":return_url})
    st.info("Open the website in a new tab and use it normally. Keep this study tab open and return here when you are finished. A small badge on the website confirms that recording is connected.")
    st.link_button("Open OneStopShop ↗" if site=="shopping" else "Open Reddit ↗",link,type="primary")
    if phase=="shopping":
        st.write("Search for the product, read its reviews, and collect the titles with the requested rating. Paste one title per line below.")
        with st.form("gather_native_"+s["id"]):
            titles=st.text_area("Review titles",height=180,key="titles_"+s["id"])
            finished=st.form_submit_button("Save review titles · Continue to Reddit",type="primary")
        if finished:
            try:
                db.change(token,"gather",{"titles":titles,"interface":"native_tabs"})
                st.rerun()
            except ValueError as e:st.error(str(e))
    else:
        st.write(f"Create a text post in **{cfg.get('forum_path','/f/gaming')}** on the WebArena Reddit website.")
        st.caption("Use this exact post title:")
        st.code("real user feedback on "+cfg['product'],language=None)
        for submission in db.state(token)['submissions']:
            if submission['kind']=='gather':
                st.caption("Your saved review titles — use these as the post body:")
                st.code(json.loads(submission['payload_json'])['titles'],language=None)
        st.write("After publishing, leave the published post open for a few seconds so its details can be saved, then return here.")
        if st.button("I have published my post · Continue to questionnaire",type="primary"):
            evidence=native.latest_post(token)
            if evidence:
                db.change(token,"live_finish",{**evidence,"interface":"native_tabs"})
                st.rerun()
            else:
                st.error("The published post has not been recorded yet. Open it in the Reddit tab, check that the recording badge is connected, wait a few seconds, and try again.")
    with st.expander("Having trouble completing this task?"):
        st.write("You can continue even if you could not complete the task. We still want to hear about your experience.")
        if st.button("I cannot complete this task"):
            db.change(token,"unable",{"interface":"native_tabs"})
            st.rerun()
elif phase in ("shopping","reddit"):
    st.subheader("Complete the two website tasks below")
    st.caption("If the workspace shows that both tasks are finished, use Continue to open your questionnaire.")
    public=os.environ.get("WEBARENA_WORKSPACE_URL","http://localhost:8766").rstrip('/')
    workspace_path="live" if cfg["mode"] == "live" else "workspace"
    workspace_url=f"{public}/{workspace_path}#token={token}"
    if hasattr(st,"iframe"):
        st.iframe(workspace_url,height=1100)
    else:
        components.iframe(workspace_url,height=1100,scrolling=True)
    if st.button("Continue / refresh progress",type="primary"):
        st.rerun()
elif phase=="survey":
    st.subheader(f"Full task {round_no} · Tell us about your experience")
    st.write(f"Answer only about {cfg['product']}: finding reviews and posting them to {cfg.get('forum_path','/f/gaming')}. There are no right or wrong answers.")
    answers={}
    with st.form("survey_"+s["id"]):
        for i,(key,question,low,high) in enumerate(ITEMS,1):
            st.markdown(f"**Q{i}. {question}**")
            if key=="would_use_again":
                value=st.radio("Choose an answer",["No","Yes"],index=None,horizontal=True,key=s["id"]+key,label_visibility="collapsed")
                answers[key]=None if value is None else int(value=="Yes")
            else:
                st.caption(f"1 = {low} · 5 = {high}")
                answers[key]=st.radio("Choose a rating",[1,2,3,4,5],index=None,horizontal=True,key=s["id"]+key,label_visibility="collapsed")
        comments=st.text_area("Anything else about your experience? (Optional)",max_chars=10000,key="comments_"+s["id"])
        submit=st.form_submit_button("Submit questionnaire",type="primary")
    if submit:
        try:
            validate(answers)
            db.save_survey(token,answers,comments)
            st.rerun()
        except ValueError:
            st.error("Please answer all nine questions before submitting.")

# Record only the task interface, never the questionnaire.
if phase in ('shopping','reddit') and cfg['mode']=='live':
    from pathlib import Path
    recorder=(Path(__file__).parent/'static/study_recorder.html').read_text()
    recorder_cfg={'token':token,'task':1 if phase=='shopping' else 2,'phase':phase,'gateway':config().get('shopping_browser_url',cfg['shopping_url']).rstrip('/')}
    components.html(recorder.replace('__CONFIG__',json.dumps(recorder_cfg).replace('<','\u003c')),height=0)
