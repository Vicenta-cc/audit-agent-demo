"""One budget attempt with fixed fake transport in a guarded child interpreter."""
import argparse
from pathlib import Path
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('isolation_root', type=Path)
    args = parser.parse_args()
    root = args.isolation_root.resolve()
    temporary = Path(tempfile.gettempdir()).resolve()
    if root == temporary or not root.is_relative_to(temporary):
        raise ValueError('dedicated temporary replay directory required')

    # The full runner installs its guard through inherited sitecustomize. Direct
    # unit-test execution gets the same DB/network guard, scoped to this root.
    from experiments.geographic_execution_plan import offline_guards
    if offline_guards.ACTIVE is None:
        guard = offline_guards.OfflineGuards(experiment_root=root)
        guard.allowed_roots = (root,)
        offline_guards.ACTIVE = guard.install()

    from experiments.geographic_runtime_integration.model_gateway import ModelGateway
    gateway = ModelGateway(
        isolation_root=root, database=root / 'model_gateway_budget.sqlite3',
        upstream_base='https://model.example/v1',
        upstream_authorization='Bearer server-secret',
        gateway_token='local-test-token', limit=3,
        transport=lambda **request: (200, b'{}', 'application/json'),
    )
    print(gateway.forward(
        'POST', '/v1/chat/completions', b'{}',
        authorization='Bearer local-test-token',
    ).status)


if __name__ == '__main__':
    main()
