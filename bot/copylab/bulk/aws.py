"""Run the bulk job on a temporary EC2 server in Tokyo, next to Hyperliquid's data (reads are free there).

  python -m bot.copylab.bulk.aws setup       # bucket + IAM role/profile + upload job code
  python -m bot.copylab.bulk.aws launch      # start the server (self-terminates after --hours)
  python -m bot.copylab.bulk.aws status      # instance state + last log lines
  python -m bot.copylab.bulk.aws wait KEY    # poll until s3://bucket/KEY exists (e.g. p1/_DONE)
  python -m bot.copylab.bulk.aws download    # pull outputs into data/hyperliquid/bulk/
  python -m bot.copylab.bulk.aws candidates FILE   # hand the pass-2 wallet list to the server
  python -m bot.copylab.bulk.aws teardown    # terminate server, empty+delete bucket, delete role

Every resource created is recorded in data/hyperliquid/bulk/aws_resources.json.
Credentials: the standard AWS chain; set AWS_PROFILE to pick a named profile. The whole run cost about $1.20.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import time
from pathlib import Path

import boto3

from ... import config

PROFILE, REGION = os.environ.get("AWS_PROFILE"), "ap-northeast-1"  # the bucket lives in Tokyo
HERE = Path(__file__).parent
STATE = config.DATA_DIR / "hyperliquid" / "bulk" / "aws_resources.json"
ROLE = "copylab-bulk-ec2"
TAG = {"Key": "project", "Value": "trader-copylab"}


def session():
    return boto3.Session(profile_name=PROFILE, region_name=REGION)


def load_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, indent=1))


def setup() -> None:
    s, st = session(), load_state()
    acct = s.client("sts").get_caller_identity()["Account"]
    if "bucket" not in st:
        st["bucket"] = f"copylab-hl-{acct[-4:]}-{secrets.token_hex(3)}"
        s.client("s3").create_bucket(Bucket=st["bucket"], CreateBucketConfiguration={"LocationConstraint": REGION})
        s.client("s3").put_bucket_tagging(Bucket=st["bucket"], Tagging={"TagSet": [TAG]})
        save_state(st)
        print("created bucket", st["bucket"])
    iam = s.client("iam")
    trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"}, "Action": "sts:AssumeRole"}]}
    policy = {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": ["s3:GetObject"], "Resource": "arn:aws:s3:::hl-mainnet-node-data/*"},
        {"Effect": "Allow", "Action": ["s3:ListBucket"], "Resource": "arn:aws:s3:::hl-mainnet-node-data"},
        {"Effect": "Allow", "Action": ["s3:*"], "Resource": [f"arn:aws:s3:::{st['bucket']}", f"arn:aws:s3:::{st['bucket']}/*"]},
    ]}
    if "role" not in st:
        iam.create_role(RoleName=ROLE, AssumeRolePolicyDocument=json.dumps(trust), Tags=[TAG],
                        Description="Temporary: lets the copylab bulk server read Hyperliquid data and write to its bucket")
        iam.create_instance_profile(InstanceProfileName=ROLE, Tags=[TAG])
        iam.add_role_to_instance_profile(InstanceProfileName=ROLE, RoleName=ROLE)
        st["role"] = st["instance_profile"] = ROLE
        save_state(st)
        print("created IAM role + instance profile", ROLE)
    iam.put_role_policy(RoleName=ROLE, PolicyName="copylab-s3", PolicyDocument=json.dumps(policy))
    s3 = s.client("s3")
    for name in ("job.py", "supported_coins.json"):
        s3.upload_file(str(HERE / name), st["bucket"], f"code/{name}")
    print("uploaded job code to", st["bucket"])


USER_DATA = """#!/bin/bash
shutdown -h +{minutes}
exec > /var/log/copylab.log 2>&1
set -x
B={bucket}
(while true; do aws s3 cp /var/log/copylab.log s3://$B/status/bootlog.txt --only-show-errors; sleep 60; done) &
dnf install -y python3.11 python3.11-pip
python3.11 -m pip install --quiet boto3 lz4 orjson pyarrow
mkdir -p /opt/job && cd /opt/job
aws s3 cp s3://$B/code/job.py job.py
aws s3 cp s3://$B/code/supported_coins.json supported_coins.json
python3.11 job.py --out-bucket $B --out-dir /opt/job/out --workers $(nproc) --wait-candidates-min {wait}
aws s3 cp /var/log/copylab.log s3://$B/status/bootlog.txt
shutdown -h now
"""


def launch(hours: float = 6.0, wait_min: int = 150) -> None:
    s, st = session(), load_state()
    ec2 = s.client("ec2")
    if st.get("instance_id"):
        state = ec2.describe_instances(InstanceIds=[st["instance_id"]])["Reservations"][0]["Instances"][0]["State"]["Name"]
        if state not in ("terminated", "shutting-down"):
            print("already running:", st["instance_id"], state)
            return
        st.setdefault("past_instances", []).append({"id": st.pop("instance_id"), "type": st.get("instance_type"),
                                                   "launched_utc": st.get("launched_utc")})
    quota = s.client("service-quotas").get_service_quota(ServiceCode="ec2", QuotaCode="L-1216C47A")["Quota"]["Value"]
    # Bigger server if the account's vCPU quota allows it; m7i has 16 GB (8 GB ran out of memory on busy days).
    itype = "m7i.4xlarge" if quota >= 16 else "m7i.xlarge"
    ami = s.client("ssm").get_parameter(Name="/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64")["Parameter"]["Value"]
    user_data = USER_DATA.format(minutes=int(hours * 60), bucket=st["bucket"], wait=wait_min)
    for attempt in range(10):  # a new instance profile takes a few seconds to become usable
        try:
            r = ec2.run_instances(
                ImageId=ami, InstanceType=itype, MinCount=1, MaxCount=1, UserData=user_data,
                IamInstanceProfile={"Name": st["instance_profile"]},
                InstanceInitiatedShutdownBehavior="terminate",
                BlockDeviceMappings=[{"DeviceName": "/dev/xvda", "Ebs": {"VolumeSize": 40, "VolumeType": "gp3", "DeleteOnTermination": True}}],
                TagSpecifications=[{"ResourceType": "instance", "Tags": [TAG, {"Key": "Name", "Value": "copylab-bulk"}]},
                                   {"ResourceType": "volume", "Tags": [TAG]}],
            )
            break
        except ec2.exceptions.ClientError as e:
            if "Invalid IAM Instance Profile" not in str(e) or attempt == 9:
                raise
            time.sleep(6)
    st.update({"instance_id": r["Instances"][0]["InstanceId"], "instance_type": itype,
               "launched_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()), "self_destruct_hours": hours})
    save_state(st)
    print(f"launched {st['instance_id']} ({itype}), self-terminates after {hours} h")


def status() -> None:
    s, st = session(), load_state()
    if st.get("instance_id"):
        try:
            inst = s.client("ec2").describe_instances(InstanceIds=[st["instance_id"]])["Reservations"][0]["Instances"][0]
            print("instance", st["instance_id"], inst["State"]["Name"], inst["InstanceType"], "launched", inst["LaunchTime"])
        except Exception as e:
            print("instance lookup:", e)
    s3 = s.client("s3")
    for key in ("status/log.txt", "status/bootlog.txt"):
        try:
            body = s3.get_object(Bucket=st["bucket"], Key=key)["Body"].read().decode(errors="replace").splitlines()
            print(f"--- {key} (last 6 lines)")
            print("\n".join(body[-6:]))
        except s3.exceptions.NoSuchKey:
            print(f"--- {key}: not yet")


def exists(key: str) -> bool:
    s3 = session().client("s3")
    try:
        s3.head_object(Bucket=load_state()["bucket"], Key=key)
        return True
    except s3.exceptions.ClientError:
        return False


def wait(key: str, timeout_min: int = 300, every: int = 60) -> None:
    deadline = time.time() + timeout_min * 60
    while time.time() < deadline:
        if exists(key):
            print("found", key)
            return
        if exists("status/FINISHED"):
            print("job finished before", key, "appeared")
            return
        time.sleep(every)
    print("timed out waiting for", key)


def download(prefix: str = "") -> None:
    s, st = session(), load_state()
    s3 = s.client("s3")
    out = config.DATA_DIR / "hyperliquid" / "bulk"
    n = 0
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=st["bucket"], Prefix=prefix):
        for o in page.get("Contents", []):
            if o["Key"].startswith("code/"):
                continue
            dest = out / o["Key"]
            if dest.exists() and dest.stat().st_size == o["Size"]:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(st["bucket"], o["Key"], str(dest))
            n += 1
    print(f"downloaded {n} new files into {out}")


def candidates(path: str) -> None:
    session().client("s3").upload_file(path, load_state()["bucket"], "candidates.json")
    print("uploaded candidates.json")


def teardown() -> None:
    s, st = session(), load_state()
    ids = [i for i in (st.get("instance_id"), st.get("helper_instance_id")) if i]
    if ids:
        ec2 = s.client("ec2")
        ec2.terminate_instances(InstanceIds=ids)
        ec2.get_waiter("instance_terminated").wait(InstanceIds=ids)
        print("terminated", ", ".join(ids))
    left = s.client("ec2").describe_instances(Filters=[{"Name": "tag:project", "Values": ["trader-copylab"]},
                                                       {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]}])
    still = [i["InstanceId"] for r in left["Reservations"] for i in r["Instances"]]
    print("copylab instances still alive:", still or "none")
    if st.get("bucket"):
        b = s.resource("s3").Bucket(st["bucket"])
        b.objects.all().delete()
        b.delete()
        print("deleted bucket", st["bucket"])
    if st.get("role"):
        iam = s.client("iam")
        iam.remove_role_from_instance_profile(InstanceProfileName=ROLE, RoleName=ROLE)
        iam.delete_instance_profile(InstanceProfileName=ROLE)
        iam.delete_role_policy(RoleName=ROLE, PolicyName="copylab-s3")
        iam.delete_role(RoleName=ROLE)
        print("deleted IAM role + profile", ROLE)
    st["torn_down_utc"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    save_state(st)


if __name__ == "__main__":
    cmd, *rest = sys.argv[1:] or ["status"]
    {"setup": setup, "launch": lambda: launch(), "status": status, "wait": lambda: wait(*rest),
     "download": lambda: download(*rest), "candidates": lambda: candidates(*rest), "teardown": teardown}[cmd]()
