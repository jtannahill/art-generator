import * as cdk from 'aws-cdk-lib';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as acm from 'aws-cdk-lib/aws-certificatemanager';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as sfn from 'aws-cdk-lib/aws-stepfunctions';
import * as tasks from 'aws-cdk-lib/aws-stepfunctions-tasks';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import * as path from 'path';

export class ArtGeneratorStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // S3 bucket — source data, outputs, and static site
    const bucket = new s3.Bucket(this, 'ArtBucket', {
      bucketName: 'art-generator-216890068001',
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
    });

    // DynamoDB table — palette records and artwork metadata
    const table = new dynamodb.Table(this, 'ArtTable', {
      tableName: 'art-generator',
      partitionKey: { name: 'PK', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'SK', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // ACM certificate for art.jamestannahill.com (DNS validation via Cloudflare)
    const certificate = new acm.Certificate(this, 'ArtCert', {
      domainName: 'art.jamestannahill.com',
      validation: acm.CertificateValidation.fromDns(),
    });

    // CloudFront distribution
    const distribution = new cloudfront.Distribution(this, 'ArtDistribution', {
      domainNames: ['art.jamestannahill.com'],
      certificate,
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(bucket, {
          originPath: '/site',
        }),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
      },
      defaultRootObject: 'index.html',
      errorResponses: [
        { httpStatus: 404, responsePagePath: '/index.html', responseHttpStatus: 200 },
      ],
    });

    // Output CloudFront domain for Cloudflare CNAME setup
    new cdk.CfnOutput(this, 'DistributionDomain', {
      value: distribution.distributionDomainName,
      description: 'Point art.jamestannahill.com CNAME to this',
    });

    // === Lambda Layer for CairoSVG ===
    // Built via layers/cairosvg/build.sh before deploy
    const cairoLayer = new lambda.LayerVersion(this, 'CairoSvgLayer', {
      code: lambda.Code.fromAsset(path.join(__dirname, '../../layers/cairosvg/dist')),
      compatibleRuntimes: [lambda.Runtime.PYTHON_3_12],
      description: 'CairoSVG + Cairo C binaries for SVG to PNG rendering',
    });

    // === Lambdas ===

    // Shared Bedrock policy
    const bedrockPolicy = new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel'],
      resources: ['arn:aws:bedrock:us-east-1::foundation-model/us.anthropic.claude-sonnet-4-6-20250514'],
    });

    // Weather Ingest
    const weatherIngest = new lambda.Function(this, 'WeatherIngest', {
      functionName: 'art-weather-ingest',
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../lambdas/weather_ingest')),
      timeout: cdk.Duration.minutes(5),
      memorySize: 1024,
      environment: {
        BUCKET_NAME: bucket.bucketName,
      },
    });
    bucket.grantReadWrite(weatherIngest);

    // Weather Render
    const weatherRender = new lambda.Function(this, 'WeatherRender', {
      functionName: 'art-weather-render',
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../lambdas/weather_render')),
      timeout: cdk.Duration.minutes(5),
      memorySize: 512,
      layers: [cairoLayer],
      environment: {
        BUCKET_NAME: bucket.bucketName,
        TABLE_NAME: table.tableName,
      },
    });
    bucket.grantReadWrite(weatherRender);
    table.grantWriteData(weatherRender);
    weatherRender.addToRolePolicy(bedrockPolicy);

    // Satellite Ingest
    const satelliteIngest = new lambda.Function(this, 'SatelliteIngest', {
      functionName: 'art-satellite-ingest',
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../lambdas/satellite_ingest')),
      timeout: cdk.Duration.minutes(5),
      memorySize: 512,
      environment: {
        BUCKET_NAME: bucket.bucketName,
        COPERNICUS_CLIENT_ID: '',
        COPERNICUS_CLIENT_SECRET: '',
      },
    });
    bucket.grantReadWrite(satelliteIngest);

    // Satellite Palette
    const satellitePalette = new lambda.Function(this, 'SatellitePalette', {
      functionName: 'art-palette-extract',
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../lambdas/satellite_palette')),
      timeout: cdk.Duration.minutes(3),
      memorySize: 512,
      layers: [cairoLayer],
      environment: {
        BUCKET_NAME: bucket.bucketName,
        TABLE_NAME: table.tableName,
      },
    });
    bucket.grantReadWrite(satellitePalette);
    table.grantWriteData(satellitePalette);
    satellitePalette.addToRolePolicy(bedrockPolicy);

    // Site Rebuild
    const siteRebuild = new lambda.Function(this, 'SiteRebuild', {
      functionName: 'art-site-rebuild',
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../lambdas/site_rebuild')),
      timeout: cdk.Duration.minutes(5),
      memorySize: 256,
      environment: {
        BUCKET_NAME: bucket.bucketName,
        TABLE_NAME: table.tableName,
        DISTRIBUTION_ID: distribution.distributionId,
      },
    });
    bucket.grantReadWrite(siteRebuild);
    table.grantReadData(siteRebuild);
    siteRebuild.addToRolePolicy(new iam.PolicyStatement({
      actions: ['cloudfront:CreateInvalidation'],
      resources: [`arn:aws:cloudfront::216890068001:distribution/${distribution.distributionId}`],
    }));

    // === Step Function ===

    const weatherIngestTask = new tasks.LambdaInvoke(this, 'WeatherIngestTask', {
      lambdaFunction: weatherIngest,
      outputPath: '$.Payload',
    });

    const weatherRenderMap = new sfn.Map(this, 'WeatherRenderMap', {
      maxConcurrency: 5,
      itemsPath: '$.regions',
    });
    weatherRenderMap.itemProcessor(
      new tasks.LambdaInvoke(this, 'WeatherRenderTask', {
        lambdaFunction: weatherRender,
        outputPath: '$.Payload',
      })
    );

    const satelliteIngestTask = new tasks.LambdaInvoke(this, 'SatelliteIngestTask', {
      lambdaFunction: satelliteIngest,
      outputPath: '$.Payload',
    });

    const paletteExtractMap = new sfn.Map(this, 'PaletteExtractMap', {
      maxConcurrency: 5,
      itemsPath: '$.locations',
    });
    paletteExtractMap.itemProcessor(
      new tasks.LambdaInvoke(this, 'PaletteExtractTask', {
        lambdaFunction: satellitePalette,
        outputPath: '$.Payload',
      })
    );

    const siteRebuildTask = new tasks.LambdaInvoke(this, 'SiteRebuildTask', {
      lambdaFunction: siteRebuild,
      outputPath: '$.Payload',
    });

    const weatherBranch = weatherIngestTask.next(weatherRenderMap);
    const satelliteBranch = satelliteIngestTask.next(paletteExtractMap);

    const parallel = new sfn.Parallel(this, 'ParallelPipelines');
    parallel.branch(weatherBranch);
    parallel.branch(satelliteBranch);

    const definition = parallel.next(siteRebuildTask);

    const stateMachine = new sfn.StateMachine(this, 'DailyPipeline', {
      stateMachineName: 'art-daily-pipeline',
      definitionBody: sfn.DefinitionBody.fromChainable(definition),
      timeout: cdk.Duration.minutes(30),
    });

    // EventBridge daily trigger at 06:00 UTC
    new events.Rule(this, 'DailyTrigger', {
      ruleName: 'art-daily-trigger',
      schedule: events.Schedule.cron({ hour: '6', minute: '0' }),
      targets: [new targets.SfnStateMachine(stateMachine)],
    });
  }
}
