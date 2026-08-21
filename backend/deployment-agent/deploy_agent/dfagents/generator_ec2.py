from __future__ import annotations

from .generator_shared import *
from .generator_shared import _EAGER_CONNECT, _LAZY_CONNECT, generator_class


class GeneratorEc2Mixin:
    @staticmethod
    def _bootstrap_template(service: ServiceSpec, plan: DeploymentPlan | None = None) -> str:
        sizing = (plan.aws_sizing if plan else None) or {}
        instance_type = str(sizing.get("instance_type", "t3.micro"))
        template = textwrap.dedent(
            """
            AWSTemplateFormatVersion: '2010-09-09'
            Description: Deployment Agent bootstrap infrastructure for a Next.js application on a single EC2 instance

            Parameters:
              ProjectSlug:
                Type: String
                AllowedPattern: '[a-z0-9-]+'
              GitHubSubjects:
                Type: CommaDelimitedList
                Description: Exact classic and immutable GitHub OIDC subjects for the authenticated repository and default branch
              AppPort:
                Type: Number
                Default: 3000
              InstanceType:
                Type: String
                Default: __INSTANCE_TYPE__
                AllowedValues: [t3.micro, t3.small, t3.medium, t4g.micro, t4g.small]
              LatestAmiId:
                Type: 'AWS::SSM::Parameter::Value<AWS::EC2::Image::Id>'
                Default: /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64
              ExistingOidcProviderArn:
                Type: String
                Default: ''
              ExistingVpcId:
                Type: String
                Default: ''
                Description: Optional existing VPC to reuse when the regional VPC quota is exhausted
              ExistingSubnetA:
                Type: String
                Default: ''

            Conditions:
              CreateOidcProvider: !Equals [!Ref ExistingOidcProviderArn, '']
              CreateNetwork: !Equals [!Ref ExistingVpcId, '']

            Resources:
              Vpc:
                Type: AWS::EC2::VPC
                Condition: CreateNetwork
                Properties:
                  CidrBlock: 10.42.0.0/16
                  EnableDnsHostnames: true
                  EnableDnsSupport: true
                  Tags: [{Key: Name, Value: !Sub '${ProjectSlug}-vpc'}]
              InternetGateway:
                Type: AWS::EC2::InternetGateway
                Condition: CreateNetwork
              GatewayAttachment:
                Type: AWS::EC2::VPCGatewayAttachment
                Condition: CreateNetwork
                Properties: {VpcId: !Ref Vpc, InternetGatewayId: !Ref InternetGateway}
              PublicSubnetA:
                Type: AWS::EC2::Subnet
                Condition: CreateNetwork
                Properties:
                  VpcId: !Ref Vpc
                  CidrBlock: 10.42.0.0/24
                  AvailabilityZone: !Select [0, !GetAZs '']
                  MapPublicIpOnLaunch: true
                  Tags: [{Key: Name, Value: !Sub '${ProjectSlug}-public-a'}]
              PublicRouteTable:
                Type: AWS::EC2::RouteTable
                Condition: CreateNetwork
                Properties: {VpcId: !Ref Vpc}
              DefaultRoute:
                Type: AWS::EC2::Route
                Condition: CreateNetwork
                DependsOn: GatewayAttachment
                Properties:
                  RouteTableId: !Ref PublicRouteTable
                  DestinationCidrBlock: 0.0.0.0/0
                  GatewayId: !Ref InternetGateway
              SubnetARouteAssociation:
                Type: AWS::EC2::SubnetRouteTableAssociation
                Condition: CreateNetwork
                Properties: {SubnetId: !Ref PublicSubnetA, RouteTableId: !Ref PublicRouteTable}
              InstanceSecurityGroup:
                Type: AWS::EC2::SecurityGroup
                Properties:
                  GroupDescription: Public HTTP access to the nginx reverse proxy
                  VpcId: !If [CreateNetwork, !Ref Vpc, !Ref ExistingVpcId]
                  SecurityGroupIngress:
                    - {IpProtocol: tcp, FromPort: 80, ToPort: 80, CidrIp: 0.0.0.0/0, Description: Public HTTP}
                  Tags: [{Key: Name, Value: !Sub '${ProjectSlug}-instance'}]
              ArtifactBucket:
                Type: AWS::S3::Bucket
                Properties:
                  BucketName: !Sub '${ProjectSlug}-artifacts-${AWS::AccountId}-${AWS::Region}'
                  BucketEncryption:
                    ServerSideEncryptionConfiguration:
                      - ServerSideEncryptionByDefault: {SSEAlgorithm: AES256}
                  PublicAccessBlockConfiguration:
                    BlockPublicAcls: true
                    BlockPublicPolicy: true
                    IgnorePublicAcls: true
                    RestrictPublicBuckets: true
                  VersioningConfiguration: {Status: Enabled}
                  LifecycleConfiguration:
                    Rules:
                      - Id: ExpireOldReleases
                        Status: Enabled
                        Prefix: releases/
                        ExpirationInDays: 30
                        NoncurrentVersionExpirationInDays: 7
              LogGroup:
                Type: AWS::Logs::LogGroup
                Properties:
                  LogGroupName: !Sub '/deployment-agent/${ProjectSlug}'
                  RetentionInDays: 30
              RuntimeSecret:
                Type: AWS::SecretsManager::Secret
                Properties:
                  Name: !Sub '/deployment-agent/${ProjectSlug}/runtime'
                  GenerateSecretString:
                    SecretStringTemplate: '{}'
                    GenerateStringKey: INITIAL_PLACEHOLDER
                    PasswordLength: 32
              InstanceRole:
                Type: AWS::IAM::Role
                Properties:
                  RoleName: !Sub '${ProjectSlug}-instance'
                  AssumeRolePolicyDocument:
                    Version: '2012-10-17'
                    Statement: [{Effect: Allow, Principal: {Service: ec2.amazonaws.com}, Action: 'sts:AssumeRole'}]
                  ManagedPolicyArns:
                    - arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
                  Policies:
                    - PolicyName: RuntimeAccess
                      PolicyDocument:
                        Version: '2012-10-17'
                        Statement:
                          - Effect: Allow
                            Action: ['s3:GetObject']
                            Resource: !Sub '${ArtifactBucket.Arn}/releases/*'
                          - Effect: Allow
                            Action: ['secretsmanager:GetSecretValue']
                            Resource: !Ref RuntimeSecret
                          - Effect: Allow
                            Action: ['logs:CreateLogStream', 'logs:PutLogEvents', 'logs:DescribeLogStreams']
                            Resource: !Sub '${LogGroup.Arn}:*'
              InstanceProfile:
                Type: AWS::IAM::InstanceProfile
                Properties:
                  Roles: [!Ref InstanceRole]
              AppInstance:
                Type: AWS::EC2::Instance
                Properties:
                  ImageId: !Ref LatestAmiId
                  InstanceType: !Ref InstanceType
                  IamInstanceProfile: !Ref InstanceProfile
                  SubnetId: !If [CreateNetwork, !Ref PublicSubnetA, !Ref ExistingSubnetA]
                  SecurityGroupIds: [!Ref InstanceSecurityGroup]
                  Tags:
                    - {Key: Name, Value: !Sub '${ProjectSlug}-app'}
                    - {Key: DeploymentAgentProject, Value: !Ref ProjectSlug}
                  BlockDeviceMappings:
                    - DeviceName: /dev/xvda
                      Ebs: {VolumeSize: 20, VolumeType: gp3, Encrypted: true, DeleteOnTermination: true}
                  UserData:
                    Fn::Base64: !Sub |
                      #!/bin/bash
                      set -euxo pipefail

                      dnf install -y nginx jq amazon-cloudwatch-agent
                      curl -fsSL https://rpm.nodesource.com/setup_20.x | bash -
                      dnf install -y nodejs

                      id -u nextjs >/dev/null 2>&1 || useradd --system --create-home --shell /sbin/nologin nextjs
                      mkdir -p /opt/app/releases /opt/app/shared /var/log/app
                      chown -R nextjs:nextjs /opt/app /var/log/app

                      # Placeholder until the first release lands.
                      printf 'NODE_ENV=production\\nPORT=${AppPort}\\nHOSTNAME=127.0.0.1\\n' > /opt/app/shared/.env
                      chown root:nextjs /opt/app/shared/.env
                      chmod 0640 /opt/app/shared/.env

                      cat > /etc/systemd/system/nextjs.service <<'UNIT'
                      [Unit]
                      Description=Next.js application
                      After=network-online.target
                      Wants=network-online.target

                      [Service]
                      Type=simple
                      User=nextjs
                      Group=nextjs
                      WorkingDirectory=/opt/app/current
                      EnvironmentFile=/opt/app/shared/.env
                      ExecStart=/usr/bin/node /opt/app/current/server.js
                      Restart=always
                      RestartSec=5
                      NoNewPrivileges=true
                      PrivateTmp=true
                      ProtectSystem=strict
                      ProtectHome=true
                      ReadWritePaths=/opt/app /var/log/app
                      # A file, not only the journal. The deployment agent
                      # scores 10 "monitoring" points for being able to read
                      # the application's own log lines back out of
                      # CloudWatch, and the journal alone never left the box:
                      # the group was created, the role could write to it, and
                      # nothing ever shipped a line, so every run finished at
                      # 90/100 with "the agent has not seen the application
                      # write anything it can read back".
                      StandardOutput=append:/var/log/app/app.log
                      StandardError=append:/var/log/app/app.log

                      [Install]
                      WantedBy=multi-user.target
                      UNIT

                      # The group name here has to stay in step with the
                      # LogGroup resource above and with the monitor, which
                      # reads /deployment-agent/<slug>.
                      mkdir -p /opt/aws/amazon-cloudwatch-agent/etc
                      cat > /opt/aws/amazon-cloudwatch-agent/etc/app-logs.json <<'CWAGENT'
                      {
                        "logs": {
                          "logs_collected": {
                            "files": {
                              "collect_list": [
                                {
                                  "file_path": "/var/log/app/app.log",
                                  "log_group_name": "/deployment-agent/${ProjectSlug}",
                                  "log_stream_name": "{instance_id}/app",
                                  "retention_in_days": 30
                                },
                                {
                                  "file_path": "/var/log/nginx/error.log",
                                  "log_group_name": "/deployment-agent/${ProjectSlug}",
                                  "log_stream_name": "{instance_id}/nginx-error",
                                  "retention_in_days": 30
                                }
                              ]
                            }
                          }
                        }
                      }
                      CWAGENT

                      # Best effort on purpose: a box that cannot ship logs
                      # should still serve the site. The run loses the 10
                      # monitoring points rather than the deployment.
                      /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
                        -a fetch-config -m ec2 -s \
                        -c file:/opt/aws/amazon-cloudwatch-agent/etc/app-logs.json || true

                      cat > /etc/nginx/conf.d/app.conf <<'NGINX'
                      server {
                        listen 80 default_server;
                        server_name _;
                        client_max_body_size 10m;

                        location / {
                          proxy_pass http://127.0.0.1:${AppPort};
                          proxy_http_version 1.1;
                          proxy_set_header Upgrade $http_upgrade;
                          proxy_set_header Connection 'upgrade';
                          proxy_set_header Host $host;
                          proxy_set_header X-Real-IP $remote_addr;
                          proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
                          proxy_set_header X-Forwarded-Proto $scheme;
                          proxy_cache_bypass $http_upgrade;
                          proxy_read_timeout 60s;
                        }
                      }
                      NGINX
                      rm -f /etc/nginx/conf.d/default.conf

                      systemctl daemon-reload
                      systemctl enable --now nginx
                      # nextjs is enabled but only starts once /opt/app/current exists.
                      systemctl enable nextjs
              ElasticIp:
                Type: AWS::EC2::EIP
                Properties:
                  Domain: vpc
                  Tags: [{Key: Name, Value: !Sub '${ProjectSlug}-eip'}]
              ElasticIpAssociation:
                Type: AWS::EC2::EIPAssociation
                Properties:
                  AllocationId: !GetAtt ElasticIp.AllocationId
                  InstanceId: !Ref AppInstance
              GitHubOidcProvider:
                Type: AWS::IAM::OIDCProvider
                Condition: CreateOidcProvider
                Properties:
                  Url: https://token.actions.githubusercontent.com
                  ClientIdList: [sts.amazonaws.com]
              GitHubDeployRole:
                Type: AWS::IAM::Role
                Properties:
                  RoleName: !Sub '${ProjectSlug}-github-deploy'
                  AssumeRolePolicyDocument:
                    Version: '2012-10-17'
                    Statement:
                      - Effect: Allow
                        Principal:
                          Federated: !If [CreateOidcProvider, !Ref GitHubOidcProvider, !Ref ExistingOidcProviderArn]
                        Action: 'sts:AssumeRoleWithWebIdentity'
                        Condition:
                          StringEquals:
                            'token.actions.githubusercontent.com:aud': sts.amazonaws.com
                            'token.actions.githubusercontent.com:sub': !Ref GitHubSubjects
                  Policies:
                    - PolicyName: ProjectDeployment
                      PolicyDocument:
                        Version: '2012-10-17'
                        Statement:
                          - Effect: Allow
                            Action: ['s3:PutObject', 's3:GetObject', 's3:AbortMultipartUpload']
                            Resource: !Sub '${ArtifactBucket.Arn}/releases/*'
                          - Effect: Allow
                            Action: ['s3:ListBucket']
                            Resource: !GetAtt ArtifactBucket.Arn
                          - Effect: Allow
                            Action: ['ssm:SendCommand']
                            Resource:
                              - !Sub 'arn:${AWS::Partition}:ssm:${AWS::Region}::document/AWS-RunShellScript'
                              - !Sub 'arn:${AWS::Partition}:ec2:${AWS::Region}:${AWS::AccountId}:instance/${AppInstance}'
                          - Effect: Allow
                            Action: ['ssm:GetCommandInvocation', 'ssm:ListCommandInvocations', 'ssm:ListCommands']
                            Resource: '*'
                          - Effect: Allow
                            Action: ['cloudformation:DescribeStacks']
                            Resource: !Sub 'arn:${AWS::Partition}:cloudformation:${AWS::Region}:${AWS::AccountId}:stack/${ProjectSlug}-*/*'
                          - Effect: Allow
                            Action: ['ec2:DescribeInstances', 'ec2:DescribeInstanceStatus']
                            Resource: '*'

            Outputs:
              ApplicationUrl:
                Value: !Sub 'http://${ElasticIp}'
              ArtifactBucket: {Value: !Ref ArtifactBucket}
              InstanceId: {Value: !Ref AppInstance}
              PublicIp: {Value: !Ref ElasticIp}
              DeployRoleArn: {Value: !GetAtt GitHubDeployRole.Arn}
              RuntimeSecretArn: {Value: !Ref RuntimeSecret}
              LogGroupName: {Value: !Ref LogGroup}
              InstanceSecurityGroup: {Value: !Ref InstanceSecurityGroup}
            """
        ).lstrip()
        return template.replace("__INSTANCE_TYPE__", instance_type)
