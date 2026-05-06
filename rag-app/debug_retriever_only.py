import os
import boto3
from langchain_aws import BedrockEmbeddings
from langchain_community.vectorstores import OpenSearchVectorSearch
from opensearchpy import RequestsHttpConnection, AWSV4SignerAuth

AOSS_SVC_NAME = "aoss"
TEXT_DOC_FILTER = {"wildcard": {"metadata.source.keyword": "*.txt"}}

region = os.environ["AWS_REGION"]
aoss_id = os.environ["AOSS_ID"]
index_name = os.environ["AOSS_INDEX_NAME"]

embeddings = BedrockEmbeddings(region_name=region, model_id="amazon.titan-embed-text-v2:0")

docsearch = OpenSearchVectorSearch(
    f"https://{aoss_id}.{region}.{AOSS_SVC_NAME}.amazonaws.com:443",
    index_name,
    embeddings,
    http_auth=AWSV4SignerAuth(boto3.Session().get_credentials(), region, AOSS_SVC_NAME),
    timeout=100,
    use_ssl=True,
    verify_certs=True,
    connection_class=RequestsHttpConnection,
)

query = "explain Generic Process Model"

for k in [6, 12, 20]:
    print(f"\n=== k={k} ===")
    docs = docsearch.similarity_search(query=query, k=k, filter=TEXT_DOC_FILTER)
    print("retrieved:", len(docs))
    for i, d in enumerate(docs[:8], 1):
        md = d.metadata or {}
        source = md.get("source")
        img = len(md.get("image_uris") or [])
        cimg = len(md.get("chunk_image_uris") or [])
        print(i, source, "chunk=", md.get("chunk"), "img=", img, "chunk_img=", cimg)
