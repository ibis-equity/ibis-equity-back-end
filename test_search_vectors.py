#!/usr/bin/env python3
import os
import boto3
from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth

aoss_id = os.environ.get('AOSS_ID', 'b93npune3dx56yo61f5d')
aoss_region = os.environ.get('AOSS_AWS_REGION', 'us-east-1')
index_name = os.environ.get('AOSS_INDEX_NAME', 'rag-oai-index')

credentials = boto3.Session().get_credentials()
awsauth = AWSV4SignerAuth(credentials, aoss_region, 'aoss')

client = OpenSearch(
    hosts=[{'host': f'{aoss_id}.{aoss_region}.aoss.amazonaws.com', 'port': 443}],
    http_auth=awsauth,
    use_ssl=True,
    verify_certs=True,
    connection_class=RequestsHttpConnection,
    timeout=30
)

# Search for documents containing "Vector Drawing"
query = {
    "size": 2,
    "_source": ["metadata", "page_content", "text"],  # Explicitly request these fields
    "query": {
        "match": {
            "text": "Vector Drawing"
        }
    }
}

try:
    print("Searching for 'Vector Drawing' in text field...")
    response = client.search(index=index_name, body=query)
    hits = response.get('hits', {}).get('hits', [])
    print(f"Found {len(hits)} documents containing 'Vector Drawing'")
    
    if len(hits) == 0:
        print("\nNo documents found. Trying different search...")
        # Try searching in page_content
        query2 = {
            "size": 1,
            "query": {
                "match_all": {}
            }
        }
        response = client.search(index=index_name, body=query2)
        hits = response.get('hits', {}).get('hits', [])
        if hits:
            doc = hits[0]['_source']
            print(f"\nDocument keys: {list(doc.keys())}")
            for key in doc:
                if key != 'vector_field':
                    print(f"\n{key}: {str(doc[key])[:500]}")
    else:
        for i, hit in enumerate(hits):
            src = hit['_source']
            print(f"\nHit {i+1} keys: {list(src.keys())}")
            for key in src:
                if key != 'vector_field':
                    print(f"{key}: {str(src[key])[:300]}")
                    
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
