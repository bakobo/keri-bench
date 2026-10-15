"""Small synthetic scenarios for tests of the run machinery (not the calibration itself)."""

DELAYS = {
    "issuer": ("constructed>registry_updated=3/0.2, registry_updated>anchored=3/0.2, "
               "anchored>witnessed=3/0.2, witnessed>published=3/0.2, "
               "revocation_requested>revocation_anchored=3/0.2, "
               "revocation_anchored>revocation_witnessed=3/0.2, "
               "revocation_witnessed>revocation_published=3/0.2"),
    "holder": "delivered>stored=3/0.2, stored>proof_generated=3/0.2",
    "verifier": "proof_generated>verified=3/0.2, sync_full_started>sync_full_done=5/0.2",
}


def roles(*names, issuer_extra=""):
    out = []
    for role in names:
        out.append(f'[roles.{role}]\nadapter = "synthetic.{role}"\n[roles.{role}.knobs]\n'
                   f"knob_schema = 1\n")
        if role == "witness_pool":
            continue
        out.append(f'seed = 3\nclock_skew_ms = 1500.0\ndelays = "{DELAYS.get(role, "")}"\n')
        if role == "issuer":
            out.append(issuer_extra or 'service_rate = 500.0\nstall_after = "n/a"\n'
                       'stall_ms = "n/a"\n')
    return "\n".join(out)


def scenario(*, stages='["issue", "deliver", "present", "verify", "revoke"]', rates="[10.0]",
             duration="1.0", faults="", repetitions=3, names=None, extra=""):
    names = names or ("witness_pool", "state_distribution", "issuer", "holder", "verifier")
    recognition = "[recognition]\npoll_ms = 5.0\nstable_polls = 2\n" if "revoke" in stages else ""
    return f"""scenario_schema = 1
name = "test"
stages = {stages}
{recognition}
[load]
rates = {rates}
duration_s = {duration}
warmup_s = 0.0
drain_s = 10.0
repetitions = {repetitions}
measure = ["requested", "constructed"]
{extra}
{roles(*names)}
{faults}
"""
