#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { ArtGeneratorStack } from '../lib/art-generator-stack';

const app = new cdk.App();
new ArtGeneratorStack(app, 'ArtGeneratorStack', {
  env: { account: '216890068001', region: 'us-east-1' },
});
