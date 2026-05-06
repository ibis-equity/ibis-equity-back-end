import json
import logging
import os
import random
import re
import time
from urllib.parse import unquote_plus

import boto3
from botocore.exceptions import ClientError
from langchain_core.documents import Document
from langchain_aws import BedrockEmbeddings
from langchain_community.vectorstores import OpenSearchVectorSearch
from opensearchpy import RequestsHttpConnection, AWSV4SignerAuth


LOGGER = logging.getLogger(__name__)

SQS_QUEUE_ENV_VAR = "QUEUE_URL"
AOSS_INDEX_NAME_ENV_VAR = "AOSS_INDEX_NAME"
AOSS_ID_ENV_VAR = "AOSS_ID"
AOSS_AWS_REGION_ENV_VAR = "AOSS_AWS_REGION"
AOSS_SVC_NAME = "aoss"
BEDROCK_EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"

DEFAULT_TIMEOUT_AOSS = 100
DEFAULT_AOSS_ENGINE = "faiss"
MAX_EMBED_CHARS = 4000
CHUNK_OVERLAP_CHARS = 300
MAX_EMBED_BATCH_DOCS = 8
MAX_EMBED_BATCH_TOTAL_CHARS = 40000
MAX_THROTTLE_RETRIES = 8
THROTTLE_BACKOFF_BASE_SECONDS = 1.0
THROTTLE_BACKOFF_MAX_SECONDS = 20.0
BATCH_PACING_SECONDS = 0.35
IMAGE_URI_PATTERN = re.compile(r"s3://[^\s\]\)]+")
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".svg")
MAX_IMAGE_URIS_PER_DOC = 40
MAX_IMAGE_URIS_EMBEDDED_PER_CHUNK = 6


def _normalize_s3_uri(uri: str) -> str:
    """Sanitize extracted S3 URIs that may include wrappers or trailing punctuation."""
    if not uri:
        return ""
    return re.sub(r"[\.,;:!\?\"'`\)>\]}]+$", "", uri.strip().strip("<>\"'`"))


class MalformedEvent(Exception):
    """Raised if a malformed event received"""


class MissingEnvironmentVariable(Exception):
    """Raised if a required environment variable is missing"""


def _is_indexable_key(object_key: str) -> bool:
    """Only processed text files are indexable."""
    if not object_key:
        return False
    return object_key.lower().endswith(".txt")


def _silence_noisy_loggers():
    """Silence chatty libraries for better logging"""
    for logger in ['boto3', 'botocore',
                   'botocore.vendored.requests.packages.urllib3']:
        logging.getLogger(logger).setLevel(logging.WARNING)


def _configure_logger():
    """Configure python logger for lambda function"""
    default_log_args = {
        "level": logging.DEBUG if os.environ.get("VERBOSE", False) else logging.INFO,
        "format": "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        "datefmt": "%d-%b-%y %H:%M",
        "force": True,
    }
    logging.basicConfig(**default_log_args)


def _check_missing_field(validation_dict, extraction_key):
    """Check if a field exists in a dictionary

    :param validation_dict: Dictionary
    :param extraction_key: String

    :raises: KeyError
    """
    extracted_value = validation_dict.get(extraction_key)
    
    if not extracted_value:
        LOGGER.error(f"Missing '{extraction_key}' key in the dict")
        raise KeyError


def _get_message_body(event):
    """Extract message body from the event
    
    :param event: Dictionary
    
    :raises: MalformedEvent
    
    :rtype: Dictionary
    """
    body = ""
    test_event = event.get("test_event", "")
    if test_event.lower() == "true":
        LOGGER.info("processing test event (and not from SQS)")
        LOGGER.debug("Test body: %s", event)
        return event
    else:
        LOGGER.info("Attempting to extract message body from SQS")
        
        _check_missing_field(event, "Records")
        records = event["Records"]
        
        first_record = records[0]
        
        try:
            body = first_record.get("body")
        except AttributeError:
            raise MalformedEvent("First record is not a proper dict")
        
        if not body:
            raise MalformedEvent("Missing 'body' in the record")
            
        try:
            return json.loads(body)
        except json.decoder.JSONDecodeError:
            raise MalformedEvent("'body' is not valid JSON")


def _get_sqs_message_attributes(event):
    """Extract receiptHandle from message
    
    :param event: Dictionary
    
    :raises: MalformedEvent
    
    :rtype: Dictionary
    """
    LOGGER.info("Attempting to extract receiptHandle from SQS")
    records = event.get("Records")
    if not records:
        LOGGER.warning("No receiptHandle found, probably not an SQS message")
        return
    try:
        first_record = records[0]
    except IndexError:
        raise MalformedEvent("Records seem to be empty")
    
    _check_missing_field(first_record, "receiptHandle")
    receipt_handle = first_record["receiptHandle"]
    
    _check_missing_field(first_record, "messageId")
    message_id = first_record["messageId"]
    
    return {
        "message_id": message_id,
        "receipt_handle": receipt_handle
    }


def get_secret_from_name(secret_name, kv=True):
    """Return secret from secret name

    :param secret_name: String
    :param kv: Boolean (weather it is json or not)
    
    :raises: botocore.exceptions.ClientError
    
    :rtype: Dictionary
    """
    session = boto3.session.Session()

    # Initializing Secret Manager's client    
    client = session.client(
        service_name='secretsmanager',
            region_name=os.environ.get("AWS_REGION", session.region_name)
        )
    LOGGER.info(f"Attempting to get secret value for: {secret_name}")
    try:
        get_secret_value_response = client.get_secret_value(
                SecretId=secret_name)
    except ClientError as e:
        # For a list of exceptions thrown, see
        # https://docs.aws.amazon.com/secretsmanager/latest/apireference/API_GetSecretValue.html
        LOGGER.error("Unable to fetch details from Secrets Manager")
        raise e
    
    _check_missing_field(
        get_secret_value_response, "SecretString")
    
    if kv:
        return json.loads(
            get_secret_value_response["SecretString"])
    else:
        return get_secret_value_response["SecretString"]


def _load_processed_text_document(bucket: str, key: str):
    """Load a processed text file from S3 and split into chunked LangChain Documents."""
    s3_client = boto3.client("s3")
    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        body_bytes = response["Body"].read()
    finally:
        s3_client.close()

    # Processed artifacts are plain text files generated by pdf-processor.
    text = body_bytes.decode("utf-8", errors="replace")
    all_image_uris = [
        uri for uri in sorted({_normalize_s3_uri(u) for u in IMAGE_URI_PATTERN.findall(text)})
        if uri and uri.lower().endswith(IMAGE_EXTENSIONS)
    ][:MAX_IMAGE_URIS_PER_DOC]
    image_uri_header = ""
    if all_image_uris:
        image_uri_header = "\n".join(
            [f"[Related Image URI] {uri}" for uri in all_image_uris[:MAX_IMAGE_URIS_EMBEDDED_PER_CHUNK]]
        )

    if not text:
        return [Document(page_content="", metadata={"source": f"s3://{bucket}/{key}", "chunk": 1})]

    # Keep each embedding payload safely below model request limits.
    chunk_size = MAX_EMBED_CHARS
    overlap = min(CHUNK_OVERLAP_CHARS, chunk_size // 2)
    step = max(1, chunk_size - overlap)

    docs = []
    chunk_index = 1
    for start in range(0, len(text), step):
        chunk = text[start:start + chunk_size]
        if not chunk:
            break
        if image_uri_header:
            chunk = f"{image_uri_header}\n\n{chunk}"
        image_uris = sorted({_normalize_s3_uri(u) for u in IMAGE_URI_PATTERN.findall(chunk) if _normalize_s3_uri(u)})
        docs.append(
            Document(
                page_content=chunk,
                metadata={
                    "source": f"s3://{bucket}/{key}",
                    "chunk": chunk_index,
                    "image_uris": all_image_uris,
                    "chunk_image_uris": image_uris,
                },
            )
        )
        chunk_index += 1

    return docs


def _iter_doc_batches(docs, max_docs=MAX_EMBED_BATCH_DOCS, max_total_chars=MAX_EMBED_BATCH_TOTAL_CHARS):
    """Yield document batches constrained by both count and total characters."""
    batch = []
    total_chars = 0

    for doc in docs:
        doc_chars = len(doc.page_content or "")
        if should_flush := batch and (
            len(batch) >= max_docs or (total_chars + doc_chars) > max_total_chars
        ):
            yield batch
            batch = []
            total_chars = 0

        batch.append(doc)
        total_chars += doc_chars

    if batch:
        yield batch


def _delete_sqs_message(queue_url, msg_attr):
    """Delete SQS message after successful document indexing."""
    LOGGER.info(
        "Deleting message %s from SQS after successful indexing",
        msg_attr["message_id"],
    )
    sqs_client = boto3.client("sqs")
    try:
        deletion_resp = sqs_client.delete_message(
            QueueUrl=queue_url,
            ReceiptHandle=msg_attr["receipt_handle"],
        )
    finally:
        sqs_client.close()

    resp_metadata = deletion_resp.get("ResponseMetadata")
    if not resp_metadata:
        raise Exception("No response metadata from deletion call")

    status_code = resp_metadata.get("HTTPStatusCode")
    if status_code == 200:
        LOGGER.info("Successfully deleted message")
    else:
        raise Exception("Unable to delete message")


def _is_throttling_error(err: Exception) -> bool:
    """Best-effort detection for Bedrock throttling exceptions."""
    err_text = str(err).lower()
    return "throttlingexception" in err_text or "too many requests" in err_text


def _index_batch_with_retry(index_callable, batch_num: int):
    """Run indexing for one batch with exponential backoff on throttling."""
    attempt = 0
    while True:
        try:
            return index_callable()
        except Exception as err:
            attempt += 1
            if not _is_throttling_error(err) or attempt > MAX_THROTTLE_RETRIES:
                raise

            sleep_seconds = min(
                THROTTLE_BACKOFF_MAX_SECONDS,
                THROTTLE_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)),
            ) + random.uniform(0.0, 0.35)
            LOGGER.warning(
                "Bedrock throttled on batch %s (attempt %s/%s). Backing off for %.2fs",
                batch_num,
                attempt,
                MAX_THROTTLE_RETRIES,
                sleep_seconds,
            )
            time.sleep(sleep_seconds)


def lambda_handler(event, context):
    """What executes when the program is run"""

    # configure python logger for Lambda
    _configure_logger()
    # silence chatty libraries for better logging
    _silence_noisy_loggers()

    msg_attr = _get_sqs_message_attributes(event)
    queue_url = None
    if msg_attr:
        queue_url = os.environ.get(SQS_QUEUE_ENV_VAR)
        if not queue_url:
            raise MissingEnvironmentVariable(
                f"{SQS_QUEUE_ENV_VAR} environment variable is required")

    body = _get_message_body(event)

    _check_missing_field(body, "bucket")
    _check_missing_field(body, "file")

    aoss_id = os.environ.get(AOSS_ID_ENV_VAR)
    if not aoss_id:
        raise MissingEnvironmentVariable(
            f"{AOSS_ID_ENV_VAR} environment variable is required")

    aoss_region = os.environ.get(AOSS_AWS_REGION_ENV_VAR)
    if not aoss_region:
        raise MissingEnvironmentVariable(
            f"{AOSS_AWS_REGION_ENV_VAR} environment variable is required")    

    index_name = os.environ.get(AOSS_INDEX_NAME_ENV_VAR)
    if not index_name:
        raise MissingEnvironmentVariable(
            f"{AOSS_INDEX_NAME_ENV_VAR} environment variable is required")
    
    LOGGER.info("Fetching Bedrock Titan embeddings")
    embeddings = BedrockEmbeddings(
        region_name=aoss_region,
        model_id=BEDROCK_EMBEDDING_MODEL_ID,
    )

    file_key = unquote_plus(body['file'])
    if not _is_indexable_key(file_key):
        LOGGER.info("Skipping non-text object for indexing: %s", file_key)
        if msg_attr and queue_url:
            _delete_sqs_message(queue_url, msg_attr)
        return

    LOGGER.info(
        f"Loading processed text document: {file_key} from bucket: {body['bucket']}")
    docs = _load_processed_text_document(body['bucket'], file_key)

    LOGGER.info("Setting up auth for OpenSearch Serverless")
    auth = AWSV4SignerAuth(
        boto3.Session().get_credentials(),
        aoss_region,
        AOSS_SVC_NAME
    )

    LOGGER.info("Adding new document to the vector store")
    if not docs:
        LOGGER.warning("No chunks were generated from %s; skipping indexing", file_key)
        return

    docsearch = None
    indexed_chunks = 0
    for batch_num, batch_docs in enumerate(_iter_doc_batches(docs), start=1):
        batch_chars = sum(len(doc.page_content or "") for doc in batch_docs)
        LOGGER.info(
            "Indexing batch %s with %s chunks and %s chars",
            batch_num,
            len(batch_docs),
            batch_chars,
        )

        if docsearch is None:
            docsearch = _index_batch_with_retry(
                lambda: OpenSearchVectorSearch.from_documents(
                    batch_docs,
                    embeddings,
                    opensearch_url=f"{aoss_id}.{aoss_region}.{AOSS_SVC_NAME}.amazonaws.com:443",
                    http_auth=auth,
                    timeout=DEFAULT_TIMEOUT_AOSS,
                    use_ssl=True,
                    verify_certs=True,
                    connection_class = RequestsHttpConnection,
                    index_name=index_name,
                    engine=DEFAULT_AOSS_ENGINE,
                ),
                batch_num,
            )
        else:
            _index_batch_with_retry(lambda: docsearch.add_documents(batch_docs), batch_num)

        indexed_chunks += len(batch_docs)
        time.sleep(BATCH_PACING_SECONDS)

    LOGGER.info("Successfully indexed %s chunks for %s", indexed_chunks, file_key)

    if msg_attr and queue_url:
        _delete_sqs_message(queue_url, msg_attr)
