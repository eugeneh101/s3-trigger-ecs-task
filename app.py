import aws_cdk as cdk

from s3_trigger_ecs_task import S3TriggerEcsTaskStack


app = cdk.App()
environment = app.node.try_get_context("environment")
S3TriggerEcsTaskStack(
    app,
    "S3TriggerEcsTaskStack",
    env=cdk.Environment(region=environment["AWS_REGION"]),
    environment=environment,
)
app.synth()
