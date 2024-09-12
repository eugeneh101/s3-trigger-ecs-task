import cdk_ecr_deployment as ecr_deploy  # to deploy container image to desired ECR repo
from aws_cdk import (
    RemovalPolicy,
    Stack,
    aws_ec2 as ec2,
    aws_ecr as ecr,
    aws_ecr_assets as ecr_assets,
    aws_ecs as ecs,
    aws_events as events,
    aws_events_targets as events_targets,
    aws_iam as iam,
    aws_logs as logs,
    aws_s3 as s3,
)
from constructs import Construct


class S3TriggerEcsTaskStack(Stack):
    def __init__(
        self, scope: Construct, construct_id: str, environment: dict, **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.role = iam.Role(
            self,
            "EcsTaskExecutionRole",
            role_name=environment["IAM_ROLE_NAME"],
            assumed_by=iam.CompositePrincipal(
                iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
                iam.ServicePrincipal("events.amazonaws.com"),
            ),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                ),  ### later principle of least privileges
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonEC2ContainerServiceEventsRole"
                ),  ### later principle of least privileges
            ],
        )

        self.vpc = ec2.Vpc(
            self,
            "VPC",
            vpc_name=environment["VPC_NAME"],
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public-Subnet",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="Private-Subnet",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24,
                ),
            ],
            availability_zones=[
                f"{environment['AWS_REGION']}{az}"
                for az in environment["AVAILABILITY_ZONES"]
            ],
            nat_gateways=len(environment["AVAILABILITY_ZONES"]),
        )

        self.s3_bucket = s3.Bucket(
            self,
            "S3Bucket",
            bucket_name=environment["S3_BUCKET_NAME"],
            event_bridge_enabled=True,  # hard coded
            versioned=False,  # hard coded
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        self.ecr_repo = ecr.Repository(
            self,
            "EcrRepo",
            repository_name=environment["ECR_REPO_NAME"],
            lifecycle_rules=[
                ecr.LifecycleRule(
                    max_image_count=1,  # hard coded
                    description="Delete old images that are not the latest",
                )
            ],
            removal_policy=RemovalPolicy.DESTROY,
            empty_on_delete=True,
        )

        self.event_rule = events.Rule(
            self,
            "EventRule",
            rule_name=environment["EVENTBRIDGE_RULE_NAME"],
            event_pattern=events.EventPattern(
                source=["aws.s3"],
                detail_type=["Object Created"],
                detail={
                    "bucket": {
                        "name": [self.s3_bucket.bucket_name],
                    },
                    "object": {"key": [{"prefix": "requests/"}]},  # hard coded
                },
            ),
        )

        # connecting AWS resources together
        task_asset = ecr_assets.DockerImageAsset(
            self, "EcrImage", directory="ecr/"
        )  # uploads to `container-assets` ECR repo
        deploy_repo = ecr_deploy.ECRDeployment(  # upload to desired ECR repo
            self,
            "PushTaskImage",
            src=ecr_deploy.DockerImageName(task_asset.image_uri),
            dest=ecr_deploy.DockerImageName(self.ecr_repo.repository_uri),
            # role=self.role,
        )
        task_image = ecs.ContainerImage.from_ecr_repository(repository=self.ecr_repo)
        task_log_group = logs.LogGroup(
            self,
            "TaskLogGroup",
            log_group_name=f"/ecs/{environment['ECS_TASK_DEFINITION_NAME']}",
            retention=logs.RetentionDays.THREE_MONTHS,  # hard coded
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.task_definition = ecs.TaskDefinition(
            self,
            "TaskDefinition",
            family=environment["ECS_TASK_DEFINITION_NAME"],
            compatibility=ecs.Compatibility.FARGATE,
            runtime_platform=ecs.RuntimePlatform(
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
                cpu_architecture=ecs.CpuArchitecture.X86_64,
            ),
            cpu="1024",  # 1 CPU
            memory_mib="8192",  # 8 GB RAM
            # ephemeral_storage_gib=None,
            # volumes=None,
            execution_role=self.role,
            task_role=self.role,
        )
        container = self.task_definition.add_container(
            "TaskDefinitionContainer",
            container_name=environment["ECS_TASK_DEFINITION_NAME"],
            image=task_image,
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="ecs",
                log_group=task_log_group,
                mode=ecs.AwsLogDriverMode.NON_BLOCKING,
            ),
            # environment={"AWS_REGION": environment["AWS_REGION"]},
        )
        # make sure repo created before task definition
        self.task_definition.node.add_dependency(self.ecr_repo)
        self.task_definition.node.add_dependency(deploy_repo)

        self.ecs_cluster = ecs.Cluster(
            self,
            "EcsCluster",
            cluster_name=environment["ECS_CLUSTER_NAME"],
            vpc=self.vpc,
        )

        self.event_rule.add_target(
            events_targets.EcsTask(
                cluster=self.ecs_cluster,
                task_definition=self.task_definition,
                security_groups=[],
                role=self.role,
                container_overrides=[
                    events_targets.ContainerOverride(
                        container_name=environment["ECS_TASK_DEFINITION_NAME"],
                        environment=[
                            {"name": "S3_BUCKET", "value": "$.detail.bucket.name"},
                            {"name": "S3_OBJECT_KEY", "value": "$.detail.object.key"},
                        ],
                    )
                ],
                retry_attempts=0,
            ),
        )
