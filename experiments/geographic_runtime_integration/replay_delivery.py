"""Read only the test delivery in a separate guarded Python interpreter."""
import json
import os
from pathlib import Path
import sys
import tempfile


def main():
    path=Path(sys.argv[1]).resolve()
    from experiments.geographic_execution_plan import offline_guards
    if offline_guards.ACTIVE is None:
        if not path.is_relative_to(Path(tempfile.gettempdir()).resolve()):
            raise ValueError('explicit temporary replay database required')
        guard=offline_guards.OfflineGuards(experiment_root=path.parent)
        guard.allowed_roots=(path.parent,)
        offline_guards.ACTIVE=guard.install()
    from audit_agent.ingestion import IngestionStore
    from audit_agent.geographic_delivery import delivery_basis_for_job
    store=IngestionStore(path)
    print(json.dumps({'refs':store.pending_for_task('job'),'basis':delivery_basis_for_job(store,'job')}))


if __name__ == '__main__':
    main()
