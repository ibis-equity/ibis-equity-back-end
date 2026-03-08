import * as cdk from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import { RagQueryStack } from '../lib/rag-query-stack';

function buildRagQueryTemplate() {
	const app = new cdk.App();
	const env = { account: '111111111111', region: 'us-east-1' };
	const supportStack = new cdk.Stack(app, 'SupportStack', { env });
	const apiKeySecret = new secretsmanager.Secret(supportStack, 'ApiKeySecret');

	const ragQueryStack = new RagQueryStack(app, 'RagQueryTestStack', {
		env,
		indexName: 'rag-oai-index',
		apiKeySecret,
		aossHost: 'b93npune3dx56yo61f5d',
	});

	return Template.fromStack(ragQueryStack);
}

describe('RagQueryStack', () => {
	const previousFunctionUrl = process.env.RAG_QUERY_ENABLE_FUNCTION_URL;

	afterEach(() => {
		if (previousFunctionUrl === undefined) {
			delete process.env.RAG_QUERY_ENABLE_FUNCTION_URL;
		} else {
			process.env.RAG_QUERY_ENABLE_FUNCTION_URL = previousFunctionUrl;
		}
	});

	test('configures lambda with OpenSearch and Polly environment defaults', () => {
		delete process.env.RAG_QUERY_ENABLE_FUNCTION_URL;
		const template = buildRagQueryTemplate();

		template.hasResourceProperties('AWS::Lambda::Function', {
			Timeout: 120,
			MemorySize: 1024,
			Environment: {
				Variables: Match.objectLike({
					AOSS_ID: 'b93npune3dx56yo61f5d',
					AOSS_INDEX_NAME: 'rag-oai-index',
					RAG_QUERY_ENABLE_POLLY: 'true',
					POLLY_VOICE_ID: 'Joanna',
					POLLY_ENGINE: 'standard',
					POLLY_LANGUAGE_CODE: 'en-US',
				}),
			},
		});
	});

	test('grants lambda role access to OpenSearch and Polly synthesis', () => {
		delete process.env.RAG_QUERY_ENABLE_FUNCTION_URL;
		const template = buildRagQueryTemplate();

		template.hasResourceProperties('AWS::IAM::Role', {
			AssumeRolePolicyDocument: {
				Statement: Match.arrayWith([
					Match.objectLike({
						Principal: { Service: 'lambda.amazonaws.com' },
					}),
				]),
			},
			Policies: Match.arrayWith([
				Match.objectLike({
					PolicyDocument: {
						Statement: Match.arrayWith([
							Match.objectLike({ Action: 'aoss:APIAccessAll', Effect: 'Allow' }),
						]),
					},
				}),
				Match.objectLike({
					PolicyDocument: {
						Statement: Match.arrayWith([
							Match.objectLike({ Action: 'polly:SynthesizeSpeech', Effect: 'Allow' }),
						]),
					},
				}),
			]),
		});
	});

	test('creates function url only when explicitly enabled', () => {
		process.env.RAG_QUERY_ENABLE_FUNCTION_URL = 'true';
		const template = buildRagQueryTemplate();
		template.resourceCountIs('AWS::Lambda::Url', 1);
	});
});
