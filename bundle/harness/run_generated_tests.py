"""Actual pytest collection/call reports, separate from container invocation."""
import json
import os
from pathlib import Path
import sys

sys.path[:0]=["/candidate","/runtime","/harness/support","/harness/test_deps"]
os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"]="1"
import pytest


class Reports:
    def __init__(self):self.collected=0;self.calls=[];self.setup_errors=0;self.collection_errors=0
    def pytest_collection_finish(self,session):self.collected=len(session.items)
    def pytest_collectreport(self,report):self.collection_errors+=report.failed
    def pytest_runtest_logreport(self,report):
        if report.when=="call":self.calls.append({"nodeid":report.nodeid,"outcome":report.outcome})
        elif report.failed:self.setup_errors+=1


path=Path("/candidate/tests/test_regression.py");reports=Reports()
if path.exists():code=pytest.main([str(path),"-q","-p","no:cacheprovider"],plugins=[reports])
else:code=5
body_passed=sum(r["outcome"]=="passed" for r in reports.calls)
body_failed=sum(r["outcome"]=="failed" for r in reports.calls)
result={"collected":reports.collected,"test_bodies_run":body_passed+body_failed,"body_passed":body_passed,"body_failed":body_failed,
    "skipped_calls":sum(r["outcome"]=="skipped" for r in reports.calls),"setup_errors":reports.setup_errors,
    "collection_errors":reports.collection_errors,"exit_code":int(code),"reports":reports.calls}
print("\n__RESOURCE_TEST_REPORT__"+json.dumps(result))
raise SystemExit(code)
