"""The imbu stack as a scenario, for the real-stack tests."""


def scenario(*, issuer="imbu.issuer", stages='["issue", "deliver", "present", '
             '"verify", "revoke", "sync"]', rate="1.0", duration="3.0", faults="", homes=1,
             clients=1, count=2, signer="lockbox", latency='"n/a"', signer_rate='"n/a"',
             poll='50.0', wait='"n/a"', answer_rate="2.0", answer_burst=20,
             verifier="heti.verifier"):
    return f"""scenario_schema = 1
name = "imbu-stack"
stages = {stages}

[recognition]
poll_ms = 250.0
stable_polls = 2

[load]
rates = [{rate}]
duration_s = {duration}
warmup_s = 0.0
drain_s = 180.0
repetitions = 3
measure = ["requested", "verified"]

[roles.witness_pool]
adapter = "heti.witness_pool"
[roles.witness_pool.knobs]
knob_schema = 1
count = {count}

[roles.state_distribution]
adapter = "registrar.state_distribution"
[roles.state_distribution.knobs]
knob_schema = 1
window_s = 2.0
delivery_timeout_s = 1.0
resync_s = 6.0

[roles.issuer]
adapter = "{issuer}"
[roles.issuer.knobs]
knob_schema = 4
toad = 2
poll_ms = {poll}
wait_s = {wait}
answer_rate = {answer_rate}
answer_burst = {answer_burst}
operation_timeout_s = 60.0
homes = {homes}
clients = {clients}
signer = "{signer}"
signer_latency_ms = {latency}
signer_rate = {signer_rate}

[roles.holder]
adapter = "heti.holder"
[roles.holder.knobs]
knob_schema = 1
toad = 2

[roles.verifier]
adapter = "{verifier}"
[roles.verifier.knobs]
knob_schema = 1
toad = 2
freshness_s = 30.0
retry_ms = 250.0
deadline_s = 60.0
{faults}
"""
