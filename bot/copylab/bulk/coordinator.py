"""Keep every allowed server busy until all pass-2 days exist, then shut everything down.

Each server's job processes its own day list newest-first. Every minute this loop: counts finished days,
terminates servers whose whole list is finished, and fills any free vCPU quota (on-demand and spot are
separate 8-vCPU pools on this account) with new 4-vCPU servers that take the *oldest* unfinished days, the
ones the others will reach last. Ends by terminating every copylab server.

  python -m bot.copylab.bulk.coordinator
"""
from __future__ import annotations

import time

from . import aws

CHUNK = 40          # days per new server (~10 min of work)
VCPU = 4            # m7i.xlarge
QUOTA = {"on-demand": 8, "spot": 8}


def done_days(s3, bucket: str) -> set[str]:
    pages = s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix="p2/")
    return {o["Key"][3:-8] for p in pages for o in p.get("Contents", []) if o["Key"].endswith(".parquet")}


def alive(ec2) -> dict[str, str]:
    r = ec2.describe_instances(Filters=[{"Name": "tag:project", "Values": ["trader-copylab"]},
                                        {"Name": "instance-state-name", "Values": ["pending", "running"]}])
    return {i["InstanceId"]: ("spot" if i.get("InstanceLifecycle") == "spot" else "on-demand")
            for res in r["Reservations"] for i in res["Instances"]}


def launch_worker(s, st: dict, days: list[str], market: str) -> str:
    ud = aws.USER_DATA.format(minutes=120, bucket=st["bucket"], wait=10).replace(
        "--wait-candidates-min 10", "--wait-candidates-min 10 --days " + ",".join(days))
    ami = s.client("ssm").get_parameter(Name="/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64")["Parameter"]["Value"]
    kw = dict(ImageId=ami, InstanceType="m7i.xlarge", MinCount=1, MaxCount=1, UserData=ud,
              IamInstanceProfile={"Name": st["instance_profile"]}, InstanceInitiatedShutdownBehavior="terminate",
              BlockDeviceMappings=[{"DeviceName": "/dev/xvda", "Ebs": {"VolumeSize": 40, "VolumeType": "gp3", "DeleteOnTermination": True}}],
              TagSpecifications=[{"ResourceType": "instance", "Tags": [aws.TAG, {"Key": "Name", "Value": "copylab-bulk-worker"}]},
                                 {"ResourceType": "volume", "Tags": [aws.TAG]}])
    if market == "spot":
        kw["InstanceMarketOptions"] = {"MarketType": "spot", "SpotOptions": {"SpotInstanceType": "one-time", "InstanceInterruptionBehavior": "terminate"}}
    return s.client("ec2").run_instances(**kw)["Instances"][0]["InstanceId"]


def main(deadline_min: int = 120) -> None:
    s, st = aws.session(), aws.load_state()
    ec2, s3 = s.client("ec2"), s.client("s3")
    all_days = sorted(f.stem for f in (aws.config.DATA_DIR / "hyperliquid" / "bulk" / "p1").glob("*.parquet"))
    # Explicit day lists of the servers already running (the first server has none: it does everything newest-first).
    assigned: dict[str, list[str]] = {}
    if st.get("helper_instance_id"):
        assigned[st["helper_instance_id"]] = all_days[:200]
    for x in st.get("extra_instances", []):
        assigned[x["id"]] = [d for d in all_days if x["days"][0] <= d <= x["days"][1]]
    first = st.get("instance_id")
    first_share = [d for d in all_days if d >= "20260621"]
    stop_at = time.time() + deadline_min * 60
    while time.time() < stop_at:
        done, live = done_days(s3, st["bucket"]), alive(ec2)
        if len(done) >= len(all_days):
            break
        if first in live and set(first_share) <= done:
            ec2.terminate_instances(InstanceIds=[first])
            live.pop(first)
            print(time.strftime("%H:%M:%S"), "first server finished its share -> terminated", flush=True)
        for iid, days in list(assigned.items()):
            if iid in live and set(days) <= done:  # its list is done; its own job will exit too, but don't wait
                ec2.terminate_instances(InstanceIds=[iid])
                live.pop(iid)
                print(time.strftime("%H:%M:%S"), iid, "finished its list -> terminated", flush=True)
        # Days nobody new is working on yet: skip ones held by live servers launched by this loop.
        held = {d for iid, days in assigned.items() if iid in live and iid != st.get("helper_instance_id") for d in days}
        todo = [d for d in all_days if d not in done and d not in held]
        for market in ("on-demand", "spot"):
            free = (QUOTA[market] - VCPU * sum(1 for m in live.values() if m == market)) // VCPU
            for _ in range(free):
                chunk, todo = todo[:CHUNK], todo[CHUNK:]
                if not chunk:
                    break
                try:
                    iid = launch_worker(s, st, chunk, market)
                except Exception as e:  # e.g. no spot capacity right now: try again next minute
                    print(time.strftime("%H:%M:%S"), "launch failed:", str(e)[:120], flush=True)
                    todo = chunk + todo
                    break
                assigned[iid] = chunk
                live[iid] = market
                st.setdefault("extra_instances", []).append({"id": iid, "market": market, "days": [chunk[0], chunk[-1]]})
                aws.save_state(st)
                print(time.strftime("%H:%M:%S"), f"launched {iid} ({market}) for {chunk[0]}..{chunk[-1]}", flush=True)
        print(time.strftime("%H:%M:%S"), f"{len(done)}/{len(all_days)} days done, {len(live)} servers", flush=True)
        time.sleep(60)
    live = alive(ec2)
    if live:
        ec2.terminate_instances(InstanceIds=list(live))
    print(time.strftime("%H:%M:%S"), f"finished: {len(done_days(s3, st['bucket']))} days; terminated {list(live) or 'nothing'}", flush=True)


if __name__ == "__main__":
    main()
