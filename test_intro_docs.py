#!/usr/bin/env python3
import os, boto3, json
from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth

aoss_id = 'b93npune3dx56yo61f5d'
aoss_region = 'us-east-1'
index = 'rag-oai-index'
creds = boto3.Session().get_credentials()
client = OpenSearch(
    hosts=[{'host': f'{aoss_id}.{aoss_region}.aoss.amazonaws.com', 'port': 443}],
    http_auth=AWSV4SignerAuth(creds, aoss_region, 'aoss'),
    use_ssl=True, verify_certs=True, connection_class=RequestsHttpConnection, timeout=30
)

# Count all documents
resp = client.count(index=index)
print(f'Total documents in index: {resp["count"]}')

# Get documents from introduction_to_datascience
resp = client.search(index=index, body={'size': 20, 'query': {'match': {'metadata.source.keyword': 's3://baseinfrastack-knowledgebaseb1b5b9dc-96defku0hgyp/introduction_to_datascience.pdf'}}})
print(f'\nDocs from intro pdf (exact source match): {len(resp["hits"]["hits"])}')

# Try text field search
resp2 = client.search(index=index, body={'size': 20, 'query':{'match': {'text': 'introduction_to_datascience'}}})
print(f'Docs matching "introduction_to_datascience" in text: {len(resp2["hits"]["hits"])}')

# Look at chunks that have chunk_image_uris
has_image_uris = 0
for hit in resp2['hits']['hits']:
    meta = hit['_source'].get('metadata', {})
    chunk_uris = meta.get('chunk_image_uris', [])
    if chunk_uris:
        has_image_uris += 1
        print(f'  Chunk {meta.get("chunk")}: {len(chunk_uris)} image_uris')
        if has_image_uris >= 3:
            break

print(f'\nTotal chunks with image_uris from intro_to_datascience: {len([h for h in resp2["hits"]["hits"] if h["_source"].get("metadata", {}).get("chunk_image_uris")])}')
