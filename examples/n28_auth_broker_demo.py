from autonomous_agent.auth_broker import CredentialBroker, CredentialRef


def main() -> int:
    reference = CredentialRef("demo-service", "worker", "primary", ("resource:read",))
    broker = CredentialBroker(lambda _: "DEMOSECRET")
    lease = broker.acquire(reference, granted_scopes=["resource:read"], lease_seconds=60)
    print("N28 Auth / Credential / Permission Broker demo")
    print(f"lease handle: {lease.secret_handle}")
    print(f"expired: {lease.expired}")
    try:
        broker.acquire(reference, granted_scopes=["resource:write"])
    except PermissionError as exc:
        print(f"denied: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
