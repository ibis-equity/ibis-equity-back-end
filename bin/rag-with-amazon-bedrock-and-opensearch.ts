#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { RagAppStack } from '../lib/rag-app-stack';
import { BaseInfraStack } from '../lib/base-infra-stack';
import { TestComputeStack } from '../lib/test-compute-stack';
import { OpenSearchStack } from '../lib/opensearch-stack';
import { OpenSearchUpdateStack } from '../lib/aoss-update-stack';
import { RagQueryStack } from '../lib/rag-query-stack';

const app = new cdk.App();

const cdkAccount = process.env.CDK_DEFAULT_ACCOUNT;
const cdkRegion = process.env.CDK_DEFAULT_REGION || 'us-east-1';

if (!cdkAccount) {
  throw new Error('Missing CDK_DEFAULT_ACCOUNT. Set it before deploy, for example: $env:CDK_DEFAULT_ACCOUNT="<aws-account-id>"');
}

const deploymentEnv: cdk.Environment = {
  account: cdkAccount,
  region: cdkRegion,
};

// contains vpc, 
const baseInfra = new BaseInfraStack(app, 'BaseInfraStack', {
  env: deploymentEnv,
});


// for a test EC2 instance to play around with (optional)
const testComputeStack = new TestComputeStack(app, 'TestComputeStack', {
  env: deploymentEnv,
  vpc: baseInfra.vpc,
  ec2SG: baseInfra.ec2SecGroup,
});

// OpenSearch Serverless Creation. TODO: fix the stackname to be consistent
const opensearchStack = new OpenSearchStack(app, 'OpenSearchStack', {
  env: deploymentEnv,
  testComputeHostRole: testComputeStack.hostRole,
  lambdaRole: baseInfra.aossUpdateLambdaRole,
  ecsTaskRole: baseInfra.ecsTaskRole
});

// lambda function to update the aoss index upon new document landing
const aossUpdateStack = new OpenSearchUpdateStack(app, 'aossUpdateStack', {
  env: deploymentEnv,
  processedBucket: baseInfra.processedBucket,
  indexName: baseInfra.aossIndexName,
  apiKeySecret: baseInfra.apiKeySecret,
  triggerQueue: baseInfra.aossQueue,
  lambdaRole: baseInfra.aossUpdateLambdaRole,
  aossHost: opensearchStack.serverlessCollection.attrId
});

// API endpoint for ibis_equity_site frontend integration
const ragQueryStack = new RagQueryStack(app, 'ragQueryStack', {
  env: deploymentEnv,
  indexName: baseInfra.aossIndexName,
  apiKeySecret: baseInfra.apiKeySecret,
  aossHost: opensearchStack.serverlessCollection.attrId,
});

// ecs service
const ragApp = new RagAppStack(app, 'ragStack', {
  env: deploymentEnv,
  vpc: baseInfra.vpc,
  indexName: baseInfra.aossIndexName,
  apiKeySecret: baseInfra.apiKeySecret,
  taskSecGroup: baseInfra.ecsTaskSecGroup,
  aossHost: opensearchStack.serverlessCollection.attrId,
  elbTargetGroup: baseInfra.appTargetGroup,
  taskRole: baseInfra.ecsTaskRole
});
