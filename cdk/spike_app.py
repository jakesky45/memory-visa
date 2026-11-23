#!/usr/bin/env python3
"""The temporal policy experiment as its own CDK app, so it never builds or touches the
main stacks (and needs no Docker):

    cd cdk && npx cdk --app "python3 spike_app.py" deploy MemoryVisaDogwoodSpike
    cd cdk && npx cdk --app "python3 spike_app.py" destroy MemoryVisaDogwoodSpike
"""
import os

import aws_cdk as cdk

from stacks.dogwood_spike_stack import DogwoodSpikeStack

app = cdk.App()
DogwoodSpikeStack(
    app,
    "MemoryVisaDogwoodSpike",
    env=cdk.Environment(account=os.environ["CDK_DEFAULT_ACCOUNT"], region="eu-west-2"),
)
app.synth()
