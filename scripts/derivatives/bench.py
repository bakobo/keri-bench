"""What verifying each derivative format costs on this host (keri-bench this.i 3jeks5st, the
derivative child). How to run it is in README.md beside this file.

It runs in arf-interop's environment, not keri-bench's, because the two pin different hetis. It
mints with the same shape Fujie and Suzuki measured (arXiv:2611.03493): a credential of 20
attributes, presented with 5 disclosed, then times only the verifier, one call at a time, on the
host's monotonic clock, after a warmup. Trust anchors and keys are held in memory and nothing is
fetched, which is the papers' scope: the format's own checks with the network out of the path.

- sd-jwt-vc+kb: arf-interop's verify_eaa with require_binding, audience and nonce, on a two-
  certificate P-256 chain, so it checks the chain, the issuer signature, the five disclosure
  digests and the holder's key-binding JWT.
- sd-jwt-vc: the same credential presented without a key-binding JWT.
- w3c-vc-rdfc-20 and w3c-vc-rdfc-5: VCDM 2.0 with eddsa-rdfc-2022, canonicalized through
  arf-interop's own pinned-context loader (w3c._canonical), so the document and the proof options
  are each canonicalized and hashed, then the Ed25519 signature checked. arf-interop mints these
  and does not verify them, so this is the verification a Data Integrity verifier would have to
  do. eddsa-rdfc-2022 cannot derive a subset, so the 5-attribute row is the holder's re-signed
  subset, as in the papers, whose signature is then the holder's and not the issuer's.
- mdoc: mdoc-interop's mdoc-verify CLI on a DeviceResponse of one document disclosing 5 of 20
  elements under an IACA and a document signer minted here, without device authentication. Timed
  two ways: a process per verification, which is how the CLI is deployed and includes the JVM's
  start, and warm inside one JVM through MdocBench.java, which calls the CLI's own entry point.
"""

from __future__ import annotations

import argparse
import base64
import datetime
import hashlib
import json
import os
import platform
import secrets
import subprocess
import sys
import tempfile
import time
from importlib.metadata import version
from pathlib import Path

HERE = Path(__file__).resolve().parent
ATTRIBUTES = 20
DISCLOSED = 5
AUDIENCE, NONCE = "https://verifier.bench", "n-" + secrets.token_urlsafe(12)
NOW = datetime.datetime(2026, 11, 4, 12, 0, 0, tzinfo=datetime.UTC)


def timed(call, iterations: int, warmup: int) -> dict:
    for _ in range(warmup):
        call()
    samples = []
    for _ in range(iterations):
        start = time.monotonic_ns()
        call()
        samples.append(time.monotonic_ns() - start)
    return summary(samples)


def summary(samples: list[int]) -> dict:
    ordered = sorted(samples)

    def at(p):  # nearest rank, the definition keri-bench's own percentiles approximate
        return ordered[min(len(ordered) - 1, max(0, -(-p * len(ordered) // 100) - 1))]

    return {"n": len(ordered), "p50_ms": at(50) / 1e6, "p99_ms": at(99) / 1e6,
            "max_ms": ordered[-1] / 1e6, "mean_ms": sum(ordered) / len(ordered) / 1e6}


# --- certificates ---

def _name(text):
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, text),
                      x509.NameAttribute(NameOID.COUNTRY_NAME, "US")])


def certificate(*, key, name, issuer_key, issuer_name, ca, days, eku=None):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    start = NOW - datetime.timedelta(days=30)
    builder = (x509.CertificateBuilder().subject_name(_name(name)).issuer_name(_name(issuer_name))
               .public_key(key.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(start).not_valid_after(start + datetime.timedelta(days=days))
               .add_extension(x509.BasicConstraints(ca=ca, path_length=None),
                              critical=True)
               .add_extension(x509.KeyUsage(
                   digital_signature=not ca, content_commitment=False, key_encipherment=False,
                   data_encipherment=False, key_agreement=False, key_cert_sign=ca, crl_sign=ca,
                   encipher_only=False, decipher_only=False), critical=True))
    if eku:
        builder = builder.add_extension(
            x509.ExtendedKeyUsage([x509.ObjectIdentifier(eku)]), critical=True)
    return builder.sign(issuer_key, hashes.SHA256())


# --- SD-JWT VC ---

def sd_jwt(iterations, warmup) -> dict:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from joserfc.jwk import ECKey

    from arf_interop.certs import x5c_values, x5t_s256
    from arf_interop.disclosures import Disclosure
    from arf_interop.holder import present
    from arf_interop.mint import serialize, sign_sd_jwt
    from arf_interop.verify import verify_eaa

    def jose(private):
        return ECKey.import_key(private.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()))

    ca_key, leaf_key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(
        ec.SECP256R1())
    anchor = certificate(key=ca_key, name="bench CA", issuer_key=ca_key,
                         issuer_name="bench CA", ca=True, days=3650)
    leaf = certificate(key=leaf_key, name="bench signer", issuer_key=ca_key,
                       issuer_name="bench CA", ca=False, days=365)
    holder = ec.generate_private_key(ec.SECP256R1())
    holder_jwk = jose(holder)
    disclosures = tuple(Disclosure(salt=base64.urlsafe_b64encode(os.urandom(16)).decode()
                                   .rstrip("="), name=f"attribute_{i:02d}", value=f"value {i}")
                        for i in range(ATTRIBUTES))
    issued = NOW - datetime.timedelta(minutes=5)
    token = sign_sd_jwt(
        disclosures=disclosures,
        claims={"vct": "urn:bench:credential", "iss": "https://issuer.bench",
                "iat": int(issued.timestamp()), "exp": int(issued.timestamp()) + 3600,
                "cnf": {"jwk": holder_jwk.as_dict(private=False)}},
        key=jose(leaf_key), header={"x5c": x5c_values([leaf, anchor]),
                                    "x5t#S256": x5t_s256(leaf)})
    shown = tuple(d.name for d in disclosures[:DISCLOSED])
    bound = present(token=token, holder_key=holder_jwk, audience=AUDIENCE, nonce=NONCE,
                    issued_at=NOW, reveal=shown)
    bare = serialize(issuer_jwt=token.split("~")[0], disclosures=disclosures[:DISCLOSED])

    def verify_bound():
        verdict = verify_eaa(token=bound, anchors=[anchor], now=NOW, audience=AUDIENCE,
                             nonce=NONCE, require_binding=True)
        assert verdict.holder_bound and len(verdict.claims) >= DISCLOSED

    def verify_bare():
        verdict = verify_eaa(token=bare, anchors=[anchor], now=NOW)
        assert not verdict.holder_bound

    verify_bound(), verify_bare()
    return {"sd-jwt-vc+kb": {**timed(verify_bound, iterations, warmup), "bytes": len(bound)},
            "sd-jwt-vc": {**timed(verify_bare, iterations, warmup), "bytes": len(bare)}}


# --- W3C VCDM 2.0 + eddsa-rdfc-2022 ---

def w3c(iterations, warmup) -> dict:
    from cryptography.hazmat.primitives.asymmetric import ed25519

    from arf_interop import w3c as minted

    def credential(count, key_id):
        subject = {"id": "did:example:holder"}
        subject.update({f"attribute{i:02d}": f"value {i}" for i in range(count)})
        return {"@context": ["https://www.w3.org/ns/credentials/v2"],
                "type": ["VerifiableCredential"], "issuer": key_id.split("#")[0],
                "validFrom": "2026-11-01T00:00:00Z", "validUntil": "2027-11-01T00:00:00Z",
                "credentialSubject": subject}

    def sign(document, key, method):
        proof = {"type": "DataIntegrityProof", "cryptosuite": "eddsa-rdfc-2022",
                 "created": "2026-11-04T12:00:00Z", "verificationMethod": method,
                 "proofPurpose": "assertionMethod"}
        options = {"@context": document["@context"], **proof}
        data = (hashlib.sha256(minted._canonical(options)).digest()
                + hashlib.sha256(minted._canonical(document)).digest())
        return {**document, "proof": {**proof,
                                      "proofValue": minted._multibase(key.sign(data))}}

    rows = {}
    for label, count in (("w3c-vc-rdfc-20", ATTRIBUTES), ("w3c-vc-rdfc-5", DISCLOSED)):
        key = ed25519.Ed25519PrivateKey.generate()
        method = "did:example:issuer#key-1"
        keys = {method: key.public_key()}  # held, as an allowlisted verificationMethod would be
        vc = sign(credential(count, method), key, method)
        wire = json.dumps(vc).encode()

        def verify(wire=wire, keys=keys):
            document = json.loads(wire)
            proof = document.pop("proof")
            value = proof.pop("proofValue")
            public = keys[proof["verificationMethod"]]  # before any canonicalization
            options = {"@context": document["@context"], **proof}
            data = (hashlib.sha256(minted._canonical(options)).digest()
                    + hashlib.sha256(minted._canonical(document)).digest())
            public.verify(_unbase58(value), data)

        verify()
        rows[label] = {**timed(verify, iterations, warmup), "bytes": len(wire)}
        canonical = json.loads(wire)
        canonical.pop("proof")
        rows[label]["one_canonicalization"] = timed(lambda c=canonical: minted._canonical(c),
                                                    iterations, warmup)
    return rows


ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _unbase58(text: str) -> bytes:
    assert text.startswith("z")
    number = 0
    for char in text[1:]:
        number = number * 58 + ALPHABET.index(char)
    raw = number.to_bytes((number.bit_length() + 7) // 8, "big")
    return b"\0" * (len(text[1:]) - len(text[1:].lstrip("1"))) + raw


# --- mdoc ---

def mdoc_fixture(folder: Path) -> list[str]:
    """A DeviceResponse disclosing 5 of 20 elements, and the IACA it verifies under; the CLI's
    arguments."""
    import cbor2
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    iaca_key, ds_key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(
        ec.SECP256R1())
    iaca = certificate(key=iaca_key, name="bench IACA", issuer_key=iaca_key,
                       issuer_name="bench IACA", ca=True, days=3650)
    ds = certificate(key=ds_key, name="bench document signer", issuer_key=iaca_key,
                     issuer_name="bench IACA", ca=False, days=365, eku="1.0.18013.5.1.2")
    device = ec.generate_private_key(ec.SECP256R1()).public_key().public_numbers()
    doctype = namespace = "org.bakobo.bench.1"

    def tdate(moment):
        return cbor2.CBORTag(0, moment.strftime("%Y-%m-%dT%H:%M:%SZ"))

    items = [{"digestID": i, "random": os.urandom(16), "elementIdentifier": f"attribute_{i:02d}",
              "elementValue": f"value {i}"} for i in range(ATTRIBUTES)]
    tagged = [cbor2.CBORTag(24, cbor2.dumps(item)) for item in items]
    mso = {"version": "1.0", "digestAlgorithm": "SHA-256",
           "valueDigests": {namespace: {item["digestID"]: hashlib.sha256(cbor2.dumps(tag))
                                        .digest() for item, tag in zip(items, tagged)}},
           "deviceKeyInfo": {"deviceKey": {1: 2, -1: 1, -2: device.x.to_bytes(32, "big"),
                                           -3: device.y.to_bytes(32, "big")}},
           "docType": doctype,
           "validityInfo": {"signed": tdate(NOW - datetime.timedelta(days=1)),
                            "validFrom": tdate(NOW - datetime.timedelta(days=1)),
                            "validUntil": tdate(NOW + datetime.timedelta(days=30))}}
    payload = cbor2.dumps(cbor2.CBORTag(24, cbor2.dumps(mso)))
    protected = cbor2.dumps({1: -7})
    to_sign = cbor2.dumps(["Signature1", protected, b"", payload])
    from cryptography.hazmat.primitives import hashes
    r, s = decode_dss_signature(ds_key.sign(to_sign, ec.ECDSA(hashes.SHA256())))
    sign1 = [protected, {33: ds.public_bytes(serialization.Encoding.DER)}, payload,
             r.to_bytes(32, "big") + s.to_bytes(32, "big")]
    response = cbor2.dumps({"version": "1.0", "status": 0, "documents": [
        {"docType": doctype, "issuerSigned": {"nameSpaces": {namespace: tagged[:DISCLOSED]},
                                              "issuerAuth": sign1}}]})
    (folder / "response.cbor").write_bytes(response)
    (folder / "iaca.der").write_bytes(iaca.public_bytes(serialization.Encoding.DER))
    return ["--response", str(folder / "response.cbor"), "--anchor", str(folder / "iaca.der"),
            "--at", NOW.strftime("%Y-%m-%dT%H:%M:%SZ")]


def mdoc(install: Path, processes: int, iterations: int, warmup: int) -> dict:
    cli = install / "bin" / "mdoc-verify"
    with tempfile.TemporaryDirectory() as scratch:
        args = mdoc_fixture(Path(scratch))
        first = subprocess.run([str(cli), *args], capture_output=True, text=True)
        if first.returncode != 0:
            raise SystemExit(f"mdoc-verify refused the bench's mdoc ({first.returncode}):\n"
                             f"{first.stdout}{first.stderr}")
        record = json.loads(first.stdout)
        samples = []
        for _ in range(processes):
            start = time.monotonic_ns()
            subprocess.run([str(cli), *args], check=True, capture_output=True)
            samples.append(time.monotonic_ns() - start)
        warm = subprocess.run(
            ["java", "-cp", str(install / "lib" / "*"), str(HERE / "MdocBench.java"),
             str(iterations), str(warmup), *args], check=True, capture_output=True, text=True)
        size = (Path(scratch) / "response.cbor").stat().st_size
    return {"mdoc (process per verification)": {**summary(samples), "bytes": size},
            "mdoc (warm JVM)": {**summary(json.loads(warm.stdout)["samples_ns"]),
                                "bytes": size},
            "mdoc_checks": [(c["name"], c["outcome"]) for c in record["checks"]]}


def host() -> dict:
    model = next((line.split(":", 1)[1].strip() for line in
                  Path("/proc/cpuinfo").read_text().splitlines()
                  if line.startswith("model name")), platform.processor())
    return {"cpu": model, "cpu_count": os.cpu_count(), "kernel": platform.release(),
            "python": sys.version.split()[0], "loadavg": os.getloadavg(), "nice": os.nice(0)}


def commit(path: Path) -> str:
    """The commit a checkout is at, and whether its tree differs from it."""
    head = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True,
                          text=True, check=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(path), "status", "--porcelain", "--untracked-files=no"],
                           capture_output=True, text=True, check=True).stdout.strip()
    return head + ("+dirty" if dirty else "")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--mdoc-install", type=Path,
                        help="mdoc-interop's build/install/mdoc-verify; without it, no mdoc row")
    parser.add_argument("--iterations", type=int, default=2000)
    parser.add_argument("--warmup", type=int, default=200)
    parser.add_argument("--processes", type=int, default=30,
                        help="mdoc-verify processes to time, one verification each")
    args = parser.parse_args(argv)
    result = {"host": host(), "iterations": args.iterations, "warmup": args.warmup,
              "attributes": ATTRIBUTES, "disclosed": DISCLOSED,
              "versions": {name: version(name) for name in
                           ("arf-interop", "pyld", "joserfc", "cryptography", "cbor2")},
              "formats": {}}
    import arf_interop
    result["versions"]["arf-interop"] = commit(Path(arf_interop.__file__).parent)
    result["versions"]["keri-bench"] = commit(HERE)
    result["formats"].update(sd_jwt(args.iterations, args.warmup))
    result["formats"].update(w3c(args.iterations, args.warmup))
    if args.mdoc_install:
        rows = mdoc(args.mdoc_install, args.processes, args.iterations, args.warmup)
        result["mdoc_checks"] = rows.pop("mdoc_checks")
        result["formats"].update(rows)
        java = subprocess.run(["java", "-version"], capture_output=True, text=True)
        result["versions"]["java"] = java.stderr.splitlines()[0]
        result["versions"]["mdoc-interop"] = commit(args.mdoc_install)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
