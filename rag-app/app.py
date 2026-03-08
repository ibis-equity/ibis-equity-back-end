import logging
import os
import sys
import uuid

import boto3
import streamlit as st

import aoss_chat_bedrock as bedrock_claude


# TODO: clean up the way this app is written
USER_ICON = "images/user-icon.png"
AI_ICON = "images/ai-icon.png"
MAX_HISTORY_LENGTH = 5

AOSS_INDEX_NAME_ENV_VAR = "AOSS_INDEX_NAME"
AOSS_ID_ENV_VAR = "AOSS_ID"
AOSS_AWS_REGION_ENV_VAR = "AOSS_AWS_REGION"
AOSS_SVC_NAME = "aoss"

DEFAULT_LOG_LEVEL = logging.INFO
LOGGER = logging.getLogger(__name__)
LOGGING_FORMAT = "%(asctime)s %(levelname)-5.5s " \
                 "[%(name)s]:[%(threadName)s] " \
                 "%(message)s"
DEFAULT_POLLY_VOICE_ID = os.environ.get("POLLY_VOICE_ID", "Joanna")
DEFAULT_POLLY_ENGINE = os.environ.get("POLLY_ENGINE", "standard")
DEFAULT_POLLY_LANGUAGE_CODE = os.environ.get("POLLY_LANGUAGE_CODE", "en-US")
DEFAULT_PREFERRED_LANGUAGE = os.environ.get("PREFERRED_LANGUAGE", "English")

LANGUAGE_PREFERENCES = {
    "English": {"instruction": "Respond in English.", "voice_id": "Joanna", "language_code": "en-US"},
    "Spanish": {"instruction": "Respond in Spanish.", "voice_id": "Lupe", "language_code": "es-US"},
    "French": {"instruction": "Respond in French.", "voice_id": "Lea", "language_code": "fr-FR"},
    "German": {"instruction": "Respond in German.", "voice_id": "Vicki", "language_code": "de-DE"},
    "Italian": {"instruction": "Respond in Italian.", "voice_id": "Bianca", "language_code": "it-IT"},
    "Portuguese": {"instruction": "Respond in Portuguese.", "voice_id": "Camila", "language_code": "pt-BR"},
}


class MissingEnvironmentVariable(Exception):
    """Raised if a required environment variable is missing"""


# logging configuration
log_level = DEFAULT_LOG_LEVEL
if os.environ.get("VERBOSE", "").lower() == "true":
    log_level = logging.DEBUG
logging.basicConfig(level=log_level, format=LOGGING_FORMAT)


def _env_flag(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _synthesize_answer_audio(answer_text: str, voice_id: str, language_code: str):
    if not _env_flag("ENABLE_POLLY_TTS", default=True):
        return None
    if not st.session_state.get("enable_speech", True):
        return None
    if not answer_text:
        return None

    region = os.environ.get("AWS_REGION")
    if not region:
        LOGGER.warning("Skipping Polly synthesis because AWS_REGION is not set")
        return None

    request = {
        "Text": answer_text[:2800],
        "OutputFormat": "mp3",
        "VoiceId": voice_id or DEFAULT_POLLY_VOICE_ID,
    }
    if DEFAULT_POLLY_ENGINE:
        request["Engine"] = DEFAULT_POLLY_ENGINE
    if language_code:
        request["LanguageCode"] = language_code
    elif DEFAULT_POLLY_LANGUAGE_CODE:
        request["LanguageCode"] = DEFAULT_POLLY_LANGUAGE_CODE

    try:
        response = boto3.client("polly", region_name=region).synthesize_speech(**request)
        audio_stream = response.get("AudioStream")
        if not audio_stream:
            return None
        try:
            return audio_stream.read()
        finally:
            audio_stream.close()
    except Exception:
        LOGGER.exception("Failed to synthesize answer audio with Polly")
        return None

# serverless collection ID
aoss_id = os.environ.get(AOSS_ID_ENV_VAR)
if not aoss_id:
    raise MissingEnvironmentVariable(
        f"{AOSS_ID_ENV_VAR} environment variable is required")

# opensearch index for RAG
index_name = os.environ.get(AOSS_INDEX_NAME_ENV_VAR)
if not index_name:
    raise MissingEnvironmentVariable(
        f"{AOSS_INDEX_NAME_ENV_VAR} environment variable is required")


#function to read a properties file and create environment variables
def read_properties_file(filename):
    import os
    import re
    with open(filename, 'r') as f:
        for line in f:
            m = re.match(r'^\s*(\w+)\s*=\s*(.*)\s*$', line)
            if m:
                os.environ[m.group(1)] = m.group(2)


# Check if the user ID is already stored in the session state
if 'user_id' in st.session_state:
    user_id = st.session_state['user_id']

# If the user ID is not yet stored in the session state, generate a random UUID
else:
    user_id = str(uuid.uuid4())
    st.session_state['user_id'] = user_id


if 'llm_chain' not in st.session_state:
    if (len(sys.argv) > 1):
        if (sys.argv[1] == 'bedrock_claude'):
            st.session_state['llm_app'] = bedrock_claude
            st.session_state['llm_chain'] = bedrock_claude.build_chain(
                f"{aoss_id}.{os.environ['AWS_REGION']}.{AOSS_SVC_NAME}.amazonaws.com:443",
                index_name
            )
        else:
            raise Exception("Unsupported LLM: ", sys.argv[1])
    else:
        raise Exception("Usage: streamlit run app.py bedrock_claude")

if 'chat_history' not in st.session_state:
    st.session_state['chat_history'] = []
    
if "chats" not in st.session_state:
    st.session_state.chats = [
        {
            'id': 0,
            'question': '',
            'answer': ''
        }
    ]

if "questions" not in st.session_state:
    st.session_state.questions = []

if "answers" not in st.session_state:
    st.session_state.answers = []

if "input" not in st.session_state:
    st.session_state.input = ""

if "enable_speech" not in st.session_state:
    st.session_state.enable_speech = _env_flag("ENABLE_POLLY_TTS", default=True)

if "preferred_language" not in st.session_state:
    if DEFAULT_PREFERRED_LANGUAGE in LANGUAGE_PREFERENCES:
        st.session_state.preferred_language = DEFAULT_PREFERRED_LANGUAGE
    else:
        st.session_state.preferred_language = "English"


st.markdown("""
        <style>
               .block-container {
                    padding-top: 32px;
                    padding-bottom: 32px;
                    padding-left: 0;
                    padding-right: 0;
                }
                .element-container img {
                    background-color: #000000;
                }

                .main-header {
                    font-size: 24px;
                }
        </style>
        """, unsafe_allow_html=True)


def write_logo():
    col1, col2, col3 = st.columns([5, 1, 5])
    with col2:
        st.image(AI_ICON, width='stretch') 


def write_top_bar():
    col1, col2, col3 = st.columns([1, 9, 3])
    with col1:
        st.image(AI_ICON, width='stretch')
    with col2:
        selected_provider = sys.argv[1]
        provider = selected_provider.capitalize()
        header = f"An AI App powered by OpenSearch and {provider}!"
        st.write(f"<h3 class='main-header'>{header}</h3>", unsafe_allow_html=True)
    with col3:
        clear = st.button("Clear Chat")
    return clear


def _get_language_config():
    preferred_language = st.session_state.get("preferred_language", "English")
    return LANGUAGE_PREFERENCES.get(preferred_language, LANGUAGE_PREFERENCES["English"])


def _apply_language_preference(question_text: str):
    language_config = _get_language_config()
    return f"{question_text}\n\n{language_config['instruction']}"


clear = write_top_bar()

if clear:
    st.session_state.questions = []
    st.session_state.answers = []
    st.session_state.input = ""
    st.session_state["chat_history"] = []

# Keep speech control in the main flow so it remains visible across screen sizes.
st.toggle("Enable Speech", key="enable_speech", help="Read AI answers aloud with Amazon Polly")
st.selectbox(
    "Preferred Language",
    options=list(LANGUAGE_PREFERENCES.keys()),
    key="preferred_language",
    help="AI responses and speech follow this language preference.",
)


def handle_input():
    input = st.session_state.input
    question_with_id = {
        'question': input,
        'id': len(st.session_state.questions)
    }
    st.session_state.questions.append(question_with_id)

    chat_history = st.session_state["chat_history"]
    if len(chat_history) == MAX_HISTORY_LENGTH:
        chat_history = chat_history[:-1]

    llm_chain = st.session_state['llm_chain']
    chain = st.session_state['llm_app']
    model_prompt = _apply_language_preference(input)
    result = chain.run_chain(llm_chain, model_prompt, chat_history)
    answer = result['answer']
    language_config = _get_language_config()
    answer_audio = _synthesize_answer_audio(
        answer,
        language_config.get("voice_id", DEFAULT_POLLY_VOICE_ID),
        language_config.get("language_code", DEFAULT_POLLY_LANGUAGE_CODE),
    )
    if answer_audio:
        result['audio'] = answer_audio
    chat_history.append((input, answer))
    
    document_list = []
    if 'source_documents' in result:
        for d in result['source_documents']:
            if not (d.metadata['source'] in document_list):
                document_list.append((d.metadata['source']))

    st.session_state.answers.append({
        'answer': result,
        'sources': document_list,
        'id': len(st.session_state.questions)
    })
    st.session_state.input = ""


def write_user_message(md):
    col1, col2 = st.columns([1,12])
    
    with col1:
        st.image(USER_ICON, width='stretch')
    with col2:
        st.warning(md['question'])


def render_result(result):
    answer, sources = st.tabs(['Answer', 'Sources'])
    with answer:
        render_answer(result['answer'])
    with sources:
        if 'source_documents' in result:
            render_sources(result['source_documents'])
        else:
            render_sources([])


def render_answer(answer):
    col1, col2 = st.columns([1,12])
    with col1:
        st.image(AI_ICON, width='stretch')
    with col2:
        st.info(answer['answer'])
        speech_on = bool(st.session_state.get("enable_speech", False))
        preferred_language = st.session_state.get("preferred_language", "English")
        audio_ready = bool(answer.get('audio'))
        if speech_on and audio_ready:
            st.caption(f"Speech: ON (audio ready) | Language: {preferred_language}")
        elif speech_on:
            st.caption(f"Speech: ON (no audio generated) | Language: {preferred_language}")
        else:
            st.caption(f"Speech: OFF | Language: {preferred_language}")
        if answer.get('audio'):
            # Auto-play freshly generated Polly audio for a smoother chat UX.
            st.audio(answer['audio'], format='audio/mpeg', autoplay=True)


def render_sources(sources):
    col1, col2 = st.columns([1,12])
    with col2:
        with st.expander("Sources"):
            for s in sources:
                st.write(s)


#Each answer will have context of the question asked in order to associate the provided feedback with the respective question
def write_chat_message(md, q):
    chat = st.container()
    with chat:
        render_answer(md['answer'])
        render_sources(md['sources'])
    
        
with st.container():
  for (q, a) in zip(st.session_state.questions, st.session_state.answers):
    write_user_message(q)
    write_chat_message(a, q)


st.markdown('---')
input = st.text_input("You are talking to an AI, ask any question.", key="input", on_change=handle_input)
