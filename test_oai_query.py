#!/usr/bin/env python3
import os
import boto3
import json
import sys
from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth

# Get environment and AWS connection
aoss_id = os.environ.get('AOSS_ID', 'b93npune3dx56yo61f5d')
aoss_region = os.environ.get('AOSS_AWS_REGION', 'us-east-1')
index_name = os.environ.get('AOSS_INDEX_NAME', 'rag-oai-index')

print(f"[INFO] Connecting to OpenSearch Serverless...")
print(f"  AOSS ID: {aoss_id}")
print(f"  Region: {aoss_region}")
print(f"  Index: {index_name}")

# Get AWS credentials
credentials = boto3.Session().get_credentials()
awsauth = AWSV4SignerAuth(credentials, aoss_region, 'aoss')

# Connect to OpenSearch
client = OpenSearch(
    hosts=[{
        'host': f'{aoss_id}.{aoss_region}.aoss.amazonaws.com',
        'port': 443
    }],
    http_auth=awsauth,
    use_ssl=True,
    verify_certs=True,
    connection_class=RequestsHttpConnection,
    timeout=30
)

# Query for introduction_to_datascience documents
query = {
    "size": 2,
    "query": {
        "match_all": {}
    }
}

print(f"\n[INFO] Querying OpenSearch...")
try:
    response = client.search(index=index_name, body=query)
    hits = response.get('hits', {}).get('hits', [])
    print(f"[RESULT] Found {len(hits)} hits (showing first 2)")
    
    for i, hit in enumerate(hits):
        print(f"\n--- Full Hit {i+1} ---")
        import json
        # Pretty print the entire source document
        print(json.dumps(hit.get('_source', {}), indent=2, default=str)[:1500])
        
except Exception as e:
    print(f"[ERROR] Query failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
