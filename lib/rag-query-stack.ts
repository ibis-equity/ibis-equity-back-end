import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import path = require('path');

export interface RagQueryStackProps extends cdk.StackProps {
  indexName: string;
  apiKeySecret: secretsmanager.Secret;
  aossHost: string;
}

export class RagQueryStack extends cdk.Stack {
  readonly ragQueryFunctionUrl?: lambda.FunctionUrl;

  constructor(scope: Construct, id: string, props: RagQueryStackProps) {
    super(scope, id, props);

    const dockerPlatform = process.env['DOCKER_CONTAINER_PLATFORM_ARCH'];
    const enableFunctionUrl = (process.env.RAG_QUERY_ENABLE_FUNCTION_URL || 'false').toLowerCase() === 'true';
    const publicFunctionUrl = (process.env.RAG_QUERY_PUBLIC_URL || 'false').toLowerCase() === 'true';
    const functionUrlAuthType = publicFunctionUrl
      ? lambda.FunctionUrlAuthType.NONE
      : lambda.FunctionUrlAuthType.AWS_IAM;
    const lambdaRole = new iam.Role(this, 'ragQueryLambdaRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      inlinePolicies: {
        basicExecutionLambda: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              effect: iam.Effect.ALLOW,
              actions: ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents'],
              resources: ['*'],
            }),
          ],
        }),
        aossApiAccess: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              effect: iam.Effect.ALLOW,
              actions: ['aoss:APIAccessAll'],
              resources: [`arn:aws:aoss:${this.region}:${this.account}:collection/*`],
            }),
          ],
        }),
        bedrockInvokePolicy: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              effect: iam.Effect.ALLOW,
              actions: ['bedrock:InvokeModel'],
              resources: ['*'],
            }),
          ],
        }),
        pollySynthesizePolicy: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              effect: iam.Effect.ALLOW,
              actions: ['polly:SynthesizeSpeech'],
              resources: ['*'],
            }),
          ],
        }),
      },
    });

    const ragQueryFn = new lambda.Function(this, 'ragQueryFn', {
      code: lambda.Code.fromAssetImage(path.join(__dirname, '../lambda/rag-query')),
      handler: lambda.Handler.FROM_IMAGE,
      runtime: lambda.Runtime.FROM_IMAGE,
      timeout: cdk.Duration.minutes(2),
      memorySize: 1024,
      role: lambdaRole,
      architecture:
        dockerPlatform == 'arm'
          ? lambda.Architecture.ARM_64
          : lambda.Architecture.X86_64,
      environment: {
        API_KEY_SECRET_NAME: props.apiKeySecret.secretName,
        AOSS_ID: props.aossHost,
        AOSS_INDEX_NAME: props.indexName,
        AOSS_AWS_REGION: `${this.region}`,
        ALLOWED_ORIGIN: process.env.IBIS_SITE_ORIGIN || '*',
        RAG_QUERY_ENABLE_POLLY: process.env.RAG_QUERY_ENABLE_POLLY || 'true',
        POLLY_VOICE_ID: process.env.POLLY_VOICE_ID || 'Joanna',
        POLLY_ENGINE: process.env.POLLY_ENGINE || 'standard',
        POLLY_LANGUAGE_CODE: process.env.POLLY_LANGUAGE_CODE || 'en-US',
      },
    });

    props.apiKeySecret.grantRead(ragQueryFn);

    if (enableFunctionUrl) {
      this.ragQueryFunctionUrl = ragQueryFn.addFunctionUrl({
        authType: functionUrlAuthType,
        cors: {
          allowedOrigins: [process.env.IBIS_SITE_ORIGIN || '*'],
          allowedMethods: [lambda.HttpMethod.POST, lambda.HttpMethod.OPTIONS],
          allowedHeaders: ['content-type', 'authorization'],
        },
      });

      new cdk.CfnOutput(this, 'RagQueryApiUrl', {
        value: this.ragQueryFunctionUrl.url,
        description: 'Public Function URL for ibis_equity_site RAG integration',
      });
    }
  }
}
