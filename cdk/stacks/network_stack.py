"""The minimum viable network for this demo: one VPC, two public subnets
across two AZs (AgentCore Runtime needs at least two), an IGW, flow logs to
an encrypted CloudWatch log group. No NAT gateway - same call made in the
BackStageAccountVending sandbox network stack this mirrors: nothing private
to protect in a demo account, and a NAT gateway is a recurring cost with no
payoff here.
"""
from __future__ import annotations

from aws_cdk import RemovalPolicy, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_logs as logs
from constructs import Construct


class NetworkStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.vpc = ec2.Vpc(
            self,
            "Vpc",
            ip_addresses=ec2.IpAddresses.cidr("10.0.0.0/16"),
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
            ],
        )

        flow_log_group = logs.LogGroup(
            self,
            "FlowLogGroup",
            log_group_name="/vpc/memory-visa/flow",
            retention=logs.RetentionDays.ONE_YEAR,
            removal_policy=RemovalPolicy.DESTROY,
        )

        self.vpc.add_flow_log(
            "FlowLog",
            destination=ec2.FlowLogDestination.to_cloud_watch_logs(flow_log_group),
            traffic_type=ec2.FlowLogTrafficType.ALL,
            max_aggregation_interval=ec2.FlowLogMaxAggregationInterval.ONE_MINUTE,
        )
