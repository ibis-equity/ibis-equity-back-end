import base64
import json
import logging
import os
from typing import Any, Dict, List

import boto3
from langchain_classic.chains import ConversationalRetrievalChain
from langchain_aws import BedrockEmbeddings, ChatBedrockConverse
from langchain_community.vectorstores import OpenSearchVectorSearch
from langchain_core.prompts import PromptTemplate
from opensearchpy import AWSV4SignerAuth, RequestsHttpConnection

LOGGER = logging.getLogger(__name__)

AOSS_INDEX_NAME_ENV_VAR = "AOSS_INDEX_NAME"
AOSS_ID_ENV_VAR = "AOSS_ID"
AOSS_AWS_REGION_ENV_VAR = "AOSS_AWS_REGION"
AOSS_SVC_NAME = "aoss"
ALLOWED_ORIGIN_ENV_VAR = "ALLOWED_ORIGIN"
BEDROCK_EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"

DEFAULT_TIMEOUT_AOSS = 100
DEFAULT_K = 3
DEFAULT_MODEL_ID = os.environ.get("BEDROCK_CHAT_MODEL_ID", "amazon.nova-micro-v1:0")
DEFAULT_POLLY_VOICE_ID = os.environ.get("POLLY_VOICE_ID", "Joanna")
DEFAULT_POLLY_ENGINE = os.environ.get("POLLY_ENGINE", "standard")
DEFAULT_POLLY_LANGUAGE_CODE = os.environ.get("POLLY_LANGUAGE_CODE", "en-US")


class MissingEnvironmentVariable(Exception):
    """Raised if a required environment variable is missing"""


def _configure_logger() -> None:
    default_log_args = {
        "level": logging.DEBUG if os.environ.get("VERBOSE", "").lower() == "true" else logging.INFO,
        "format": "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        "datefmt": "%d-%b-%y %H:%M",
        "force": True,
    }
    logging.basicConfig(**default_log_args)


def _cors_headers() -> Dict[str, str]:
    origin = os.environ.get(ALLOWED_ORIGIN_ENV_VAR, "*")
    return {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "OPTIONS,POST",
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
    }


def _response(status_code: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": _cors_headers(),
        "body": json.dumps(payload),
    }


def _get_required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise MissingEnvironmentVariable(f"{name} environment variable is required")
    return value


def _parse_body(event: Dict[str, Any]) -> Dict[str, Any]:
    body = event.get("body")
    if isinstance(body, str):
        return json.loads(body)
    if isinstance(body, dict):
        return body
    if event.get("test_event", "").lower() == "true":
        return event
    raise ValueError("Request body is required")


def _parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _synthesize_speech(answer: str, region: str, voice_id: str, engine: str, language_code: str) -> str:
    if not answer:
        return ""

    # Polly has a text size limit per request; keep a safe margin.
    text = answer[:2800]
    client = boto3.client("polly", region_name=region)
    request: Dict[str, Any] = {
        "Text": text,
        "OutputFormat": "mp3",
        "VoiceId": voice_id,
    }
    if engine:
        request["Engine"] = engine
    if language_code:
        request["LanguageCode"] = language_code

    response = client.synthesize_speech(**request)
    audio_stream = response.get("AudioStream")
    if not audio_stream:
        return ""

    try:
        audio_bytes = audio_stream.read()
    finally:
        audio_stream.close()

    return base64.b64encode(audio_bytes).decode("utf-8")


def _build_chain(host: str, index_name: str, region: str, model_id: str, top_k: int, system_prompt: str):
    LOGGER.info("Using Bedrock chat model: %s", model_id)

    # Use Converse API so request payloads are emitted as "messages" for Nova models.
    llm = ChatBedrockConverse(
        region_name=region,
        model=model_id,
        temperature=0.3,
        max_tokens=400,
    )

    embeddings = BedrockEmbeddings(
        region_name=region,
        model_id=BEDROCK_EMBEDDING_MODEL_ID,
    )
    vector_store = OpenSearchVectorSearch(
        f"https://{host}",
        index_name,
        embeddings,
        http_auth=AWSV4SignerAuth(boto3.Session().get_credentials(), region, AOSS_SVC_NAME),
        timeout=DEFAULT_TIMEOUT_AOSS,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
    )
    retriever = vector_store.as_retriever(search_kwargs={"k": top_k})

    qa_prompt_template = f"""Human: You are a practical assistant focused on actionable answers.
    Use the provided context and keep the answer concise and useful.
    Domain guidance: {system_prompt}

    <documents>
    {{context}}
    </documents>

    Question: {{question}}

    If the answer is not in context, say you do not know.

    Assistant:
    """

    prompt = PromptTemplate(template=qa_prompt_template, input_variables=["context", "question"])

    condense_qa_template = """{chat_history}
    Human: Rephrase the follow-up into a standalone question.
    Follow Up Question: {question}
    Standalone Question:
    Assistant:"""

    standalone_question_prompt = PromptTemplate.from_template(condense_qa_template)

    return ConversationalRetrievalChain.from_llm(
        llm=llm,
        retriever=retriever,
        condense_question_prompt=standalone_question_prompt,
        return_source_documents=True,
        combine_docs_chain_kwargs={"prompt": prompt},
        verbose=False,
    )


def _extract_sources(source_documents: List[Any]) -> List[Dict[str, Any]]:
    sources: List[Dict[str, Any]] = []
    seen = set()
    for doc in source_documents:
        source_uri = doc.metadata.get("source", "")
        if source_uri in seen:
            continue
        seen.add(source_uri)
        sources.append(
            {
                "title": source_uri.split("/")[-1] if source_uri else "Reference",
                "uri": source_uri,
                "excerpt": (doc.page_content or "")[:220],
            }
        )
    return sources


def lambda_handler(event, context):
    _configure_logger()

    method = (
        event.get("requestContext", {})
        .get("http", {})
        .get("method", "")
        .upper()
    )
    if method == "OPTIONS":
        return _response(200, {"ok": True})

    try:
        body = _parse_body(event)
        question = str(body.get("question", "")).strip()
        if not question:
            return _response(400, {"error": "question is required"})

        config = body.get("config", {}) if isinstance(body.get("config", {}), dict) else {}
        model_id = str(config.get("modelId", DEFAULT_MODEL_ID)).strip() or DEFAULT_MODEL_ID
        top_k = int(config.get("topK", DEFAULT_K) or DEFAULT_K)
        system_prompt = str(config.get("systemPrompt", "Provide clear, grounded recommendations.")).strip()
        speech_default = _parse_bool(os.environ.get("RAG_QUERY_ENABLE_POLLY", "true"), default=True)
        speech_enabled = _parse_bool(config.get("speechEnabled"), default=speech_default)
        voice_id = str(config.get("voiceId", DEFAULT_POLLY_VOICE_ID)).strip() or DEFAULT_POLLY_VOICE_ID
        engine = str(config.get("engine", DEFAULT_POLLY_ENGINE)).strip() or DEFAULT_POLLY_ENGINE
        language_code = str(config.get("languageCode", DEFAULT_POLLY_LANGUAGE_CODE)).strip() or DEFAULT_POLLY_LANGUAGE_CODE

        aoss_id = _get_required_env(AOSS_ID_ENV_VAR)
        aoss_region = _get_required_env(AOSS_AWS_REGION_ENV_VAR)
        index_name = _get_required_env(AOSS_INDEX_NAME_ENV_VAR)

        host = f"{aoss_id}.{aoss_region}.{AOSS_SVC_NAME}.amazonaws.com:443"
        chain = _build_chain(host, index_name, aoss_region, model_id, max(top_k, 1), system_prompt)
        result = chain({"question": question, "chat_history": []})

        answer = result.get("answer", "")
        source_documents = result.get("source_documents", [])

        speech: Dict[str, Any] = {
            "enabled": speech_enabled,
            "format": "mp3",
            "voiceId": voice_id,
            "engine": engine,
            "languageCode": language_code,
        }
        if speech_enabled:
            try:
                speech["audioBase64"] = _synthesize_speech(answer, aoss_region, voice_id, engine, language_code)
            except Exception:
                LOGGER.exception("Polly synthesis failed")
                speech["audioBase64"] = ""

        return _response(
            200,
            {
                "answer": answer,
                "sources": _extract_sources(source_documents),
                "speech": speech,
            },
        )
    except MissingEnvironmentVariable as exc:
        LOGGER.exception("Configuration error")
        return _response(500, {"error": str(exc)})
    except (ValueError, json.JSONDecodeError) as exc:
        LOGGER.warning("Bad request")
        return _response(400, {"error": str(exc)})
    except Exception as exc:
        LOGGER.exception("Unhandled error in rag query lambda")
        return _response(500, {"error": "Internal server error"})
