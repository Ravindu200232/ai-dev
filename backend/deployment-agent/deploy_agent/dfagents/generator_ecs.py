from __future__ import annotations

from .generator_shared import *
from .generator_shared import _EAGER_CONNECT, _LAZY_CONNECT, generator_class


class GeneratorEcsMixin:
    @staticmethod
    def _ecs_bootstrap_template(service: ServiceSpec, plan: DeploymentPlan | None = None) -> str:
        """ECR, a Fargate service, and a load balancer in front of it."""
        port = service.port or 3000
        health = service.health_path or "/api/health"
        return textwrap.dedent(
            f"""
            AWSTemplateFormatVersion: '2010-09-09'
            Description: Deployment Agent bootstrap infrastructure for a Next.js application on ECS Fargate

            Parameters:
              ProjectSlug:
                Type: String
                AllowedPattern: '[a-z0-9-]+'
              GitHubSubjects:
                Type: CommaDelimitedList
                Description: Exact classic and immutable GitHub OIDC subjects for the authenticated repository and default branch
              AppPort:
                Type: Number
                Default: {port}
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
              ExistingSubnetB:
                Type: String
                Default: ''
                Description: A load balancer requires two availability zones

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
                  Tags: [{{Key: Name, Value: !Sub '${{ProjectSlug}}-vpc'}}]
              InternetGateway:
                Type: AWS::EC2::InternetGateway
                Condition: CreateNetwork
              GatewayAttachment:
                Type: AWS::EC2::VPCGatewayAttachment
                Condition: CreateNetwork
                Properties:
                  VpcId: !Ref Vpc
                  InternetGatewayId: !Ref InternetGateway
              PublicSubnetA:
                Type: AWS::EC2::Subnet
                Condition: CreateNetwork
                Properties:
                  VpcId: !Ref Vpc
                  CidrBlock: 10.42.0.0/24
                  AvailabilityZone: !Select [0, !GetAZs '']
                  MapPublicIpOnLaunch: true
              PublicSubnetB:
                Type: AWS::EC2::Subnet
                Condition: CreateNetwork
                Properties:
                  VpcId: !Ref Vpc
                  CidrBlock: 10.42.1.0/24
                  AvailabilityZone: !Select [1, !GetAZs '']
                  MapPublicIpOnLaunch: true
              PublicRouteTable:
                Type: AWS::EC2::RouteTable
                Condition: CreateNetwork
                Properties:
                  VpcId: !Ref Vpc
              PublicRoute:
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
                Properties:
                  SubnetId: !Ref PublicSubnetA
                  RouteTableId: !Ref PublicRouteTable
              SubnetBRouteAssociation:
                Type: AWS::EC2::SubnetRouteTableAssociation
                Condition: CreateNetwork
                Properties:
                  SubnetId: !Ref PublicSubnetB
                  RouteTableId: !Ref PublicRouteTable

              # Only the load balancer faces the internet.
              LoadBalancerSecurityGroup:
                Type: AWS::EC2::SecurityGroup
                Properties:
                  GroupDescription: !Sub 'Public ingress for ${{ProjectSlug}}'
                  VpcId: !If [CreateNetwork, !Ref Vpc, !Ref ExistingVpcId]
                  SecurityGroupIngress:
                    - IpProtocol: tcp
                      FromPort: 80
                      ToPort: 80
                      CidrIp: 0.0.0.0/0
              # The task accepts traffic from the load balancer and from nothing
              # else. An open task security group would make the ALB decorative.
              TaskSecurityGroup:
                Type: AWS::EC2::SecurityGroup
                Properties:
                  GroupDescription: !Sub 'Task ingress for ${{ProjectSlug}}, load balancer only'
                  VpcId: !If [CreateNetwork, !Ref Vpc, !Ref ExistingVpcId]
                  SecurityGroupIngress:
                    - IpProtocol: tcp
                      FromPort: !Ref AppPort
                      ToPort: !Ref AppPort
                      SourceSecurityGroupId: !Ref LoadBalancerSecurityGroup

              EcrRepository:
                Type: AWS::ECR::Repository
                Properties:
                  RepositoryName: !Ref ProjectSlug
                  ImageScanningConfiguration: {{ScanOnPush: true}}
                  ImageTagMutability: IMMUTABLE
                  LifecyclePolicy:
                    LifecyclePolicyText: |
                      {{"rules":[{{"rulePriority":1,"description":"Keep the 10 most recent images",
                        "selection":{{"tagStatus":"any","countType":"imageCountMoreThan","countNumber":10}},
                        "action":{{"type":"expire"}}}}]}}

              LogGroup:
                Type: AWS::Logs::LogGroup
                Properties:
                  LogGroupName: !Sub '/deployment-agent/${{ProjectSlug}}'
                  RetentionInDays: 14

              RuntimeSecret:
                Type: AWS::SecretsManager::Secret
                Properties:
                  Name: !Sub '${{ProjectSlug}}/runtime'
                  Description: Runtime configuration injected into the task

              # Pulls the image and resolves the task definition's `secrets`.
              # It is the EXECUTION role, not the task role, that reads them.
              TaskExecutionRole:
                Type: AWS::IAM::Role
                Properties:
                  AssumeRolePolicyDocument:
                    Version: '2012-10-17'
                    Statement:
                      - Effect: Allow
                        Principal: {{Service: ecs-tasks.amazonaws.com}}
                        Action: sts:AssumeRole
                  ManagedPolicyArns:
                    - arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
                  Policies:
                    - PolicyName: read-runtime-secret
                      PolicyDocument:
                        Version: '2012-10-17'
                        Statement:
                          - Effect: Allow
                            Action: [secretsmanager:GetSecretValue]
                            Resource: !Ref RuntimeSecret
              # The application's own identity. Deliberately empty: this is what
              # a compromised container gets.
              TaskRole:
                Type: AWS::IAM::Role
                Properties:
                  AssumeRolePolicyDocument:
                    Version: '2012-10-17'
                    Statement:
                      - Effect: Allow
                        Principal: {{Service: ecs-tasks.amazonaws.com}}
                        Action: sts:AssumeRole

              Cluster:
                Type: AWS::ECS::Cluster
                Properties:
                  ClusterName: !Ref ProjectSlug

              TaskDefinition:
                Type: AWS::ECS::TaskDefinition
                Properties:
                  Family: !Sub '${{ProjectSlug}}-task'
                  Cpu: '256'
                  Memory: '512'
                  NetworkMode: awsvpc
                  RequiresCompatibilities: [FARGATE]
                  ExecutionRoleArn: !GetAtt TaskExecutionRole.Arn
                  TaskRoleArn: !GetAtt TaskRole.Arn
                  ContainerDefinitions:
                    # A placeholder image so the stack can create before any build exists.
                    - Name: !Sub '${{ProjectSlug}}-app'
                      Image: public.ecr.aws/docker/library/busybox:latest
                      Essential: true
                      Command: ['sh', '-c', 'sleep 3600']
                      PortMappings: [{{ContainerPort: !Ref AppPort, Protocol: tcp}}]
                      LogConfiguration:
                        LogDriver: awslogs
                        Options:
                          awslogs-group: !Ref LogGroup
                          awslogs-region: !Ref 'AWS::Region'
                          awslogs-stream-prefix: app

              LoadBalancer:
                Type: AWS::ElasticLoadBalancingV2::LoadBalancer
                Properties:
                  # No explicit Name, deliberately.
                  Scheme: internet-facing
                  Type: application
                  SecurityGroups: [!Ref LoadBalancerSecurityGroup]
                  Subnets:
                    - !If [CreateNetwork, !Ref PublicSubnetA, !Ref ExistingSubnetA]
                    - !If [CreateNetwork, !Ref PublicSubnetB, !Ref ExistingSubnetB]
              TargetGroup:
                Type: AWS::ElasticLoadBalancingV2::TargetGroup
                Properties:
                  # Unnamed for the same reason as the load balancer above.
                  Port: !Ref AppPort
                  Protocol: HTTP
                  TargetType: ip
                  VpcId: !If [CreateNetwork, !Ref Vpc, !Ref ExistingVpcId]
                  HealthCheckPath: {health}
                  HealthCheckIntervalSeconds: 30
                  HealthyThresholdCount: 2
                  UnhealthyThresholdCount: 5
                  Matcher: {{HttpCode: '200-399'}}
                  TargetGroupAttributes:
                    - {{Key: deregistration_delay.timeout_seconds, Value: '15'}}
              Listener:
                Type: AWS::ElasticLoadBalancingV2::Listener
                Properties:
                  LoadBalancerArn: !Ref LoadBalancer
                  Port: 80
                  Protocol: HTTP
                  DefaultActions:
                    - Type: forward
                      TargetGroupArn: !Ref TargetGroup

              Service:
                Type: AWS::ECS::Service
                DependsOn: Listener
                Properties:
                  ServiceName: !Sub '${{ProjectSlug}}-service'
                  Cluster: !Ref Cluster
                  LaunchType: FARGATE
                  # Zero, and the workflow scales it to one.
                  DesiredCount: 0
                  TaskDefinition: !Ref TaskDefinition
                  # The workflow registers new revisions; CloudFormation must not
                  # drag the service back to the placeholder on the next update.
                  DeploymentController: {{Type: ECS}}
                  NetworkConfiguration:
                    AwsvpcConfiguration:
                      AssignPublicIp: ENABLED
                      SecurityGroups: [!Ref TaskSecurityGroup]
                      Subnets:
                        - !If [CreateNetwork, !Ref PublicSubnetA, !Ref ExistingSubnetA]
                        - !If [CreateNetwork, !Ref PublicSubnetB, !Ref ExistingSubnetB]
                  LoadBalancers:
                    - ContainerName: !Sub '${{ProjectSlug}}-app'
                      ContainerPort: !Ref AppPort
                      TargetGroupArn: !Ref TargetGroup

              GitHubOidcProvider:
                Type: AWS::IAM::OIDCProvider
                Condition: CreateOidcProvider
                Properties:
                  Url: https://token.actions.githubusercontent.com
                  ClientIdList: [sts.amazonaws.com]
                  ThumbprintList: [6938fd4d98bab03faadb97b34396831e3780aea1]

              GitHubDeployRole:
                Type: AWS::IAM::Role
                Properties:
                  RoleName: !Sub '${{ProjectSlug}}-github-deploy'
                  AssumeRolePolicyDocument:
                    Version: '2012-10-17'
                    Statement:
                      - Effect: Allow
                        Principal:
                          Federated: !If
                            - CreateOidcProvider
                            - !Ref GitHubOidcProvider
                            - !Ref ExistingOidcProviderArn
                        Action: sts:AssumeRoleWithWebIdentity
                        Condition:
                          StringEquals:
                            token.actions.githubusercontent.com:aud: sts.amazonaws.com
                            token.actions.githubusercontent.com:sub: !Ref GitHubSubjects
                  Policies:
                    - PolicyName: ecs-deploy
                      PolicyDocument:
                        Version: '2012-10-17'
                        Statement:
                          # Accepts no resource ARN.
                          - Effect: Allow
                            Action: [ecr:GetAuthorizationToken]
                            Resource: '*'
                          - Effect: Allow
                            Action:
                              - ecr:BatchCheckLayerAvailability
                              - ecr:InitiateLayerUpload
                              - ecr:UploadLayerPart
                              - ecr:CompleteLayerUpload
                              - ecr:PutImage
                              - ecr:BatchGetImage
                              - ecr:GetDownloadUrlForLayer
                              - ecr:DescribeImages
                            Resource: !GetAtt EcrRepository.Arn
                          - Effect: Allow
                            Action: [ecs:RegisterTaskDefinition, ecs:DescribeTaskDefinition]
                            Resource: '*'
                          - Effect: Allow
                            Action: [ecs:UpdateService, ecs:DescribeServices]
                            Resource: !Ref Service
                          - Effect: Allow
                            Action: [ecs:ListTasks, ecs:DescribeTasks]
                            Resource: '*'
                            Condition:
                              ArnEquals:
                                ecs:cluster: !GetAtt Cluster.Arn
                          # Scoped, and conditioned on the service it may be passed to.
                          - Effect: Allow
                            Action: [iam:PassRole]
                            Resource:
                              - !GetAtt TaskExecutionRole.Arn
                              - !GetAtt TaskRole.Arn
                            Condition:
                              StringEquals:
                                iam:PassedToService: ecs-tasks.amazonaws.com
                          - Effect: Allow
                            Action: [cloudformation:DescribeStacks]
                            Resource: !Sub 'arn:aws:cloudformation:${{AWS::Region}}:${{AWS::AccountId}}:stack/${{ProjectSlug}}-bootstrap/*'

            Outputs:
              ApplicationUrl:
                Value: !Sub 'http://${{LoadBalancer.DNSName}}'
              EcrRepositoryUri:
                Value: !GetAtt EcrRepository.RepositoryUri
              ClusterName:
                Value: !Ref Cluster
              ServiceName:
                Value: !GetAtt Service.Name
              TaskFamily:
                Value: !Sub '${{ProjectSlug}}-task'
              ContainerName:
                Value: !Sub '${{ProjectSlug}}-app'
              ExecutionRoleArn:
                Value: !GetAtt TaskExecutionRole.Arn
              TaskRoleArn:
                Value: !GetAtt TaskRole.Arn
              DeployRoleArn:
                Value: !GetAtt GitHubDeployRole.Arn
              RuntimeSecretArn:
                Value: !Ref RuntimeSecret
              LogGroupName:
                Value: !Ref LogGroup
              LoadBalancerDns:
                Value: !GetAtt LoadBalancer.DNSName
            """
        ).lstrip()
