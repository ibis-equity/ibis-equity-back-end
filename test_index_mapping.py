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

# Get index mapping
print("Index Settings and Mappings:")
try:
    mapping = client.indices.get_mapping(index=index_name)
    import json
    print(json.dumps(mapping, indent=2))
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()

# Also check one document to see its actual structure
print("\n\nExample Document from Index:")
try:
    response = client.search(index=index_name, body={"size": 1, "query": {"match_all": {}}})
    if response['hits']['hits']:
        doc = response['hits']['hits'][0]['_source']
        import json
        print(json.dumps(doc, indent=2, default=str))
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
