#!/usr/bin/env python3
import os, boto3
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

# Get documents to see their source values
resp = client.search(index=index, body={'size': 50, 'query': {'match_all': {}}})
sources = set()
for hit in resp['hits']['hits']:
    src = hit['_source'].get('metadata', {}).get('source', 'UNKNOWN')
    sources.add(src)

print(f"Found {len(sources)} unique source values:")
for src in sorted(sources)[:20]:
    print(f"  - {src}")

# Now test the wildcard filter
print("\nTesting wildcard filter '*.txt':")
resp2 = client.search(index=index, body={
    'size': 3,
    'query': {
        'bool': {
            'filter': {
                'wildcard': {
                    'metadata.source.keyword': '*.txt'
                }
            }
        }
    }
})
print(f"  Found {len(resp2['hits']['hits'])} documents")
for hit in resp2['hits']['hits']:
    print(f"    - {hit['_source'].get('metadata', {}).get('source')}")
